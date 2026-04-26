# Backup and Disaster Recovery

## Backup policy

- Daily logical backup via `pg_dump`.
- Keep 14 daily backups and 3 weekly backups.
- Store backups in encrypted remote object storage.

## Backup command

```bash
pg_dump "$DATABASE_URL" > "$BACKUP_DIR/telegram_game_$(date +%F).sql"
```

## Restore drill

1. Provision clean Postgres instance.
2. Restore latest backup:
   - `psql "$DATABASE_URL" < backup.sql`
3. Run smoke tests:
   - `/system/ready`
   - `/api/leaderboard`
4. Record restore time objective (RTO) and issues.

## DR objectives

- RPO: 24h.
- RTO: 60 minutes.
