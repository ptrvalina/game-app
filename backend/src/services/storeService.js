const { pool } = require("../db");

const catalog = [
  { sku: "daily_boost", title: "Daily Boost", priceCoins: 50, type: "consumable", gameId: null },
  { sku: "remove_ads", title: "Remove Ads", priceCoins: 500, type: "permanent", gameId: null },
  { sku: "vip_badge", title: "VIP Badge", priceCoins: 1000, type: "permanent", gameId: null },
  { sku: "snake_skin_neon", title: "Snake Neon Skin", priceCoins: 350, type: "permanent", gameId: "neon_snake" },
  { sku: "match3_combo_pack", title: "Match3 Combo Pack", priceCoins: 280, type: "consumable", gameId: "match3_nova" },
  { sku: "ludo_dice_fx", title: "Ludo Dice FX", priceCoins: 400, type: "permanent", gameId: "ludo_world" },
];

function getCatalog(gameId) {
  if (!gameId) return catalog;
  return catalog.filter((item) => !item.gameId || item.gameId === gameId);
}

async function getBalance(userId) {
  const result = await pool.query(
    `
    SELECT COALESCE(SUM(delta), 0) AS balance
    FROM wallet_transactions
    WHERE user_id = $1;
  `,
    [userId]
  );
  return Number(result.rows[0]?.balance || 0);
}

async function getEntitlements(userId) {
  const result = await pool.query(
    `
    SELECT sku FROM user_entitlements
    WHERE user_id = $1
    ORDER BY purchased_at DESC;
  `,
    [userId]
  );
  return result.rows.map((row) => row.sku);
}

async function claimDailyReward(userId, gameId = null) {
  const check = await pool.query(
    `
      SELECT created_at
      FROM wallet_transactions
      WHERE user_id = $1 AND txn_type = 'daily_reward'
      ORDER BY created_at DESC
      LIMIT 1;
    `,
    [userId]
  );

  if (check.rowCount > 0) {
    const last = new Date(check.rows[0].created_at).getTime();
    if (Date.now() - last < 24 * 60 * 60 * 1000) {
      throw new Error("Daily reward already claimed");
    }
  }

  const reward = 100;
  await pool.query(
    `
      INSERT INTO wallet_transactions (game_id, user_id, txn_type, delta, metadata)
      VALUES ($1, $2, 'daily_reward', $3, '{}'::jsonb);
    `,
    [gameId, userId, reward]
  );

  return reward;
}

async function purchaseSku(userId, sku, gameId = null) {
  const item = catalog.find((entry) => entry.sku === sku && (!entry.gameId || entry.gameId === gameId));
  if (!item) {
    throw new Error("Unknown SKU");
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const balanceRes = await client.query(
      `SELECT COALESCE(SUM(delta), 0) AS balance FROM wallet_transactions WHERE user_id = $1`,
      [userId]
    );
    const balance = Number(balanceRes.rows[0]?.balance || 0);
    if (balance < item.priceCoins) {
      throw new Error("Insufficient balance");
    }

    if (item.type === "permanent") {
      const entRes = await client.query(
        `SELECT 1 FROM user_entitlements WHERE user_id = $1 AND sku = $2 AND COALESCE(game_id, '') = COALESCE($3, '')`,
        [userId, sku, item.gameId || null]
      );
      if (entRes.rowCount > 0) {
        throw new Error("Item already purchased");
      }
      await client.query(
        `INSERT INTO user_entitlements (game_id, user_id, sku) VALUES ($1, $2, $3)`,
        [item.gameId || null, userId, sku]
      );
    }

    await client.query(
      `
        INSERT INTO wallet_transactions (game_id, user_id, txn_type, delta, metadata)
        VALUES ($1, $2, 'purchase', $3, $4::jsonb);
      `,
      [item.gameId || null, userId, -item.priceCoins, JSON.stringify({ sku, gameId: item.gameId || null })]
    );
    await client.query("COMMIT");
    return item;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

module.exports = {
  getCatalog,
  getBalance,
  getEntitlements,
  claimDailyReward,
  purchaseSku,
};
