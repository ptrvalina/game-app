function validateScoreBody(req, res, next) {
  const { score, nonce, gameId } = req.body || {};

  if (!Number.isInteger(score) || score < 0 || score > 1000000) {
    return res.status(400).json({ error: "score must be an integer between 0 and 1000000" });
  }

  if (typeof nonce !== "string" || nonce.length < 8 || nonce.length > 100) {
    return res.status(400).json({ error: "nonce must be a string between 8 and 100 chars" });
  }
  if (gameId !== undefined && (typeof gameId !== "string" || gameId.length < 3 || gameId.length > 80)) {
    return res.status(400).json({ error: "gameId must be a string between 3 and 80 chars" });
  }

  return next();
}

function validateLeaderboardQuery(req, res, next) {
  const limit = Number(req.query.limit || 10);
  const offset = Number(req.query.offset || 0);
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    return res.status(400).json({ error: "limit must be integer between 1 and 100" });
  }
  if (!Number.isInteger(offset) || offset < 0 || offset > 10000) {
    return res.status(400).json({ error: "offset must be integer between 0 and 10000" });
  }
  req.pagination = { limit, offset };
  return next();
}

module.exports = { validateScoreBody, validateLeaderboardQuery };
