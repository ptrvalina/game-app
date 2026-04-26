require("dotenv").config({ override: true });
const { Telegraf } = require("telegraf");

if (!process.env.BOT_TOKEN) {
  throw new Error("BOT_TOKEN is required");
}
if (!process.env.WEBAPP_URL) {
  throw new Error("WEBAPP_URL is required");
}

const bot = new Telegraf(process.env.BOT_TOKEN);

bot.start(async (ctx) => {
  await ctx.reply("Global Game Hub is live. Choose a flagship game.", {
    reply_markup: {
      inline_keyboard: [
        [
          {
            text: "NeonSnake",
            web_app: { url: process.env.WEBAPP_URL },
          },
          {
            text: "Match3Nova",
            web_app: { url: process.env.WEBAPP_URL },
          },
        ],
        [
          {
            text: "LudoWorld",
            web_app: { url: process.env.WEBAPP_URL },
          },
        ],
      ],
    },
  });
});

bot.command("help", async (ctx) => {
  await ctx.reply("Use /start to launch the game.");
});

bot.launch();

const stop = (signal) => {
  bot.stop(signal);
};
process.once("SIGINT", () => stop("SIGINT"));
process.once("SIGTERM", () => stop("SIGTERM"));