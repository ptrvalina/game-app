const express = require("express");
const { getGames, getGameById } = require("../services/gameCatalogService");

const router = express.Router();

function createConfigFor(gameId, title) {
  if (gameId === "neon_snake") {
    return {
      type: "reaction",
      title,
      objective: "Collect as many neon orbs as possible with speed bonus",
      rounds: 12,
      pointsPerHit: 15,
      modernFeatures: ["speed_bonus", "streak_multiplier", "global_competition"],
    };
  }
  if (gameId === "match3_nova") {
    return {
      type: "match",
      title,
      objective: "Build combo chains and trigger multiplier windows",
      rounds: 10,
      pointsPerHit: 20,
      modernFeatures: ["combo_chain", "flash_events", "highscore_chase"],
    };
  }
  if (gameId === "ludo_world") {
    return {
      type: "board",
      title,
      objective: "Fast dice duels with tactical risk and comeback turns",
      rounds: 8,
      pointsPerHit: 25,
      modernFeatures: ["dice_duel", "momentum_bonus", "social_leaderboard"],
    };
  }
  return { type: "arcade", title, objective: "Finish micro-session", rounds: 10, pointsPerHit: 10 };
}

router.get("/games", async (req, res, next) => {
  try {
    const wave = req.query.wave ? Number(req.query.wave) : undefined;
    const flagshipOnly = req.query.flagship === "1";
    const genre = req.query.genre ? String(req.query.genre) : undefined;
    const data = await getGames({ wave, flagshipOnly, genre });
    return res.json({ data });
  } catch (error) {
    return next(error);
  }
});

router.get("/games/:gameId/config", async (req, res, next) => {
  try {
    const game = await getGameById(req.params.gameId);
    if (!game) {
      return res.status(404).json({ error: "Game not found" });
    }
    return res.json({
      gameId: game.id,
      title: game.title,
      genre: game.genre,
      wave: game.wave,
      isFlagship: game.isFlagship,
      ...createConfigFor(game.id, game.title),
    });
  } catch (error) {
    return next(error);
  }
});

router.get("/game/1", async (req, res, next) => {
  try {
    const game = await getGameById("neon_snake");
    return res.json({
      gameId: game?.id || "neon_snake",
      type: "reaction",
      title: game?.title || "NeonSnake",
      rounds: 12,
      pointsPerHit: 15,
    });
  } catch (error) {
    return next(error);
  }
});

module.exports = { gameRoutes: router };
