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

## Prerequisites

- **Python 3.9+**
- **Microsoft ODBC Driver 18 for SQL Server** (system dependency — `pyodbc`
  links to it at runtime; it is *not* a pip package).
  - macOS: `brew install msodbcsql18`
- Access to the Fabric SQL endpoint with an Azure AD account.
- An Azure OpenAI deployment.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env      # then fill in your Azure OpenAI values
```

Place the world-lookup spreadsheet at:

```
memory/xls_data/Master Content List.xlsx
```

## Usage

```bash
.venv/bin/python bot.py
```

Then chat:

```
you > What are the top 5 countries by total current MAU?
you > How many sessions did the 'Frozen Planet 2' worlds get in 2025?
you > /chart histogram of tenant MAU in TX
```

Type `exit` to quit. The first query of a session opens a browser for Azure AD
sign-in; the token is cached for subsequent runs.

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
