const express = require("express");
const { authMiddleware } = require("../middleware/authMiddleware");
const { validateScoreBody, validateLeaderboardQuery } = require("../validators/scoreValidator");
const { validateScoreAnomaly } = require("../services/antiCheatService");
const {
  submitScore,
  getLeaderboard,
  submitDailyChallengeScore,
  getDailyChallengeLeaderboard,
} = require("../services/leaderboardService");
const { incScoreSubmissions } = require("./systemRoutes");
const { getGameById } = require("../services/gameCatalogService");

const router = express.Router();

async function handleSubmitScore(gameId, req, res, next) {
  try {
    const { score, nonce } = req.body;
    const userId = req.authUser.telegramUserId;
    const game = await getGameById(gameId);
    if (!game) {
      return res.status(404).json({ error: "Game not found" });
    }
    validateScoreAnomaly({ userId, gameId, score, nonce });
    const accepted = await submitScore({
      gameId,
      userId,
      username: req.authUser.username,
      score,
      nonce,
    });
    incScoreSubmissions();
    return res.status(accepted ? 201 : 202).json({ success: true, accepted });
  } catch (error) {
    if (/duplicate|suspicious|too many|out of allowed|step/i.test(error.message)) {
      return res.status(409).json({ error: error.message });
    }
    return next(error);
  }
}

function getChallengeDay() {
  return new Date().toISOString().slice(0, 10);
}

router.post("/score", authMiddleware, validateScoreBody, async (req, res, next) => {
  const gameId = req.body.gameId || "neon_snake";
  return handleSubmitScore(gameId, req, res, next);
});

router.post("/games/:gameId/score", authMiddleware, validateScoreBody, async (req, res, next) => {
  return handleSubmitScore(String(req.params.gameId), req, res, next);
});

router.post("/games/:gameId/daily-score", authMiddleware, validateScoreBody, async (req, res, next) => {
  try {
    const gameId = String(req.params.gameId);
    const { score, nonce } = req.body;
    const userId = req.authUser.telegramUserId;
    const game = await getGameById(gameId);
    if (!game) return res.status(404).json({ error: "Game not found" });
    validateScoreAnomaly({ userId, gameId, score, nonce });
    const accepted = await submitDailyChallengeScore({
      gameId,
      userId,
      username: req.authUser.username,
      score,
      nonce,
      challengeDay: getChallengeDay(),
    });
    return res.status(accepted ? 201 : 202).json({ success: true, accepted, challengeDay: getChallengeDay() });
  } catch (error) {
    if (/duplicate|suspicious|too many|out of allowed|step/i.test(error.message)) {
      return res.status(409).json({ error: error.message });
    }
    return next(error);
  }
});

router.get("/leaderboard", validateLeaderboardQuery, async (req, res, next) => {
  try {
    const gameId = String(req.query.gameId || "neon_snake");
    const data = await getLeaderboard(gameId, req.pagination.limit, req.pagination.offset);
    return res.json({ gameId, data, page: req.pagination });
  } catch (error) {
    return next(error);
  }
});

router.get("/games/:gameId/leaderboard", validateLeaderboardQuery, async (req, res, next) => {
  try {
    const gameId = String(req.params.gameId);
    const data = await getLeaderboard(gameId, req.pagination.limit, req.pagination.offset);
    return res.json({ gameId, data, page: req.pagination });
  } catch (error) {
    return next(error);
  }
});

router.get("/games/:gameId/daily-leaderboard", validateLeaderboardQuery, async (req, res, next) => {
  try {
    const gameId = String(req.params.gameId);
    const challengeDay = String(req.query.day || getChallengeDay());
    const data = await getDailyChallengeLeaderboard(gameId, challengeDay, req.pagination.limit, req.pagination.offset);
    return res.json({ gameId, challengeDay, data, page: req.pagination });
  } catch (error) {
    return next(error);
  }
});

module.exports = { scoreRoutes: router };
