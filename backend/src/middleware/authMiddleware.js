const { verifyInitData } = require("../services/telegramAuth");

function authMiddleware(req, res, next) {
  try {
    const headerInitData = req.headers.authorization?.replace(/^tma\s+/i, "");
    const initData = headerInitData || req.body.initData;
    if (!initData) {
      return res.status(401).json({ error: "Missing Telegram initData" });
    }

    req.authUser = verifyInitData(initData);
    return next();
  } catch (error) {
    return res.status(401).json({ error: error.message });
  }
}

function adminMiddleware(req, res, next) {
  const apiKey = req.headers["x-admin-key"];
  if (!apiKey || apiKey !== process.env.ADMIN_API_KEY) {
    return res.status(403).json({ error: "Forbidden" });
  }
  return next();
}

module.exports = { authMiddleware, adminMiddleware };
