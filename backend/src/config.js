const path = require("path");

const env = process.env.NODE_ENV || "development";
const port = Number(process.env.PORT || 3000);

const config = {
  env,
  port,
  isProduction: env === "production",
  botToken: process.env.BOT_TOKEN || "",
  webAppUrl: process.env.WEBAPP_URL || "http://localhost:3000",
  postgresUrl: process.env.DATABASE_URL || "",
  redisUrl: process.env.REDIS_URL || "",
  adminKey: process.env.ADMIN_API_KEY || "",
  rateLimitWindowMs: Number(process.env.RATE_LIMIT_WINDOW_MS || 60000),
  rateLimitMax: Number(process.env.RATE_LIMIT_MAX || 30),
  leaderboardPollIntervalMs: Number(process.env.LEADERBOARD_POLL_INTERVAL_MS || 3000),
  allowedOrigins: (process.env.ALLOWED_ORIGINS || "http://localhost:3000")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean),
  backupDir: process.env.BACKUP_DIR || path.join(process.cwd(), "backups"),
};

module.exports = { config };
