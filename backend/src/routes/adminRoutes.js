const express = require("express");
const { adminMiddleware } = require("../middleware/authMiddleware");
const { resetLeaderboard } = require("../services/leaderboardService");

const router = express.Router();

router.use(adminMiddleware);

router.post("/leaderboard/reset", async (req, res, next) => {
  try {
    const gameId = req.body?.gameId ? String(req.body.gameId) : null;
    await resetLeaderboard(gameId);
    return res.json({ success: true, gameId: gameId || "all" });
  } catch (error) {
    return next(error);
  }
});

module.exports = { adminRoutes: router };
