"""FabricFinder — a CLI chatbot over the HelixMCEDU Fabric warehouse.

Ask questions in plain English. The bot (Azure OpenAI gpt-5-chat) plans and
runs read-only SQL, answers you, says when something isn't answerable from the
data, and writes a timestamped Markdown report (plus a CSV of the result data)
to ./reports. Conversation history persists across sessions in ./memory.
"""
import csv
import json
import os
from datetime import datetime

import updater
import usage_log

# Check for a newer version on the git remote and restart if we updated.
# Must run before we import heavy modules / read env, so the new code is used.
updater.check_and_update()

APP_VERSION = updater.get_version()

from dotenv import load_dotenv
from openai import AzureOpenAI
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

import charts
import db
import lxp
import tenant_report
import worlds

console = Console()

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
REPORTS_DIR = os.environ.get(
    "FABRICFINDER_REPORTS_DIR", os.path.join(BASE_DIR, "reports")
)
MEMORY_FILE = os.environ.get(
    "FABRICFINDER_MEMORY_FILE",
    os.path.join(BASE_DIR, "memory", "conversation_memory.json"),
)
MAX_TOOL_TURNS = 24
MEMORY_RECALL = 15   # how many past interactions to feed the model
MEMORY_KEEP = 100    # how many to retain on disk

client = AzureOpenAI(
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_version=os.environ["AZURE_OPENAI_API_VERSION"],
)
DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]

SYSTEM_PROMPT = """You are FabricFinder, a data analyst assistant for the \
Microsoft Education "HelixMCEDU" Fabric SQL warehouse.

Your job:
1. Understand the user's question (use prior conversation history for context,
   e.g. follow-ups like "and for teachers only?").
2. Use the `run_sql` tool to query the warehouse and gather the answer. You may
   run several queries (explore, then refine). Always inspect results before
   concluding.
3. If the question CANNOT be answered from the available tables/columns, clearly
   tell the user it is not possible and explain what data would be needed.
4. When you have a substantive, data-backed answer, call `save_report` exactly
   once to persist a Markdown report, then give the user a concise summary in
   chat. A CSV of your most recent query's results is exported automatically
   alongside the report.

SQL rules (this is Microsoft Fabric / T-SQL, read-only):
- Only SELECT / WITH queries. Never write, modify, or run DDL.
- Use `TOP n` (not LIMIT). Use square brackets for identifiers if needed.
- Dates: month/week columns are real DATE types where noted; some are stored as
  strings (startOfMonth in mau). Cast when comparing.
- Prefer joining fact tables to `tenant_mapping` for tenant names and geography.
- Keep result sets reasonable (use aggregation / TOP). Make the final query the
  one whose rows best represent the answer (it becomes the CSV export).
- Geography: `Country` holds full English names (e.g. 'United States',
  'United Kingdom'), NOT ISO codes like 'US'/'GB'. `Region` is a broad
  geo/sales region, not a US state. There is no state column, so US-state
  questions (e.g. "California") can only be approximated via `City`.
- IMPORTANT: a filtered query returning zero rows does NOT mean the data is
  absent — it usually means the filter value is wrong. Before telling the user
  something isn't in the data, verify the real values, e.g.
  `SELECT TOP 50 Country, COUNT(*) c FROM ... GROUP BY Country ORDER BY c DESC`
  (always ORDER BY when sampling; an unordered TOP returns arbitrary rows), then
  retry with the correct value.

Charts:
- When the user asks for a chart (or sends a [CHART REQUEST]), gather the data
  with run_sql, then call `create_chart`. Honor an explicitly requested type
  (e.g. "histogram", "bar", "line", "pie"); otherwise choose the best fit.
- Query results are capped at 500 rows. For a DISTRIBUTION / "bucket" /
  histogram over a large population, bucket the data in SQL with CASE into
  ranges and return one row per bucket (bucket label + count), then render a
  `bar` chart (x = bucket labels, y = counts). Only pass raw `values` for a
  true histogram when the row count is small.
- After creating the chart, call `save_report` and embed the image in the
  Markdown body using the returned filename: `![<title>](<filename>.png)`.

Tenants / account contacts (LXP):
- The warehouse has no Account Manager / Account Executive column. Use
  `lookup_tenant_contacts` with a tenant_id (GUID) or partial tenant name to
  fetch AM (account-manager alias), Account Executive, vertical, field region,
  renewal date, advisory-board status, FY26 engagement notes, etc., sourced
  from the LXP-derived workbook.
- If the user asks "who owns/covers this tenant", "who is the AE/AM/account
  manager for X", "when does it renew", or similar contact/renewal questions,
  call this tool BEFORE answering — those facts are only in LXP.
- If the lookup returns multiple matches, show the user a short list and ask
  which one they mean rather than guessing.

Worlds / content:
- The only table with content usage is `content_sessions_monthly`, and it
  identifies content ONLY by `world_product_id` (a GUID) -- there is no world
  name column in the database.
- To answer anything about a world/content by NAME, FIRST call `search_worlds`
  to resolve the name to one or more product IDs, then filter
  `content_sessions_monthly.world_product_id IN (...)` using those IDs.
- When you SELECT `world_product_id` in results, call `name_worlds` to translate
  the IDs back to readable names before reporting (the DB cannot do this join).
- If a name matches no world in the content list, tell the user it wasn't found
  and (optionally) show close matches from `search_worlds`.

Schema (database dbo):
{schema}

Reply formatting (the chat reply, not the saved report):
- Always respond in GitHub-Flavored Markdown so it renders well in the terminal.
- Lead with a short `##` heading naming the answer.
- When you have tabular results, present them as a Markdown table (pipes and a
  `---` separator row). Right-align numeric columns by using `---:` in the
  separator. Format large integers with thousands separators. Limit to the top
  ~15 rows in chat; mention if the CSV has more.
- Use `**bold**` for key figures and short bullet lists for takeaways.
- Close with a one-line pointer to the saved report filename when applicable.

Prior conversation history (most recent last; may be empty):
{memory}

Today's date is {today}.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Run a single read-only T-SQL SELECT/WITH query against the "
                "HelixMCEDU warehouse and return rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A single read-only T-SQL query.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_worlds",
            "description": (
                "Look up Minecraft Education worlds/content by name (substring, "
                "case-insensitive) and get their product IDs (the GUIDs stored "
                "in content_sessions_monthly.world_product_id). Use this to "
                "translate a world NAME into IDs before querying usage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "World name or partial name to search for.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "name_worlds",
            "description": (
                "Translate a list of world_product_id GUIDs back into readable "
                "world names. Use to label query results that contain "
                "world_product_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of world_product_id GUIDs.",
                    }
                },
                "required": ["product_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_tenant_contacts",
            "description": (
                "Look up tenant detail from the LXP-derived workbook by "
                "TenantId (GUID) or by case-insensitive substring on tenant "
                "name. Returns 0..N matches, each with AM (account-manager "
                "alias), Account Executive, vertical, field region, renewal "
                "date, advisory-board status, FY26 engagement notes, and "
                "other contact/account fields. Use this for any 'who covers "
                "X', 'AM/AE for X', or renewal-date question — the warehouse "
                "does not have these columns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Tenant GUID or partial tenant name.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max name-match results to return "
                        "(default 10).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_chart",
            "description": (
                "Render a chart to a PNG image from data you have queried. "
                "Returns the image filename so you can embed it in the report "
                "with Markdown image syntax: ![title](filename.png)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["histogram", "bar", "line", "pie", "scatter"],
                        "description": "Honor the type the user asked for; "
                        "otherwise pick the best fit.",
                    },
                    "title": {"type": "string"},
                    "x_label": {"type": "string"},
                    "y_label": {"type": "string"},
                    "x": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Category/x-axis values (bar, line, "
                        "scatter, pie labels). Omit for histogram.",
                    },
                    "y": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Numeric series for bar/line/scatter/pie.",
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Raw numeric values to bin (histogram).",
                    },
                    "bins": {
                        "type": "integer",
                        "description": "Number of histogram bins (optional).",
                    },
                    "series_name": {"type": "string"},
                },
                "required": ["chart_type", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_report",
            "description": (
                "Save a Markdown report of the findings to the reports folder. "
                "A CSV of the most recent query result is exported automatically. "
                "Call once per answered question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short report title (used in filename).",
                    },
                    "markdown_body": {
                        "type": "string",
                        "description": (
                            "Full report body in Markdown: the question, the "
                            "approach, key SQL used, a results table, and the "
                            "analysis/answer."
                        ),
                    },
                },
                "required": ["title", "markdown_body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_tenant_pdf",
            "description": (
                "Render a detailed, fact-only PDF report for a SINGLE tenant. "
                "Use only for /tenant requests. Gather every field with run_sql "
                "FIRST; do not invent figures. Include the full available "
                "monthly history of MAU and NUA (oldest first), the LAST FULL "
                "month (never the partial in-progress month) as current_month, "
                "the year-over-year comparison vs. the same month one year "
                "earlier, a trailing-6-month YoY comparison (yoy_6m) to smooth "
                "seasonality, top content by sessions, device breakdown, and a "
                "status classification (HEALTHY / INTENSIVE / PIPELINE / "
                "DECLINE) with a one-line factual rationale that cites both "
                "the single-month and 6-month YoY % changes. Highlights must "
                "be facts (numbers, ratios, ranks) — no assumptions or advice."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tenant_name": {"type": "string"},
                    "tenant_id":   {"type": "string"},
                    "geography": {
                        "type": "object",
                        "properties": {
                            "country": {"type": "string"},
                            "region":  {"type": "string"},
                            "city":    {"type": "string"},
                        },
                    },
                    "status": {
                        "type": "string",
                        "enum": ["HEALTHY", "INTENSIVE", "PIPELINE", "DECLINE"],
                    },
                    "status_rationale": {"type": "string"},
                    "current_month": {
                        "type": "object",
                        "properties": {
                            "month": {"type": "string"},
                            "mau":   {"type": "number"},
                            "nua":   {"type": "number"},
                        },
                    },
                    "yoy": {
                        "type": "object",
                        "properties": {
                            "prior_month":    {"type": "string"},
                            "prior_mau":      {"type": "number"},
                            "prior_nua":      {"type": "number"},
                            "mau_change_pct": {"type": "number"},
                            "nua_change_pct": {"type": "number"},
                        },
                    },
                    "yoy_6m": {
                        "type": "object",
                        "description": (
                            "Trailing-6-month year-over-year comparison "
                            "ending at the last full month. Use SUM totals "
                            "(metric='sum') unless you explicitly used "
                            "averages (metric='avg')."
                        ),
                        "properties": {
                            "metric":            {"type": "string",
                                                   "enum": ["sum", "avg"]},
                            "current_period":    {"type": "string"},
                            "prior_period":      {"type": "string"},
                            "current_mau":       {"type": "number"},
                            "current_nua":       {"type": "number"},
                            "prior_mau":         {"type": "number"},
                            "prior_nua":         {"type": "number"},
                            "mau_change_pct":    {"type": "number"},
                            "nua_change_pct":    {"type": "number"},
                        },
                    },
                    "monthly_series": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "month": {"type": "string"},
                                "mau":   {"type": "number"},
                                "nua":   {"type": "number"},
                            },
                            "required": ["month"],
                        },
                    },
                    "top_content": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name":     {"type": "string"},
                                "sessions": {"type": "number"},
                            },
                        },
                    },
                    "device_breakdown": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "device": {"type": "string"},
                                "value":  {"type": "number"},
                            },
                        },
                    },
                    "highlights":   {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "account_contact": {
                        "type": "object",
                        "description": (
                            "Optional contact/account block from "
                            "lookup_tenant_contacts. Pass through verbatim; do "
                            "not invent fields."
                        ),
                        "properties": {
                            "am":                {"type": "string"},
                            "account_executive": {"type": "string"},
                            "vertical":          {"type": "string"},
                            "field_area":        {"type": "string"},
                            "field_region":      {"type": "string"},
                            "sales_unit":        {"type": "string"},
                            "dominant_position": {"type": "string"},
                            "primary_upsell":    {"type": "string"},
                            "renewal_date":      {"type": "string"},
                            "renewal_flag":      {"type": "string"},
                            "earliest_anniversary": {"type": "string"},
                            "advisory_board":    {"type": "string"},
                            "fy26_engagement":   {"type": "string"},
                            "training_partner":  {"type": "string"},
                            "technical_blocker": {"type": "string"},
                        },
                    },
                    "data_sources": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["tenant_name", "status"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# Reports + CSV export
# --------------------------------------------------------------------------- #
def _slug(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in text)
    return "-".join(keep.lower().split())[:60] or "report"


def _enrich_world_names(columns, rows):
    """If results contain world_product_id, insert a world_name column next to it."""
    lower = [c.lower() for c in columns]
    if "world_product_id" not in lower:
        return columns, rows
    idx = lower.index("world_product_id")
    ids = [r[idx] for r in rows]
    name_map = worlds.names_for_ids([i for i in ids if i is not None])
    new_cols = columns[: idx + 1] + ["world_name"] + columns[idx + 1 :]
    new_rows = [
        r[: idx + 1] + [name_map.get(r[idx])] + r[idx + 1 :] for r in rows
    ]
    return new_cols, new_rows


def _enrich_tenant_contacts(columns, rows):
    """If results contain a TenantId column, append LXP contact columns."""
    lower = [c.lower().replace("_", "").replace(" ", "") for c in columns]
    tid_idx = next(
        (i for i, c in enumerate(lower) if c in {"tenantid", "tenant"}),
        None,
    )
    if tid_idx is None:
        return columns, rows
    extra_cols = lxp.enrich_columns()
    new_cols = list(columns) + extra_cols
    new_rows = [list(r) + lxp.enrich_row(r[tid_idx]) for r in rows]
    return new_cols, new_rows


def save_report(title, markdown_body, result):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    now = datetime.now()
    stem = f"{now:%Y-%m-%d_%H%M%S}_{_slug(title)}"
    md_path = os.path.join(REPORTS_DIR, stem + ".md")

    csv_path = None
    if result and result.get("columns"):
        cols, rows = _enrich_world_names(result["columns"], result["rows"])
        cols, rows = _enrich_tenant_contacts(cols, rows)
        csv_path = os.path.join(REPORTS_DIR, stem + ".csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            writer.writerows(rows)

    meta = [
        f"*Generated: {now:%Y-%m-%d %H:%M:%S}*  ",
        "*Source: HelixMCEDU (Microsoft Fabric)*  ",
    ]
    if csv_path:
        meta.append(f"*Data export: [{os.path.basename(csv_path)}]"
                    f"({os.path.basename(csv_path)})*  ")
    header = f"# {title}\n\n" + "\n".join(meta) + "\n\n---\n\n"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(header + markdown_body.strip() + "\n")
    return {"report": md_path, "csv": csv_path}


# --------------------------------------------------------------------------- #
# Cross-session conversation memory
# --------------------------------------------------------------------------- #
def load_memory() -> list:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def append_memory(question, answer_text, report_path):
    mem = load_memory()
    mem.append(
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "question": question,
            "answer": (answer_text or "")[:800],
            "report": os.path.basename(report_path) if report_path else None,
        }
    )
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem[-MEMORY_KEEP:], f, indent=2)


def memory_block() -> str:
    mem = load_memory()[-MEMORY_RECALL:]
    if not mem:
        return "(no prior conversations yet)"
    out = []
    for e in mem:
        line = f"- [{e['timestamp']}] Q: {e['question']}\n  A: {e['answer']}"
        if e.get("report"):
            line += f"\n  report: {e['report']}"
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Agent loop
# --------------------------------------------------------------------------- #
def _handle_tool_call(call, state):
    name = call.function.name
    args = json.loads(call.function.arguments or "{}")
    if name == "run_sql":
        console.print("  [dim]↳ running SQL...[/]")
        try:
            result = db.run_query(args.get("query", ""))
            state["last_result"] = result  # remember for CSV export
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})
    if name == "search_worlds":
        hits = worlds.search_worlds(args.get("name", ""))
        console.print(
            f"  [dim]↳ world lookup: '{args.get('name','')}' "
            f"→ {len(hits)} match(es)[/]"
        )
        return json.dumps(
            {"matches": [{"name": w["name"], "product_id": w["product_id"]}
                         for w in hits]}
        )
    if name == "name_worlds":
        return json.dumps(worlds.names_for_ids(args.get("product_ids", [])))
    if name == "lookup_tenant_contacts":
        q = args.get("query", "")
        hits = lxp.lookup(q, limit=int(args.get("limit") or 10))
        console.print(
            f"  [dim]↳ tenant lookup: '{q}' → {len(hits)} match(es)[/]"
        )
        return json.dumps({"matches": hits}, default=str)
    if name == "create_chart":
        try:
            path = charts.render_chart(args)
            state["chart_path"] = path
            console.print(f"  [dim]↳ chart created: {path}[/]")
            return json.dumps({"chart_file": os.path.basename(path)})
        except Exception as e:
            return json.dumps({"error": str(e)})
    if name == "save_report":
        paths = save_report(
            args["title"], args["markdown_body"], state.get("last_result")
        )
        state["report_path"] = paths["report"]
        console.print(f"  [dim]↳ report saved: {paths['report']}[/]")
        if paths["csv"]:
            console.print(f"  [dim]↳ csv saved:    {paths['csv']}[/]")
        return json.dumps({"saved_to": paths["report"], "csv": paths["csv"]})
    if name == "save_tenant_pdf":
        try:
            path = tenant_report.render_tenant_pdf(args)
            state["report_path"] = path
            console.print(f"  [dim]↳ tenant PDF saved: {path}[/]")
            return json.dumps({"saved_to": path})
        except Exception as e:
            return json.dumps({"error": str(e)})
    return json.dumps({"error": f"unknown tool {name}"})


def answer(messages, state):
    """Run the tool-calling loop until the model produces a final reply.

    Accumulates token usage across every LLM call in ``state['usage']``.
    """
    usage = state.setdefault(
        "usage",
        {"prompt_tokens": 0, "completion_tokens": 0,
         "total_tokens": 0, "llm_calls": 0},
    )
    for _ in range(MAX_TOOL_TURNS):
        resp = client.chat.completions.create(
            model=DEPLOYMENT, messages=messages, tools=TOOLS
        )
        if getattr(resp, "usage", None) is not None:
            usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
            usage["completion_tokens"] += resp.usage.completion_tokens or 0
            usage["total_tokens"] += resp.usage.total_tokens or 0
        usage["llm_calls"] += 1
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or "(no response)"
        for call in msg.tool_calls:
            output = _handle_tool_call(call, state)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": output}
            )
    return "Stopped after too many steps — try narrowing the question."


def main():
    # Connect first (this triggers interactive sign-in on the first run), then
    # welcome the user once we actually have the schema in hand.
    with console.status(
        "[cyan]Connecting to HelixMCEDU…[/]", spinner="dots"
    ):
        schema = db.get_schema_context()
    console.print(
        Panel.fit(
            Text.from_markup(
                "[bold cyan]Welcome to FabricFinder![/]\n"
                f"[dim]version:[/] [bold]{APP_VERSION}[/]\n"
                "Connected to the [bold]HelixMCEDU[/] Fabric warehouse.\n\n"
                "What would you like to know?\n"
                "[dim]Type 'exit' to quit.  Commands: "
                "'/chart <prompt>' makes a chart, "
                "'/tenant <name>' produces a tenant PDF report, "
                "'/log' bundles usage logs, "
                "'/version' shows the running version.[/]"
            ),
            border_style="cyan",
        )
    )
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                schema=schema,
                memory=memory_block(),
                today=datetime.now().strftime("%Y-%m-%d"),
            ),
        }
    ]
    while True:
        try:
            q = console.input("[bold green]you ›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            break
        if q.lower() in {"exit", "quit"}:
            break
        if not q:
            continue
        low = q.lower()
        if low in {"/version", "/v"}:
            console.print(f"[cyan]FabricFinder version:[/] {APP_VERSION}")
            continue
        if low.startswith("/log"):
            zip_path = usage_log.bundle_logs(REPORTS_DIR)
            totals = usage_log.session_totals()
            cost_str = (
                f"${totals['cost_usd']:.4f}"
                + ("" if totals["cost_known"] else " (partial — unknown model)")
            )
            console.print(
                Panel.fit(
                    Text.from_markup(
                        f"[bold]Usage logs bundled[/]\n"
                        f"[dim]file:[/] {zip_path}\n\n"
                        f"[bold]This session so far[/]\n"
                        f"  turns:             {totals['turns']}\n"
                        f"  LLM prompt tokens: {totals['prompt_tokens']:,}\n"
                        f"  LLM output tokens: {totals['completion_tokens']:,}\n"
                        f"  LLM total tokens:  {totals['total_tokens']:,}\n"
                        f"  estimated cost:    {cost_str}"
                    ),
                    border_style="cyan",
                )
            )
            continue
        if low.startswith("/chart"):
            prompt = q[len("/chart"):].strip()
            content = (
                f"[CHART REQUEST] Build a chart for: {prompt}\n"
                "Honor the chart type if one is named; otherwise infer the best "
                "fit. Gather data with run_sql (bucket distributions in SQL), "
                "call create_chart, then save_report embedding the image."
            )
        elif low.startswith("/tenant"):
            name = q[len("/tenant"):].strip()
            if not name:
                console.print(
                    "[yellow]Usage:[/] /tenant <tenant name or partial name>"
                )
                continue
            content = (
                f"[TENANT REPORT REQUEST] Tenant: {name!r}.\n"
                "Produce a detailed, FACT-ONLY PDF report. Steps:\n"
                "1. Resolve the tenant. Query tenant_mapping (or whatever the "
                "schema names it) with a case-insensitive LIKE on the name. If "
                "MORE THAN ONE distinct tenant matches, STOP and ask the user "
                "which one they meant — show a short numbered list with name, "
                "country, and tenant id. Do NOT call save_tenant_pdf yet.\n"
                "2. Once a single tenant is identified, call "
                "`lookup_tenant_contacts` with the tenant_id to fetch "
                "AM/Account Executive/vertical/renewal/etc. from LXP. Carry "
                "these into the save_tenant_pdf call as `account_contact` "
                "(snake_case keys). Then gather (each via "
                "run_sql, joining on the tenant id):\n"
                "   a. The FULL available history of monthly MAU and NUA "
                "      (oldest first). Use the canonical month column (cast to "
                "      YYYY-MM strings).\n"
                "   b. The LAST FULL month of MAU and NUA. Do NOT use the "
                "      partial in-progress current month — always step back to "
                "      the most recent month that has fully closed. Use that "
                "      month as the 'current_month' anchor for all comparisons.\n"
                "   c. Year-over-year (single month) comparison: same month "
                "      one year earlier vs. the last full month from (b), "
                "      with % change for MAU and NUA.\n"
                "   d. Trailing-6-month YoY comparison (to smooth seasonality):\n"
                "      sum/avg MAU and NUA across the last 6 FULL months "
                "      ending at the anchor month from (b), and compare to the "
                "      same 6 calendar months one year earlier. Report both "
                "      period totals (or averages — be explicit which) and the "
                "      % change for MAU and NUA.\n"
                "   e. Top 10 content/worlds by sessions for this tenant from "
                "      content_sessions_monthly (resolve world_product_id "
                "      with name_worlds).\n"
                "   f. Device breakdown (sessions or users) if a device or "
                "      platform column exists; otherwise omit.\n"
                "   g. Any other long-view facts: months active, peak MAU + "
                "      month, peak NUA + month.\n"
                "3. Classify status using ONLY these rules, evaluated against "
                "   the trailing-6-month YoY change (preferred, because it "
                "   smooths seasonality), with the single-month YoY as a "
                "   tiebreaker:\n"
                "   - HEALTHY   = MAU growth AND NUA growth\n"
                "   - INTENSIVE = MAU flat, NUA growth\n"
                "   - PIPELINE  = MAU growth, NUA flat or declining\n"
                "   - DECLINE   = MAU decline AND NUA decline\n"
                "   Pick the closest match for edge cases. The rationale must "
                "   cite the specific % changes you observed (both the single-"
                "   month and the 6-month YoY) — no opinions, no forecasts, "
                "   no recommendations.\n"
                "4. Call save_tenant_pdf ONCE with all the gathered fields, "
                "   including the new yoy_6m block. highlights[] must be "
                "   short factual statements (numbers, ranks, months) — never "
                "   assumptions, never advice.\n"
                "5. In chat, reply with a brief one-paragraph summary and the "
                "   saved PDF path."
            )
        else:
            content = q
        messages.append({"role": "user", "content": content})
        state = {}
        error = None
        try:
            reply = answer(messages, state)
        except Exception as e:
            error = repr(e)
            reply = f"(error: {e})"
        append_memory(q, reply, state.get("report_path"))
        u = state.get("usage", {})
        usage_log.log_turn(
            question=q,
            reply=reply,
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
            llm_calls=u.get("llm_calls", 0),
            model=DEPLOYMENT,
            version=APP_VERSION,
            report_path=state.get("report_path"),
            chart_path=state.get("chart_path"),
            error=error,
        )
        console.print()
        console.print(Rule("[bold cyan]bot[/]", style="cyan", align="left"))
        console.print(Markdown(reply or "(no response)"))
        console.print(Rule(style="cyan"))
        cost = usage_log.estimate_cost(
            DEPLOYMENT, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        )
        cost_part = f" · ~${cost:.4f}" if cost is not None else ""
        console.print(
            f"[dim]tokens: {u.get('total_tokens', 0):,} "
            f"(prompt {u.get('prompt_tokens', 0):,}, "
            f"output {u.get('completion_tokens', 0):,}){cost_part}[/]"
        )
        console.print()


if __name__ == "__main__":
    main()
