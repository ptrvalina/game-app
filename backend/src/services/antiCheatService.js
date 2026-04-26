const userState = new Map();

function validateScoreAnomaly({ userId, gameId, score, nonce }) {
  const now = Date.now();
  const stateKey = `${userId}:${gameId || "global"}`;
  const state = userState.get(stateKey) || { lastAt: 0, windowStart: now, burst: 0, nonces: new Set() };

  if (state.nonces.has(nonce)) {
    throw new Error("Duplicate nonce detected");
  }

  if (now - state.windowStart > 60000) {
    state.windowStart = now;
    state.burst = 0;
    state.nonces.clear();
  }

  state.burst += 1;
  if (state.burst > 20) {
    throw new Error("Too many score submissions");
  }

  if (state.lastAt && now - state.lastAt < 500) {
    throw new Error("Submission frequency is suspicious");
  }

  if (score % 10 !== 0) {
    throw new Error("Invalid score step");
  }

  if (score > 1000000) {
    throw new Error("Score is out of allowed range");
  }

  state.lastAt = now;
  state.nonces.add(nonce);
  userState.set(stateKey, state);
}

module.exports = { validateScoreAnomaly };
