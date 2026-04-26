const { pool } = require("../db");

async function submitScore({ gameId, userId, username, score, nonce }) {
  const query = `
    INSERT INTO game_scores (game_id, user_id, username, score, nonce)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (game_id, user_id, nonce) DO NOTHING
    RETURNING id;
  `;
  const values = [gameId, userId, username, score, nonce];
  const result = await pool.query(query, values);
  return result.rowCount > 0;
}

async function getLeaderboard(gameId, limit = 10, offset = 0) {
  const query = `
    SELECT
      user_id AS "userId",
      MAX(COALESCE(NULLIF(username, ''), 'player_' || user_id::text)) AS "username",
      MAX(score) AS "bestScore",
      MAX(created_at) AS "lastPlayedAt"
    FROM game_scores
    WHERE game_id = $1
    GROUP BY user_id
    ORDER BY "bestScore" DESC, "lastPlayedAt" DESC
    LIMIT $2 OFFSET $3;
  `;
  const values = [gameId, limit, offset];
  const result = await pool.query(query, values);
  return result.rows;
}

async function resetLeaderboard(gameId) {
  if (gameId) {
    await pool.query("DELETE FROM game_scores WHERE game_id = $1;", [gameId]);
    return;
  }
  await pool.query("TRUNCATE TABLE game_scores;");
}

async function submitDailyChallengeScore({ gameId, userId, username, score, nonce, challengeDay }) {
  const query = `
    INSERT INTO daily_challenge_scores (game_id, challenge_day, user_id, username, score, nonce)
    VALUES ($1, $2::date, $3, $4, $5, $6)
    ON CONFLICT (game_id, challenge_day, user_id, nonce) DO NOTHING
    RETURNING id;
  `;
  const values = [gameId, challengeDay, userId, username, score, nonce];
  const result = await pool.query(query, values);
  return result.rowCount > 0;
}

async function getDailyChallengeLeaderboard(gameId, challengeDay, limit = 10, offset = 0) {
  const query = `
    SELECT
      user_id AS "userId",
      MAX(COALESCE(NULLIF(username, ''), 'player_' || user_id::text)) AS "username",
      MAX(score) AS "bestScore",
      MAX(created_at) AS "lastPlayedAt"
    FROM daily_challenge_scores
    WHERE game_id = $1 AND challenge_day = $2::date
    GROUP BY user_id
    ORDER BY "bestScore" DESC, "lastPlayedAt" DESC
    LIMIT $3 OFFSET $4;
  `;
  const values = [gameId, challengeDay, limit, offset];
  const result = await pool.query(query, values);
  return result.rows;
}

module.exports = {
  submitScore,
  getLeaderboard,
  resetLeaderboard,
  submitDailyChallengeScore,
  getDailyChallengeLeaderboard,
};
