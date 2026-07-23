# FabricFinder Copilot Instructions

## Running the application

- On Windows, bootstrap with `powershell -ExecutionPolicy Bypass -File .\setup.ps1`, then launch with `powershell -ExecutionPolicy Bypass -File .\run.ps1`.
- On macOS, run `./setup.sh`, then `./run.sh`. Both setup scripts create `.venv`, install `requirements.txt`, and create `.env` from `.env.example` when needed.
- The app requires Python 3.9+, ODBC Driver 18 for SQL Server, Azure OpenAI values in `.env`, and interactive Azure AD authentication on a managed/compliant device.
- There is no configured automated test suite, linter, formatter, or build target, so do not invent commands or claim a single-test invocation exists. The executable module is `bot.py`; individual support modules also expose small `__main__` smoke paths where present.

## Architecture

- `bot.py` is the composition root and terminal conversation loop. At startup it runs `updater.check_and_update()`, loads `.env`, introspects the Fabric schema, builds the Azure OpenAI system prompt, and dispatches function calls from the model. Each turn is logged; substantive answers are persisted as reports.
- `connect.py` authenticates through `InteractiveBrowserCredential`, caches the token, converts it to the ODBC access-token structure, and opens the fixed `HelixMCEDU` Fabric endpoint. `db.py` is the only warehouse query boundary: it exposes schema context and rejects anything except one read-only `SELECT` or `WITH` statement, returning at most 500 rows.
- The model's tool loop in `bot.py` coordinates warehouse SQL with local lookup and rendering modules: `worlds.py` maps world names to `world_product_id` GUIDs from the tracked content workbook; `lxp.py` reads an optional local LXP tenant workbook for account/contact data; `charts.py` renders general PNG charts; and `tenant_report.py` builds fact-only tenant PDFs with derived visualizations.
- Every user request receives a timestamped, slugged folder directly under `reports/`. Its report, CSV, chart, tenant PDF, and SQL notes are written there; SQL notes live in that folder's `sql/` subdirectory. `save_report()` enriches CSV output with world names and, when a tenant identifier is present, LXP contact fields. Conversation memory is retained in `memory/conversation_memory.json`; `usage_log.py` appends JSONL usage records under `logs/` and `/log` creates its own output folder containing the bundled ZIP.

## Repository-specific conventions

- Keep warehouse access behind `db.run_query()` and preserve its single-statement, read-only guard. Use Fabric/T-SQL syntax (`TOP`, not `LIMIT`) and keep final result sets modest because tool results cap at 500 rows.
- Treat the current calendar month as incomplete for all default reporting. Filter it out in SQL and anchor latest, YoY, trend, trailing-window, and `/tenant` calculations on the most recently closed month. Include a partial month only for an explicit MTD/partial-period request, and label it as such.
- `tenant_mapping` is the preferred source for tenant names and geography. `Country` stores full English country names, `Region` is a broad sales/geo region rather than a US state, and state questions can only be approximated through `City`.
- For content questions by world name, resolve names with `search_worlds` before querying `content_sessions_monthly`; the warehouse only stores `world_product_id`. Translate GUIDs back with `name_worlds` before presenting results.
- For AM, AE, ownership, renewal, or related tenant-account questions, use the LXP lookup rather than the warehouse. If a name lookup is ambiguous, present the matches and ask for disambiguation instead of selecting one.
- Maintain the model-tool contract when changing tools: update `TOOLS`, `_handle_tool_call`, and the relevant system-prompt instructions together. Every `run_sql` invocation must include a purpose and plain-language SQL explanation so the separate query note is complete. `save_report` is expected once for a substantive ordinary answer; `/chart` must create and embed a PNG; `/tenant` must gather data first and call `save_tenant_pdf` once with fact-only fields.
- Tenant reports classify status from trailing six-month YoY first, using the single-month YoY as a tiebreaker. Preserve the `HEALTHY`, `INTENSIVE`, `PIPELINE`, and `DECLINE` definitions and cite observed percentage changes rather than adding forecasts or recommendations.
- Keep `.env`, generated reports/logs, conversation memory, and internal workbook data untracked. Only `memory/xls_data/Master Content List.xlsx` is intentionally tracked; the LXP workbook is optional local input.
- Preserve the environment-variable overrides for output locations: `FABRICFINDER_REPORTS_DIR`, `FABRICFINDER_MEMORY_FILE`, and `FABRICFINDER_LOG_DIR`.
