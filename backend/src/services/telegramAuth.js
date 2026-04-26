const crypto = require("crypto");
const { config } = require("../config");

function parseInitData(initDataRaw) {
  const params = new URLSearchParams(initDataRaw || "");
  const hash = params.get("hash");
  if (!hash) {
    throw new Error("hash is missing in initData");
  }

  const dataCheckArr = [];
  for (const [key, value] of params.entries()) {
    if (key !== "hash") {
      dataCheckArr.push(`${key}=${value}`);
    }
  }

  dataCheckArr.sort();
  return { hash, dataCheckString: dataCheckArr.join("\n"), params };
}

function verifyInitData(initDataRaw) {
  if (!config.botToken) {
    throw new Error("BOT_TOKEN is missing");
  }

  const { hash, dataCheckString, params } = parseInitData(initDataRaw);
  const secret = crypto.createHmac("sha256", "WebAppData").update(config.botToken).digest();
  const computedHash = crypto.createHmac("sha256", secret).update(dataCheckString).digest("hex");

  if (computedHash !== hash) {
    throw new Error("Invalid Telegram initData signature");
  }

  const authDate = Number(params.get("auth_date") || 0);
  const nowUnix = Math.floor(Date.now() / 1000);
  if (!authDate || nowUnix - authDate > 3600) {
    throw new Error("Expired Telegram initData");
  }

  const userRaw = params.get("user");
  if (!userRaw) {
    throw new Error("Telegram user is missing");
  }

  const user = JSON.parse(userRaw);
  if (!user.id) {
    throw new Error("Telegram user id is missing");
  }

  return {
    telegramUserId: Number(user.id),
    username: user.username || null,
    firstName: user.first_name || null,
    lastName: user.last_name || null,
  };
}

module.exports = { verifyInitData };
