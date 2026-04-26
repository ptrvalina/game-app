const { pool } = require("../db");

const gameSeed = [
  ["neon_snake", "NeonSnake", "arcade_reflex", 1, true],
  ["brick_breaker_x", "BrickBreakerX", "arcade_reflex", 2, false],
  ["astro_pong", "AstroPong", "arcade_reflex", 2, false],
  ["orbit_invaders", "OrbitInvaders", "arcade_reflex", 3, false],
  ["retro_runner", "RetroRunner", "arcade_reflex", 3, false],
  ["match3_nova", "Match3Nova", "puzzle_logic", 1, true],
  ["pipe_flow", "PipeFlow", "puzzle_logic", 2, false],
  ["hexa_merge", "HexaMerge", "puzzle_logic", 2, false],
  ["color_grid", "ColorGrid", "puzzle_logic", 3, false],
  ["tangram_rush", "TangramRush", "puzzle_logic", 4, false],
  ["ludo_world", "LudoWorld", "board_card", 1, true],
  ["chess_blitz", "ChessBlitz", "board_card", 2, false],
  ["checkers_duel", "CheckersDuel", "board_card", 2, false],
  ["solitaire_sprint", "SolitaireSprint", "board_card", 3, false],
  ["domino_arena", "DominoArena", "board_card", 3, false],
  ["word_forge", "WordForge", "word_quiz", 2, false],
  ["trivia_storm", "TriviaStorm", "word_quiz", 2, false],
  ["geo_guess_mini", "GeoGuessMini", "word_quiz", 3, false],
  ["emoji_riddle", "EmojiRiddle", "word_quiz", 3, false],
  ["rapid_facts", "RapidFacts", "word_quiz", 4, false],
  ["tower_lane", "TowerLane", "strategy_lite", 3, false],
  ["micro_td", "MicroTD", "strategy_lite", 3, false],
  ["kingdom_lines", "KingdomLines", "strategy_lite", 4, false],
  ["fleet_command_lite", "FleetCommandLite", "strategy_lite", 4, false],
  ["tactic_grid", "TacticGrid", "strategy_lite", 4, false],
  ["sling_master", "SlingMaster", "physics_skill", 2, false],
  ["rope_cut_lab", "RopeCutLab", "physics_skill", 3, false],
  ["stack_drop", "StackDrop", "physics_skill", 3, false],
  ["bottle_flip_pro", "BottleFlipPro", "physics_skill", 4, false],
  ["bounce_lab", "BounceLab", "physics_skill", 4, false],
  ["cafe_dash_idle", "CafeDashIdle", "idle_management", 3, false],
  ["mine_tycoon_mini", "MineTycoonMini", "idle_management", 3, false],
  ["factory_tap_flow", "FactoryTapFlow", "idle_management", 4, false],
  ["farm_merge_lite", "FarmMergeLite", "idle_management", 4, false],
  ["hotel_rush_idle", "HotelRushIdle", "idle_management", 4, false],
  ["shadow_ninja_run", "ShadowNinjaRun", "action_micro", 3, false],
  ["cyber_dash", "CyberDash", "action_micro", 3, false],
  ["arena_shooter_mini", "ArenaShooterMini", "action_micro", 4, false],
  ["beat_rush", "BeatRush", "action_micro", 4, false],
  ["sky_hook", "SkyHook", "action_micro", 4, false],
  ["draw_and_guess_live", "DrawAndGuessLive", "party_social", 4, false],
  ["bluff_cards", "BluffCards", "party_social", 4, false],
  ["quick_mafia", "QuickMafia", "party_social", 4, false],
  ["meme_battle", "MemeBattle", "party_social", 4, false],
  ["guess_the_sound", "GuessTheSound", "party_social", 4, false],
  ["snow_dash", "SnowDash", "seasonal_event", 4, false],
  ["pumpkin_defense", "PumpkinDefense", "seasonal_event", 4, false],
  ["lunar_quest", "LunarQuest", "seasonal_event", 4, false],
  ["summer_surf", "SummerSurf", "seasonal_event", 4, false],
  ["anniversary_arcade", "AnniversaryArcade", "seasonal_event", 4, false],
];

async function ensureGamesSeeded() {
  const upsert = `
    INSERT INTO games (id, title, genre, wave, is_flagship, config)
    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
    ON CONFLICT (id) DO UPDATE
    SET title = EXCLUDED.title,
        genre = EXCLUDED.genre,
        wave = EXCLUDED.wave,
        is_flagship = EXCLUDED.is_flagship;
  `;

  for (const [id, title, genre, wave, isFlagship] of gameSeed) {
    const config = {
      gameId: id,
      title,
      genre,
      sessionTargetSec: 90,
      modernMode: "reimagined",
    };
    // eslint-disable-next-line no-await-in-loop
    await pool.query(upsert, [id, title, genre, wave, isFlagship, JSON.stringify(config)]);
  }
}

async function getGames({ wave, flagshipOnly, genre }) {
  const clauses = [];
  const params = [];
  if (Number.isInteger(wave)) {
    params.push(wave);
    clauses.push(`wave <= $${params.length}`);
  }
  if (flagshipOnly) {
    clauses.push("is_flagship = TRUE");
  }
  if (genre) {
    params.push(genre);
    clauses.push(`genre = $${params.length}`);
  }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const result = await pool.query(
    `SELECT id, title, genre, wave, is_flagship AS "isFlagship", config
     FROM games
     ${where}
     ORDER BY wave ASC, title ASC`,
    params
  );
  return result.rows;
}

async function getGameById(gameId) {
  const result = await pool.query(
    `SELECT id, title, genre, wave, is_flagship AS "isFlagship", config
     FROM games WHERE id = $1`,
    [gameId]
  );
  return result.rows[0] || null;
}

module.exports = { ensureGamesSeeded, getGames, getGameById };
