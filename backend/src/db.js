const { Pool } = require("pg");
const { config } = require("./config");

if (!config.postgresUrl) {
  throw new Error("DATABASE_URL is required");
}

const pool = new Pool({
  connectionString: config.postgresUrl,
  ssl: config.isProduction ? { rejectUnauthorized: false } : false,
});

async function initDb() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS games (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      genre TEXT NOT NULL,
      wave INTEGER NOT NULL,
      is_flagship BOOLEAN NOT NULL DEFAULT FALSE,
      config JSONB NOT NULL DEFAULT '{}'::jsonb,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS game_modes (
      id BIGSERIAL PRIMARY KEY,
      game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
      mode_key TEXT NOT NULL,
      title TEXT NOT NULL,
      config JSONB NOT NULL DEFAULT '{}'::jsonb,
      UNIQUE (game_id, mode_key)
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS scores (
      id BIGSERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL,
      username TEXT,
      score INTEGER NOT NULL CHECK (score >= 0 AND score <= 1000000),
      nonce TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (user_id, nonce)
    );
  `);

  await pool.query(`
    CREATE INDEX IF NOT EXISTS idx_scores_user_score_time
    ON scores(user_id, score DESC, created_at DESC);
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS game_scores (
      id BIGSERIAL PRIMARY KEY,
      game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
      user_id BIGINT NOT NULL,
      username TEXT,
      score INTEGER NOT NULL CHECK (score >= 0 AND score <= 1000000),
      nonce TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (game_id, user_id, nonce)
    );
  `);

  await pool.query(`
    CREATE INDEX IF NOT EXISTS idx_game_scores_game_score_time
    ON game_scores(game_id, score DESC, created_at DESC);
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS game_sessions (
      id BIGSERIAL PRIMARY KEY,
      game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
      user_id BIGINT NOT NULL,
      session_nonce TEXT NOT NULL,
      started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      finished_at TIMESTAMPTZ,
      telemetry JSONB NOT NULL DEFAULT '{}'::jsonb,
      UNIQUE (game_id, user_id, session_nonce)
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS daily_challenge_scores (
      id BIGSERIAL PRIMARY KEY,
      game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
      challenge_day DATE NOT NULL,
      user_id BIGINT NOT NULL,
      username TEXT,
      score INTEGER NOT NULL CHECK (score >= 0 AND score <= 1000000),
      nonce TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (game_id, challenge_day, user_id, nonce)
    );
  `);

  await pool.query(`
    CREATE INDEX IF NOT EXISTS idx_daily_challenge_scores
    ON daily_challenge_scores(game_id, challenge_day, score DESC, created_at DESC);
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS wallet_transactions (
      id BIGSERIAL PRIMARY KEY,
      game_id TEXT,
      user_id BIGINT NOT NULL,
      txn_type TEXT NOT NULL,
      delta INTEGER NOT NULL,
      metadata JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  await pool.query(`
    CREATE INDEX IF NOT EXISTS idx_wallet_transactions_user_time
    ON wallet_transactions(user_id, created_at DESC);
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS user_entitlements (
      id BIGSERIAL PRIMARY KEY,
      game_id TEXT,
      user_id BIGINT NOT NULL,
      sku TEXT NOT NULL,
      purchased_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (user_id, sku, game_id)
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS progression_daily_quests (
      id BIGSERIAL PRIMARY KEY,
      quest_key TEXT UNIQUE NOT NULL,
      title TEXT NOT NULL,
      reward_coins INTEGER NOT NULL DEFAULT 0,
      min_wave INTEGER NOT NULL DEFAULT 2
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS progression_user_quests (
      id BIGSERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL,
      quest_key TEXT NOT NULL,
      progress INTEGER NOT NULL DEFAULT 0,
      completed_at TIMESTAMPTZ,
      claimed_at TIMESTAMPTZ,
      UNIQUE (user_id, quest_key)
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS liveops_seasons (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      starts_at TIMESTAMPTZ NOT NULL,
      ends_at TIMESTAMPTZ NOT NULL,
      config JSONB NOT NULL DEFAULT '{}'::jsonb
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS ab_experiments (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      config JSONB NOT NULL DEFAULT '{}'::jsonb,
      is_active BOOLEAN NOT NULL DEFAULT TRUE
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS user_experiment_buckets (
      id BIGSERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL,
      experiment_id TEXT NOT NULL REFERENCES ab_experiments(id) ON DELETE CASCADE,
      bucket TEXT NOT NULL,
      assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (user_id, experiment_id)
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS recommendation_profiles (
      user_id BIGINT PRIMARY KEY,
      genre_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);

  await pool.query(`
    INSERT INTO progression_daily_quests (quest_key, title, reward_coins, min_wave)
    VALUES
      ('play_3_rounds', 'Play 3 rounds', 30, 2),
      ('win_1_flagship', 'Win one flagship match', 50, 2),
      ('score_500_total', 'Earn 500 total score', 60, 2)
    ON CONFLICT (quest_key) DO NOTHING;
  `);

  await pool.query(`
    INSERT INTO liveops_seasons (id, title, starts_at, ends_at, config)
    VALUES
      ('s1_global_launch', 'Global Launch Season', NOW(), NOW() + INTERVAL '30 days', '{"battlePass": true}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
  `);

  await pool.query(`
    INSERT INTO ab_experiments (id, title, config, is_active)
    VALUES
      ('onboarding_v1', 'Onboarding Reward Frequency', '{"variants":["A","B"],"weights":[0.5,0.5]}'::jsonb, TRUE)
    ON CONFLICT (id) DO NOTHING;
  `);
}

module.exports = { pool, initDb };
