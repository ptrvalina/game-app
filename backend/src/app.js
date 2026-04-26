require("dotenv").config({ override: true });
const path = require("path");
const express = require("express");
const { applySecurity } = require("./middleware/securityMiddleware");
const { errorMiddleware } = require("./middleware/errorMiddleware");
const { gameRoutes } = require("./routes/gameRoutes");
const { scoreRoutes } = require("./routes/scoreRoutes");
const { adminRoutes } = require("./routes/adminRoutes");
const { analyticsRoutes } = require("./routes/analyticsRoutes");
const { storeRoutes } = require("./routes/storeRoutes");
const { platformRoutes } = require("./routes/platformRoutes");
const { systemRoutes } = require("./routes/systemRoutes");

function createApp() {
  const app = express();
  const frontendDir = path.join(__dirname, "../../frontend");

  applySecurity(app);
  app.use(express.json({ limit: "64kb" }));
  app.use(express.static(frontendDir));
  // Backward-compatible paths for previously configured WEBAPP_URL values.
  app.get("/frontend/index.html", (req, res) => res.sendFile(path.join(frontendDir, "index.html")));
  app.get("/frontend/app.js", (req, res) => res.sendFile(path.join(frontendDir, "app.js")));

  app.get("/", (req, res) => res.send("API running"));
  app.use("/api", gameRoutes);
  app.use("/api", scoreRoutes);
  app.use("/api/admin", adminRoutes);
  app.use("/api", analyticsRoutes);
  app.use("/api", storeRoutes);
  app.use("/api", platformRoutes);
  app.use("/system", systemRoutes);

  app.use(errorMiddleware);
  return app;
}

module.exports = { createApp };
