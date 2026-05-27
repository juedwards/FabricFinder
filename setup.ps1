# FabricFinder setup (Windows 11).
# Run:  powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> FabricFinder setup" -ForegroundColor Cyan

# --- Python ---
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
  Write-Host "!! Python not found. Install it, then re-run this script:" -ForegroundColor Red
  Write-Host "     winget install -e --id Python.Python.3.12"
  exit 1
}

# --- ODBC Driver 18 (system dependency for pyodbc) ---
$driver = Get-OdbcDriver -Name "ODBC Driver 18 for SQL Server" -ErrorAction SilentlyContinue
if (-not $driver) {
  Write-Host "!! 'ODBC Driver 18 for SQL Server' not found. Install it, then re-run:" -ForegroundColor Red
  Write-Host "     winget install -e --id Microsoft.msodbcsql.18"
  Write-Host "   (or install it from Company Portal / Software Center if winget is blocked)"
  exit 1
}

# --- Python environment ---
Write-Host "==> Creating virtualenv (.venv) and installing dependencies"
& $py.Source -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

# --- Config ---
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Write-Host "==> Created .env - edit it and add your Azure OpenAI key/endpoint."
}

Write-Host ""
Write-Host "Setup complete. Next:" -ForegroundColor Green
Write-Host "  1) Edit .env  (notepad .env)  and add your Azure OpenAI values"
Write-Host "  2) Run:  powershell -ExecutionPolicy Bypass -File .\run.ps1"
