const test = require("node:test");
const assert = require("node:assert/strict");
const request = require("supertest");
const { createApp } = require("../src/app");
const { initDb, pool } = require("../src/db");

let app;
let dbReady = false;

test.before(async () => {
  try {
    await initDb();
    dbReady = true;
    app = createApp();
  } catch (error) {
    dbReady = false;
    console.warn("Skipping DB-backed tests:", error.message);
  }
});

test.after(async () => {
  if (dbReady) {
    await pool.end();
  }
});

test("GET /system/health returns ok", async () => {
  if (!dbReady) return test.skip("Database is not reachable in local environment");
  const res = await request(app).get("/system/health");
  assert.equal(res.status, 200);
  assert.equal(res.body.status, "ok");
});

test("GET /api/leaderboard returns data envelope", async () => {
  if (!dbReady) return test.skip("Database is not reachable in local environment");
  const res = await request(app).get("/api/leaderboard?limit=10&offset=0");
  assert.equal(res.status, 200);
  assert.ok(Array.isArray(res.body.data));
});

test("POST /api/admin/leaderboard/reset is forbidden without key", async () => {
  if (!dbReady) return test.skip("Database is not reachable in local environment");
  const res = await request(app).post("/api/admin/leaderboard/reset");
  assert.equal(res.status, 403);
});
