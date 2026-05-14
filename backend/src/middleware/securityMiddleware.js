const cors = require("cors");
const helmet = require("helmet");
const rateLimit = require("express-rate-limit");
const { config } = require("../config");

function isNgrokDevOrigin(origin) {
  if (config.env === "production" || !origin) return false;
  try {
    const url = new URL(origin);
    const host = url.hostname || "";
    return (
      host.endsWith(".ngrok-free.app") ||
      host.endsWith(".ngrok.io") ||
      host.endsWith(".ngrok.app")
    );
  } catch {
    return false;
  }
}

function applySecurity(app) {
  app.use(helmet());
  app.use(
    cors({
      origin(origin, callback) {
        if (!origin || config.allowedOrigins.includes(origin)) {
          return callback(null, true);
        }
        if (isNgrokDevOrigin(origin)) {
          return callback(null, true);
        }
        return callback(new Error("Origin is not allowed"));
      },
    })
  );
  app.use(
    rateLimit({
      windowMs: config.rateLimitWindowMs,
      max: config.rateLimitMax,
      standardHeaders: true,
      legacyHeaders: false,
    })
  );
}

module.exports = { applySecurity };
