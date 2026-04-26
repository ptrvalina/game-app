require("dotenv").config({ override: true });
const { createApp } = require("./app");
const { config } = require("./config");
const { initDb, pool } = require("./db");
const { ensureGamesSeeded } = require("./services/gameCatalogService");

async function start() {
  await initDb();
  await ensureGamesSeeded();
  const app = createApp();
  const server = app.listen(config.port, () => {
    console.log(`Server running on ${config.port}`);
  });

  const graceful = async () => {
    server.close(async () => {
      await pool.end();
      process.exit(0);
    });
  };

  process.on("SIGINT", graceful);
  process.on("SIGTERM", graceful);
}

start().catch((error) => {
  console.error("Server failed to start", error);
  process.exit(1);
});
