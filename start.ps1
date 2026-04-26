Write-Host "Starting backend..."

Start-Process powershell -ArgumentList "node backend/server.js"

Start-Sleep -Seconds 2

Write-Host "Starting bot..."

Start-Process powershell -ArgumentList "node bot/bot.js"

Write-Host "All services started"