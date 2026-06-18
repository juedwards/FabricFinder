# FabricFinder GUI launcher (Windows PowerShell).
# Run: powershell -ExecutionPolicy Bypass -File .\run_gui.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .\.venv\Scripts\pythonw.exe)) {
    Write-Host "!! No .venv found. Run setup.ps1 first." -ForegroundColor Red
    exit 1
}

# pythonw launches Python without a console window.
Start-Process -FilePath ".\.venv\Scripts\pythonw.exe" `
              -ArgumentList "gui.py" `
              -WorkingDirectory $PSScriptRoot

Write-Host "FabricFinder GUI launched." -ForegroundColor Green
