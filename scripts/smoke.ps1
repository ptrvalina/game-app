$ErrorActionPreference = "Stop"

$baseUrl = if ($env:SMOKE_BASE_URL) { $env:SMOKE_BASE_URL } else { "http://localhost:3000" }

Write-Host "Checking health..."
$health = Invoke-RestMethod -Uri "$baseUrl/system/health" -Method Get
if ($health.status -ne "ok") { throw "Health check failed" }

Write-Host "Checking readiness..."
Invoke-RestMethod -Uri "$baseUrl/system/ready" -Method Get | Out-Null

Write-Host "Checking leaderboard..."
$lb = Invoke-RestMethod -Uri "$baseUrl/api/leaderboard?limit=5&offset=0" -Method Get
if ($null -eq $lb.data) { throw "Leaderboard payload is invalid" }

Write-Host "Smoke checks passed"
