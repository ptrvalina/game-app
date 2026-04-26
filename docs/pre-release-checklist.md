# Pre-release Checklist

## Security

- Rotate `BOT_TOKEN` and verify it is stored only in secure environment variables.
- Ensure `.env` is not committed and production secrets are injected by platform.
- Confirm `ALLOWED_ORIGINS` contains only trusted domains.
- Verify `ADMIN_API_KEY` is long, random, and stored securely.

## Functional smoke

- `GET /system/health` returns `ok`.
- `GET /system/ready` returns `ready`.
- `GET /api/store/catalog` returns expected SKUs.
- Submit a score from real Telegram WebApp session and confirm leaderboard update.
- Claim daily reward and confirm wallet balance changes.

## Operations

- Confirm backup job runs (`npm run backup`) and file is created.
- Confirm DR restore procedure from `docs/dr.md` on a clean database.
- Confirm logs and metrics collection (`/system/metrics`) are available.
