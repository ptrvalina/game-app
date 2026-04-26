const express = require("express");
const { pool } = require("../db");

const router = express.Router();

router.post("/events", async (req, res, next) => {
  try {
    const { eventName, metadata, gameId, userId } = req.body || {};
    if (typeof eventName !== "string" || eventName.length < 2 || eventName.length > 100) {
      return res.status(400).json({ error: "eventName is invalid" });
    }
    await pool.query(
      `CREATE TABLE IF NOT EXISTS analytics_events (
        id BIGSERIAL PRIMARY KEY,
        event_name TEXT NOT NULL,
        game_id TEXT,
        user_id BIGINT,
        metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      );`
    );
    await pool.query("INSERT INTO analytics_events (event_name, game_id, user_id, metadata) VALUES ($1, $2, $3, $4)", [
      eventName,
      gameId || null,
      userId || null,
      metadata || {},
    ]);
    return res.status(201).json({ success: true });
  } catch (error) {
    return next(error);
  }
});

module.exports = { analyticsRoutes: router };
