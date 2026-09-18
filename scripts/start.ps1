$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
docker compose --env-file .env -f infrastructure/compose.yaml up --build -d
