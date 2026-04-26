# Telegram Game Platform

Production-ready Telegram bot + WebApp game backend with secure score ingestion and PostgreSQL leaderboard.
Now includes a global multi-game catalog (50 reimagined titles) with wave-based rollout.

## Quick start

1. Copy `.env.example` to `.env` and fill values.
   - Set `WEBAPP_URL` to your public URL, e.g. `https://your-domain/index.html`.
   - Legacy `https://your-domain/frontend/index.html` is also supported for compatibility.
2. Start dependencies:
   - `docker compose up -d postgres redis`
3. Start API:
   - `npm install`
   - `npm start`
4. Start bot:
   - `npm run bot`

## Security baseline

- Telegram WebApp `initData` signature verification.
- HTTP hardening with `helmet`, CORS allowlist, and rate limiting.
- Input validation for score and pagination.
- Admin endpoints protected with `x-admin-key`.
- Duplicate nonce protection and anti-cheat checks.

## Monetization

- Coins wallet stored in PostgreSQL transaction ledger.
- `/api/store/catalog` for available items.
- `/api/store/claim-daily` for daily coin rewards.
- `/api/store/purchase` for SKU purchases and entitlement assignment.
- Game-aware SKUs for flagship titles (NeonSnake, Match3Nova, LudoWorld).

## Multi-game API

- `GET /api/games` for full catalog (supports `wave`, `flagship`, `genre` filters).
- `GET /api/games/:gameId/config` for game config.
- `POST /api/games/:gameId/score` for per-game score submit.
- `GET /api/games/:gameId/leaderboard` for per-game leaderboard.
- `GET /api/progression/quests` for wave 2 progression.
- `GET /api/liveops/seasons` for live-ops events.
- `GET /api/recommendations` for personalized game feed.

## Observability

- Liveness endpoint: `/system/health`
- Readiness endpoint: `/system/ready`
- Prometheus-style metrics: `/system/metrics`

## Release checks

- `npm run lint`
- `npm test`
- `npm run smoke`
- Follow `docs/pre-release-checklist.md`

## Backup and DR

See `docs/runbook.md` and `docs/dr.md`.
