# FabricFinder — CLI chatbot over the HelixMCEDU Fabric warehouse.
# Bakes in the Microsoft ODBC Driver 18 so users need nothing installed but Docker.
FROM python:3.12-slim-bookworm

# --- Microsoft ODBC Driver 18 for SQL Server (amd64 + arm64) ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates apt-transport-https \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] \
https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql18 unixodbc-dev \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + the world-lookup spreadsheet (copied per .dockerignore rules)
COPY . .

# Persistent paths live under /data (mount a volume there to keep sign-in +
# conversation memory across runs). Reports go to /app/reports (bind-mountable).
ENV HOME=/data/home \
    FABRICFINDER_MEMORY_FILE=/data/conversation_memory.json \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data/home /app/reports

ENTRYPOINT ["python", "bot.py"]
