const express = require("express");
const { pool } = require("../db");

const startTs = Date.now();
const metrics = {
  scoreSubmissions: 0,
};

const router = express.Router();

router.get("/health", (req, res) => {
  res.json({
    status: "ok",
    uptimeSec: Math.floor((Date.now() - startTs) / 1000),
  });
});

router.get("/ready", async (req, res) => {
  try {
    await pool.query("SELECT 1");
    return res.json({ status: "ready" });
  } catch (error) {
    return res.status(503).json({ status: "not_ready" });
  }
});

router.get("/metrics", (req, res) => {
  res.type("text/plain");
  res.send(
    [
      "# HELP app_uptime_seconds Process uptime",
      "# TYPE app_uptime_seconds gauge",
      `app_uptime_seconds ${Math.floor((Date.now() - startTs) / 1000)}`,
      "# HELP score_submissions_total Number of score submissions",
      "# TYPE score_submissions_total counter",
      `score_submissions_total ${metrics.scoreSubmissions}`,
    ].join("\n")
  );
});

function incScoreSubmissions() {
  metrics.scoreSubmissions += 1;
}

module.exports = { systemRoutes: router, incScoreSubmissions };
