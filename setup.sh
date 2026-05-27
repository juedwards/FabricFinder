#!/usr/bin/env bash
# FabricFinder onboarding: Python venv + deps + ODBC Driver 18.
# Run once:  ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "==> FabricFinder setup"

# --- ODBC Driver 18 (system dependency for pyodbc) ---
if ! odbcinst -q -d 2>/dev/null | grep -q "ODBC Driver 18 for SQL Server"; then
  case "$(uname -s)" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        echo "!! Homebrew not found. Install from https://brew.sh then re-run." >&2
        exit 1
      fi
      echo "==> Installing ODBC Driver 18 via Homebrew"
      brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
      brew update
      HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18
      ;;
    *)
      echo "!! Please install 'msodbcsql18' for your OS, then re-run." >&2
      echo "   See: https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server" >&2
      exit 1
      ;;
  esac
else
  echo "==> ODBC Driver 18 already installed"
fi

# --- Python environment ---
echo "==> Creating virtualenv (.venv) and installing dependencies"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# --- Config ---
if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env — edit it and add your Azure OpenAI key/endpoint."
fi

echo
echo "✅ Setup complete. Next:"
echo "   1) Edit .env with your Azure OpenAI values"
echo "   2) Run ./run.sh  (a browser opens for Azure AD sign-in on first query)"
