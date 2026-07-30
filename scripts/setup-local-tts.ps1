# Sets up the self-hosted Chatterbox TTS server natively (no Docker).
#
# Native rather than Docker because the CUDA image needs ~20 GB to build and
# GPU passthrough on Windows besides; this needs ~8 GB and talks to the GPU
# directly. See docs/local-tts.md.
#
# Safe to re-run: every step checks before it acts.
#
#   powershell -ExecutionPolicy Bypass -File scripts/setup-local-tts.ps1
#
# ASCII only, and no Python passed inline via -c. Windows PowerShell 5.1
# mis-parses both, which broke an earlier version of this script.

#   ...add -NoDocker to skip the Docker steps entirely. The TTS server does
#   not need Docker; those steps exist only to reclaim disk space.

param([switch]$NoDocker)

$ErrorActionPreference = 'Stop'

$Source = "$HOME\OneDrive\Desktop\Chatterbox-TTS-Server"
$Target = 'C:\dev\Chatterbox-TTS-Server'
# pip needs room for the torch+CUDA wheels (~3 GB) plus its own unpack space,
# and the model lands on top of that.
$RequiredFreeGB = 15

function Get-FreeGB { [math]::Round((Get-PSDrive C).Free / 1GB, 2) }
function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }

function Test-DockerUp {
    $job = Start-Job { docker version --format '{{.Server.Version}}' }
    $ok = $false
    if (Wait-Job $job -Timeout 15) {
        $out = Receive-Job $job -ErrorAction SilentlyContinue
        if ($out) { $ok = $true }
    } else {
        Stop-Job $job -ErrorAction SilentlyContinue
    }
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    return $ok
}

Write-Host "Free space at start: $(Get-FreeGB) GB"

# --- 0. Unwedge and start Docker Desktop ------------------------------------
# Closing Docker while its daemon was hung leaves stale WSL processes holding
# the VM; Docker then hangs on startup. "wsl --shutdown" clears them. It stops
# all WSL distros but touches no containers, images, or volumes.
Step 0 'Starting Docker Desktop'
if ($NoDocker) {
    Write-Host '  -NoDocker given, skipping'
} elseif (Test-DockerUp) {
    Write-Host '  already running'
} else {
    $exe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $exe)) {
        Write-Host "  Docker Desktop not found, skipping the prune step." -ForegroundColor Yellow
    } else {
        Write-Host '  shutting down stale WSL processes'
        wsl --shutdown
        Start-Sleep -Seconds 5
        Write-Host '  launching Docker Desktop'
        Start-Process $exe
        Write-Host '  waiting for the daemon (up to 3 minutes)'
        $ready = $false
        foreach ($i in 1..18) {
            if (Test-DockerUp) { $ready = $true; break }
            Start-Sleep -Seconds 10
        }
        if ($ready) {
            Write-Host '  daemon is up'
        } else {
            Write-Host '  daemon did not come up; the prune step will be skipped.' -ForegroundColor Yellow
            Write-Host '  Docker is only needed to reclaim space and to run Postgres/Redis/MinIO.' -ForegroundColor Yellow
            Write-Host '  The TTS server itself does not need it at all.' -ForegroundColor Yellow
        }
    }
}

# --- 1. Reclaim the npm cache -----------------------------------------------
Step 1 'Clearing npm cache'
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm cache clean --force
    Write-Host "  free now: $(Get-FreeGB) GB"
} else {
    Write-Host '  npm not on PATH, skipping'
}

# --- 2. Reclaim Docker images -----------------------------------------------
# No --volumes: that would delete pgdata and miniodata, i.e. the run history
# and every audio file ScriptCast has produced.
Step 2 'Pruning Docker images and build cache'
if ($NoDocker) {
    Write-Host '  -NoDocker given, skipping'
} elseif (Test-DockerUp) {
    docker system prune -a -f
    Write-Host "  free now: $(Get-FreeGB) GB"
} else {
    Write-Host '  Docker unavailable, skipping (see step 0)' -ForegroundColor Yellow
}

# --- 3. Get the clone off OneDrive ------------------------------------------
# OneDrive would try to sync the multi-GB model cache the server downloads.
Step 3 'Moving the clone off OneDrive'
if (Test-Path $Target) {
    Write-Host "  already at $Target"
} elseif (Test-Path $Source) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Target) | Out-Null
    Move-Item $Source $Target
    Write-Host "  moved to $Target"
} else {
    throw "Cannot find the clone at $Source or $Target. Clone it first with: git clone https://github.com/devnen/Chatterbox-TTS-Server.git"
}

# --- 4. Check we have room before downloading anything ----------------------
Step 4 'Checking disk space'
$free = Get-FreeGB
if ($free -lt $RequiredFreeGB) {
    Write-Host "  Only $free GB free; need about $RequiredFreeGB GB." -ForegroundColor Red
    Write-Host '  Biggest remaining targets:' -ForegroundColor Yellow
    Write-Host '    AppData\Local\wsl      14.7 GB  - old WSL distros: wsl --list --verbose'
    Write-Host '    AppData\Local\Google    9.9 GB  - Chrome cache'
    Write-Host '    Downloads               3.5 GB'
    Write-Host '  Also: Settings > System > Storage > Cleanup recommendations'
    throw 'Not enough disk space. Freed what was safe; the rest is your call.'
}
Write-Host "  $free GB free, enough to continue"

# --- 5. Virtual environment -------------------------------------------------
Step 5 'Creating the virtual environment'
Set-Location $Target
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'python is not on PATH. Install Python 3.11 from python.org and re-run.'
}
$version = (& python -V) -join ''
Write-Host "  using $version"
if (Test-Path "$Target\.venv") {
    Write-Host '  .venv already exists'
} else {
    & python -m venv .venv
}
$venvPy = "$Target\.venv\Scripts\python.exe"

# --- 6. Dependencies --------------------------------------------------------
# cu128 wheels match the CUDA runtime the RTX 4050 wants. This is the slow
# step: roughly 3 GB of downloads.
Step 6 'Installing dependencies (several GB, expect 10-20 minutes)'
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements-nvidia-cu128.txt

# The engine package is deliberately left out of that requirements file: its
# own metadata pins an older torch, so a normal install silently downgrades
# the CUDA build we just fetched. --no-deps keeps ours. Its dependencies are
# already listed in the requirements file for exactly this reason.
#
# s3tokenizer and onnx ride along under the same --no-deps for a separate
# reason: onnx wants protobuf>=3.20.2 while descript-audiotools wants <3.20,
# and letting the resolver see both fails the install.
Step '6b' 'Installing the Chatterbox engine package'
& $venvPy -m pip install --no-deps `
    'git+https://github.com/devnen/chatterbox-v2.git@master' `
    's3tokenizer==0.3.0' `
    'onnx==1.16.0'

# --no-deps keeps the resolver quiet about the protobuf split, but it does not
# settle it: descript-audiotools pins protobuf<3.20 and wins the install, then
# onnx dies at import with "cannot import name 'builder'" because that arrived
# in 3.20. Nothing else here wants the old pin, and descript only uses protobuf
# for its TensorBoard training logger, which inference never calls. So 3.20.3 --
# the lowest version onnx accepts. pip prints a conflict warning; it is expected.
Step '6c' 'Pinning protobuf for onnx'
& $venvPy -m pip install 'protobuf==3.20.3'

# --- 7. Confirm the GPU is visible -----------------------------------------
# Kept in a real file rather than an inline -c string, because quoting Python
# through PowerShell 5.1 is where the last version of this script died.
Step 7 'Verifying CUDA'
$probe = Join-Path $env:TEMP 'cuda_probe.py'
@'
import torch
print(torch.cuda.is_available())
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0))
else:
    print("cpu only")
'@ | Set-Content -Path $probe -Encoding utf8
$cuda = & $venvPy $probe
Remove-Item $probe -ErrorAction SilentlyContinue
Write-Host "  $($cuda -join ' | ')"
if ($cuda[0] -ne 'True') {
    Write-Host '  CUDA not detected: the server will run on CPU, several times slower.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Done. Start the server with:' -ForegroundColor Green
Write-Host "  cd $Target"
Write-Host '  .\.venv\Scripts\python.exe server.py'
Write-Host ''
Write-Host 'It then serves http://localhost:8004 (first start downloads the model).'
Write-Host 'After that, back in scriptcast: set TTS_PROVIDER=local in .env'
