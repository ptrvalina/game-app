require("dotenv").config({ override: true });
const { Telegraf } = require("telegraf");

if (!process.env.BOT_TOKEN) {
  throw new Error("BOT_TOKEN is required");
}
if (!process.env.WEBAPP_URL) {
  throw new Error("WEBAPP_URL is required");
}

const baseWebAppUrl = process.env.WEBAPP_URL.trim();
if (!/^https:\/\//i.test(baseWebAppUrl)) {
  throw new Error("WEBAPP_URL must be an https:// URL (use ngrok: ngrok http 3100, then copy the https URL + /index.html)");
}
const placeholderLike =
  /YOUR_SUBDOMAIN|YOUR_TUNNEL|YOUR_REAL|PASTE_|abcd-12-34|твой-ngrok/i.test(
    baseWebAppUrl
  ) || baseWebAppUrl.includes("</");
if (placeholderLike) {
  throw new Error(
    "WEBAPP_URL is still a placeholder. Run: ngrok http 3100 — copy the https Forwarding host (not abcd-12-34 example). Set WEBAPP_URL=https://<that-host>/index.html and ensure npm start listens on PORT=3100."
  );
}

/** Hub page: root `/` may not serve the SPA; force /index.html when path is empty or `/`. */
function normalizeHubUrl(base) {
  const u = new URL(base.trim(), "https://example.com");
  if (!u.pathname || u.pathname === "/") {
    u.pathname = "/index.html";
  }
  return u.toString();
}

function gameUrl(gameId) {
  const u = new URL(normalizeHubUrl(baseWebAppUrl));
  u.searchParams.set("game", gameId);
  return u.toString();
}

const bot = new Telegraf(process.env.BOT_TOKEN);

async function syncDefaultMenuButton() {
  const url = normalizeHubUrl(baseWebAppUrl);
  try {
    await bot.telegram.setChatMenuButton({
      menu_button: {
        type: "web_app",
        text: "Play",
        web_app: { url },
      },
    });
    console.info("Menu button URL synced to:", url);
  } catch (err) {
    console.warn(
      "setChatMenuButton failed — open @BotFather → your bot → Menu Button and set the same URL as WEBAPP_URL:",
      err.message
    );
  }
}

bot.start(async (ctx) => {
  try {
    await ctx.reply("Global Game Hub is live. Choose a flagship game.", {
      reply_markup: {
        inline_keyboard: [
          [
            { text: "NeonSnake", web_app: { url: gameUrl("neon_snake") } },
            { text: "Match3Nova", web_app: { url: gameUrl("match3_nova") } },
          ],
          [{ text: "LudoWorld", web_app: { url: gameUrl("ludo_world") } }],
        ],
      },
    });
  } catch (err) {
    console.error("start handler failed", err);
    await ctx.reply("Bot could not show WebApp buttons. Check WEBAPP_URL in .env (valid https ngrok URL) and that the API is running on PORT=3100.");
  }
});

bot.command("help", async (ctx) => {
  await ctx.reply("Use /start to launch the game.");
});

(async () => {
  await syncDefaultMenuButton();
  await bot.launch();
  console.info("Bot polling — inline buttons and menu use WEBAPP_URL →", normalizeHubUrl(baseWebAppUrl));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});

const stop = (signal) => {
  bot.stop(signal);
};
process.once("SIGINT", () => stop("SIGINT"));
process.once("SIGTERM", () => stop("SIGTERM"));