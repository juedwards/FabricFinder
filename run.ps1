# Launch FabricFinder (Windows). Run setup.ps1 first.
# Run:  powershell -ExecutionPolicy Bypass -File .\run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  Write-Host "!! No .venv found. Run setup.ps1 first." -ForegroundColor Red
  exit 1
}

& .\.venv\Scripts\python.exe bot.py
