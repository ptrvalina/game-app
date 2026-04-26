const express = require("express");
const { authMiddleware } = require("../middleware/authMiddleware");
const {
  getCatalog,
  getBalance,
  getEntitlements,
  claimDailyReward,
  purchaseSku,
} = require("../services/storeService");

const router = express.Router();

router.get("/store/catalog", (req, res) => {
  const gameId = req.query.gameId ? String(req.query.gameId) : undefined;
  res.json({ data: getCatalog(gameId) });
});

router.get("/store/balance", authMiddleware, async (req, res, next) => {
  try {
    const userId = req.authUser.telegramUserId;
    const [balance, entitlements] = await Promise.all([getBalance(userId), getEntitlements(userId)]);
    return res.json({ balance, entitlements });
  } catch (error) {
    return next(error);
  }
});

router.post("/store/claim-daily", authMiddleware, async (req, res, next) => {
  try {
    const userId = req.authUser.telegramUserId;
    const gameId = req.body?.gameId ? String(req.body.gameId) : null;
    const rewarded = await claimDailyReward(userId, gameId);
    const balance = await getBalance(userId);
    return res.status(201).json({ success: true, rewarded, balance });
  } catch (error) {
    if (/already claimed/i.test(error.message)) {
      return res.status(409).json({ error: error.message });
    }
    return next(error);
  }
});

router.post("/store/purchase", authMiddleware, async (req, res, next) => {
  try {
    const userId = req.authUser.telegramUserId;
    const sku = String(req.body?.sku || "");
    const gameId = req.body?.gameId ? String(req.body.gameId) : null;
    const item = await purchaseSku(userId, sku, gameId);
    const balance = await getBalance(userId);
    return res.status(201).json({ success: true, item, balance });
  } catch (error) {
    if (/insufficient|unknown sku|already purchased/i.test(error.message)) {
      return res.status(409).json({ error: error.message });
    }
    return next(error);
  }
});

module.exports = { storeRoutes: router };
