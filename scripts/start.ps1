# Start the ScriptCast stack and wait until it actually answers.
#
# Safe to run when things are already up: every step is a no-op if the work is
# already done, so the warm path costs a few seconds instead of a rebuild.
#
#   .\scripts\start.ps1            # normal start
#   .\scripts\start.ps1 -Rebuild   # after changing pyproject.toml or package.json

[CmdletBinding()]
param(
    [switch]$Rebuild,
    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor DarkGray }

# BuildKit cannot read this repo's files: the project lives under OneDrive, so
# every file is a cloud placeholder (a reparse point) and the builder rejects it
# with "invalid file request Dockerfile". The legacy builder reads them fine.
$env:DOCKER_BUILDKIT = '0'
$env:COMPOSE_DOCKER_CLI_BUILD = '0'

# ---------------------------------------------------------------- docker daemon
Step 'Docker daemon'
docker info *>$null
if (-not $?) {
    $desktop = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { throw "Docker Desktop not found at $desktop" }
    Ok 'not running - starting Docker Desktop (this is the slow part, ~30-60s)'
    Start-Process $desktop

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        docker info *>$null
        if ($?) { break }
    }
    docker info *>$null
    if (-not $?) { throw "Docker daemon did not come up within $TimeoutSeconds s" }
}
Ok 'ready'

# ---------------------------------------------------------------------- images
# app/ and frontend/ are bind-mounted into the containers, so source edits need
# no rebuild. Only dependency manifests baked into the image do.
$images = @('scriptcast-api', 'scriptcast-worker', 'scriptcast-web')
$missing = @($images | Where-Object { docker image inspect $_ *>$null; -not $? })

if ($Rebuild -or $missing.Count -gt 0) {
    if ($missing.Count -gt 0) { Step "Building images (missing: $($missing -join ', '))" }
    else                      { Step 'Rebuilding images (-Rebuild)' }
    Ok 'first build takes several minutes; later ones hit the layer cache'
    docker compose build
    if (-not $?) { throw 'docker compose build failed' }
} else {
    Step 'Images'
    Ok 'present - skipping build (source is bind-mounted; use -Rebuild after dependency changes)'
}

# ------------------------------------------------------------------ containers
Step 'Containers'
docker compose up -d --no-build
if (-not $?) { throw 'docker compose up failed' }

# ------------------------------------------------------------------ migrations
# Cheap and idempotent - alembic no-ops when the schema is already at head.
Step 'Migrations'
docker compose exec -T api alembic upgrade head
if (-not $?) { throw 'alembic upgrade head failed' }

# ----------------------------------------------------------------------- ports
# WEB_PORT is configurable so a busy 3000 does not block the stack.
$webPort = '3000'
if (Test-Path .env) {
    $line = Select-String -Path .env -Pattern '^\s*WEB_PORT\s*=\s*(\S+)' | Select-Object -First 1
    if ($null -ne $line) { $webPort = $line.Matches[0].Groups[1].Value }
}

# ------------------------------------------------------------------- readiness
# The API answers before the web container has compiled its first page, so wait
# on both rather than declaring victory when the port opens.
Step 'Waiting for the app to answer'
$targets = [ordered]@{
    'api' = 'http://localhost:8000/docs'
    'web' = "http://localhost:$webPort"
}
foreach ($name in $targets.Keys) {
    $url = $targets[$name]
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $up = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
            if ($r.StatusCode -eq 200) { $up = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $up) {
        docker compose logs --tail 40 $name
        throw "$name did not answer at $url within $TimeoutSeconds s"
    }
    Ok "$name ok"
}

Write-Host ''
Write-Host 'ScriptCast is up.' -ForegroundColor Green
Write-Host "  UI:            http://localhost:$webPort"
Write-Host '  API docs:      http://localhost:8000/docs'
Write-Host '  MinIO console: http://localhost:9001  (minioadmin / minioadmin)'
