# FabricFinder

A CLI chatbot over the **HelixMCEDU** Microsoft Fabric data warehouse. Ask
questions in plain English; the bot (Azure OpenAI `gpt-5-chat`) plans and runs
read-only SQL, answers you, tells you when something isn't answerable from the
data, and writes a timestamped Markdown report (plus a CSV, and optionally a
chart) to `reports/`.

## Features

- **Natural-language → SQL** against the warehouse, with the full schema given
  to the model.
- **World name search** — content is stored only by `world_product_id` (GUID);
  the bot resolves names ↔ IDs via `Master Content List.xlsx`.
- **Dated reports** — every answered question produces
  `reports/YYYY-MM-DD_HHMMSS_<title>.md` with the question, the SQL used, a
  results table, and analysis.
- **CSV export** alongside each report (auto-enriched with a `world_name`
  column when results contain `world_product_id`).
- **Charts** — `/chart <prompt>` renders a histogram/bar/line/pie/scatter PNG
  and embeds it in the report.
- **Conversation memory** across sessions (`memory/conversation_memory.json`).

## Run with Docker (recommended for the team)

The Docker image bundles the Microsoft ODBC Driver 18, so the only thing each
teammate installs is **Docker**. Everyone signs in to Fabric as themselves via
device code (no admin setup required).

```bash
git clone https://github.com/jjedwards2081/FabricFinder.git
cd FabricFinder
cp .env.example .env          # fill in your Azure OpenAI key/endpoint
docker compose run --rm fabricfinder
```

On first run it prints a URL + code — open it in any browser and sign in with
your `@microsoft.com` account. Your sign-in and conversation history persist in
a Docker volume; generated reports/CSVs/charts appear in `./reports` on your
machine. Type `exit` to quit.

> Apple Silicon: the image builds natively for arm64. If the ODBC driver ever
> fails to install, force amd64 (emulated):
> `DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose build`.

### Prerequisites
- **Docker** (Desktop on macOS/Windows).
- An Azure AD account with read access to the `HelixMCEDU` warehouse.
- Azure OpenAI key + endpoint (in your `.env`).

## Run locally without Docker

Requires **Python 3.9+** and the **Microsoft ODBC Driver 18 for SQL Server**
installed on your OS (`brew install msodbcsql18` on macOS).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in your Azure OpenAI values
.venv/bin/python bot.py
```

The world-lookup spreadsheet must be at
`memory/xls_data/Master Content List.xlsx`.

## Using it

```
you > What are the top 5 countries by total current MAU?
you > How many sessions did the 'Frozen Planet 2' worlds get in 2025?
you > /chart histogram of tenant MAU in TX
```

The first query of a session triggers Azure AD device-code sign-in; the token
is cached for subsequent runs.

## Project layout

| File | Role |
|------|------|
| `bot.py` | CLI loop + Azure OpenAI agent (tools: `run_sql`, `search_worlds`, `name_worlds`, `create_chart`, `save_report`). |
| `connect.py` | Fabric auth (interactive Azure AD, cached token) + ODBC connection. |
| `db.py` | Schema introspection + read-only query guard. |
| `worlds.py` | World name ↔ product-ID lookup from the content-list spreadsheet. |
| `charts.py` | Headless PNG chart rendering. |
| `reports/` | Generated reports, CSVs, charts (gitignored). |
| `memory/` | Conversation memory + data files. |

## Security notes

- `.env` (API key), generated `reports/`, conversation memory, and internal
  data files are gitignored. Only `Master Content List.xlsx` is tracked.
- Queries are restricted to a single read-only `SELECT`/`WITH` statement
  (see `db.py`).
