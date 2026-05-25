Param(
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found. Install with:  winget install astral-sh.uv" -ForegroundColor Yellow
    exit 1
}

$venvPath = Join-Path $PSScriptRoot "..\.venv"
if ($Recreate -and (Test-Path $venvPath)) {
    Remove-Item -Recurse -Force $venvPath
}

Push-Location (Join-Path $PSScriptRoot "..")
try {
    if (-not (Test-Path ".venv")) {
        uv venv --python 3.12
        if ($LASTEXITCODE -ne 0) { throw "uv venv failed (exit $LASTEXITCODE)" }
    }
    uv pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed (exit $LASTEXITCODE)" }

    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host ".env created from .env.example" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Bootstrap complete. Activate with:  .\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
    Write-Host "Then verify LM Studio:  lumos ping" -ForegroundColor Cyan
}
finally {
    Pop-Location
}
