const express = require("express");
const { authMiddleware } = require("../middleware/authMiddleware");
const { pool } = require("../db");
const { getGames } = require("../services/gameCatalogService");

const router = express.Router();

router.get("/progression/quests", async (req, res, next) => {
  try {
    const result = await pool.query(
      "SELECT quest_key AS \"questKey\", title, reward_coins AS \"rewardCoins\", min_wave AS \"minWave\" FROM progression_daily_quests ORDER BY min_wave, quest_key"
    );
    return res.json({ data: result.rows });
  } catch (error) {
    return next(error);
  }
});

router.get("/liveops/seasons", async (req, res, next) => {
  try {
    const result = await pool.query(
      `SELECT id, title, starts_at AS "startsAt", ends_at AS "endsAt", config
       FROM liveops_seasons
       WHERE ends_at > NOW()
       ORDER BY starts_at ASC`
    );
    return res.json({ data: result.rows });
  } catch (error) {
    return next(error);
  }
});

router.get("/experiments/active", authMiddleware, async (req, res, next) => {
  try {
    const userId = req.authUser.telegramUserId;
    const experiments = await pool.query(`SELECT id, config FROM ab_experiments WHERE is_active = TRUE ORDER BY id`);
    const data = [];
    for (const exp of experiments.rows) {
      // eslint-disable-next-line no-await-in-loop
      let bucket = await pool.query(
        `SELECT bucket FROM user_experiment_buckets WHERE user_id = $1 AND experiment_id = $2`,
        [userId, exp.id]
      );
      if (bucket.rowCount === 0) {
        const choice = Math.random() < 0.5 ? "A" : "B";
        // eslint-disable-next-line no-await-in-loop
        await pool.query(
          `INSERT INTO user_experiment_buckets (user_id, experiment_id, bucket) VALUES ($1, $2, $3)
           ON CONFLICT (user_id, experiment_id) DO NOTHING`,
          [userId, exp.id, choice]
        );
        // eslint-disable-next-line no-await-in-loop
        bucket = await pool.query(
          `SELECT bucket FROM user_experiment_buckets WHERE user_id = $1 AND experiment_id = $2`,
          [userId, exp.id]
        );
      }
      data.push({ experimentId: exp.id, bucket: bucket.rows[0].bucket, config: exp.config });
    }
    return res.json({ data });
  } catch (error) {
    return next(error);
  }
});

router.get("/recommendations", authMiddleware, async (req, res, next) => {
  try {
    const userId = req.authUser.telegramUserId;
    const profileResult = await pool.query(
      `SELECT genre_scores AS "genreScores" FROM recommendation_profiles WHERE user_id = $1`,
      [userId]
    );
    const profile = profileResult.rows[0]?.genreScores || {};
    const games = await getGames({ wave: 4, flagshipOnly: false });
    const ranked = games
      .map((g) => ({ ...g, rank: Number(profile[g.genre] || 0) + (g.isFlagship ? 1 : 0) }))
      .sort((a, b) => b.rank - a.rank)
      .slice(0, 10);
    return res.json({ data: ranked });
  } catch (error) {
    return next(error);
  }
});

module.exports = { platformRoutes: router };
