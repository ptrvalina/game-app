# Operations Runbook

## SLO targets

- API availability: 99.9% monthly.
- `/api/leaderboard` p95 latency: < 300ms.
- Score submission success ratio: > 99%.

## Alerts

- `/system/ready` fails for 2 minutes.
- Error rate > 2% for 5 minutes.
- Postgres disk usage > 80%.

## Incident response

1. Triage with `/system/health` and `/system/ready`.
2. Check app logs for `Unhandled error`.
3. Verify Postgres connectivity and free disk.
4. Roll back to previous image if regression started after deploy.

## Scaling strategy

- Run API as stateless containers behind a load balancer.
- Keep state in Postgres and optional Redis.
- Run bot as a separate process deployment.
