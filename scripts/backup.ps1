$ErrorActionPreference = "Stop"

if (-not $env:DATABASE_URL) {
  throw "DATABASE_URL is required"
}

$backupDir = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { ".\\backups" }
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

$stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$file = Join-Path $backupDir "telegram_game_$stamp.sql"

if (Get-Command pg_dump -ErrorAction SilentlyContinue) {
  pg_dump $env:DATABASE_URL | Out-File -FilePath $file -Encoding utf8
} else {
  # Fallback for Windows environments without local postgres client binaries.
  docker exec project-postgres-1 sh -lc "PGPASSWORD=postgres pg_dump -U postgres -d telegram_game" | Out-File -FilePath $file -Encoding utf8
}

if (-not (Test-Path $file)) {
  throw "Backup file was not created"
}

Write-Host "Backup created: $file"
