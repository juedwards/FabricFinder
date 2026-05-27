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

## Quick start (recommended)

Runs natively on your managed/compliant device, which is required to satisfy
the org's device-compliance Conditional Access policy (see note below).

```bash
git clone https://github.com/jjedwards2081/FabricFinder.git
cd FabricFinder
./setup.sh                    # venv + deps + ODBC Driver 18 (Homebrew on macOS)
# edit .env with your Azure OpenAI key/endpoint
./run.sh
```

On the first query a browser opens for Azure AD sign-in — sign in with your
`@microsoft.com` account. Each user signs in as themselves; the token is cached
for later runs. Type `exit` to quit.

### Prerequisites
- A **managed/compliant** device (corp laptop) — see the Conditional Access note.
- **Python 3.9+** and **Homebrew** (macOS) for the ODBC driver install.
- An Azure AD account with read access to the `HelixMCEDU` warehouse.
- Azure OpenAI key + endpoint (in your `.env`).

> **Manual setup** (instead of `setup.sh`): install the ODBC Driver 18
> (`brew install msodbcsql18` on macOS), then
> `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`,
> `cp .env.example .env`, and `.venv/bin/python bot.py`.

## Using it

```
you > What are the top 5 countries by total current MAU?
you > How many sessions did the 'Frozen Planet 2' worlds get in 2025?
you > /chart histogram of tenant MAU in TX
```

The first query of a session triggers an Azure AD browser sign-in; the token is
cached for subsequent runs.

> **Note:** This must run on a managed/compliant device. Containers (Docker)
> are rejected by the org's device-compliance Conditional Access policy
> (error `530033`), so there is no Docker option.

## Project layout

| File | Role |
|------|------|
| `bot.py` | CLI loop + Azure OpenAI agent (tools: `run_sql`, `search_worlds`, `name_worlds`, `create_chart`, `save_report`). |
| `connect.py` | Fabric auth (interactive Azure AD, cached token) + ODBC connection. |
| `db.py` | Schema introspection + read-only query guard. |
| `worlds.py` | World name ↔ product-ID lookup from the content-list spreadsheet. |
| `charts.py` | Headless PNG chart rendering. |
| `setup.sh` / `run.sh` | One-time onboarding (venv, deps, ODBC driver) and launcher. |
| `reports/` | Generated reports, CSVs, charts (gitignored). |
| `memory/` | Conversation memory + data files. |

## Security notes

- `.env` (API key), generated `reports/`, conversation memory, and internal
  data files are gitignored. Only `Master Content List.xlsx` is tracked.
- Queries are restricted to a single read-only `SELECT`/`WITH` statement
  (see `db.py`).
