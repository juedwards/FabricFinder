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

from dotenv import load_dotenv
from openai import AzureOpenAI
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

import charts
import db
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
MAX_TOOL_TURNS = 12
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


def save_report(title, markdown_body, result):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    now = datetime.now()
    stem = f"{now:%Y-%m-%d_%H%M%S}_{_slug(title)}"
    md_path = os.path.join(REPORTS_DIR, stem + ".md")

    csv_path = None
    if result and result.get("columns"):
        cols, rows = _enrich_world_names(result["columns"], result["rows"])
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
    return json.dumps({"error": f"unknown tool {name}"})


def answer(messages, state):
    """Run the tool-calling loop until the model produces a final reply."""
    for _ in range(MAX_TOOL_TURNS):
        resp = client.chat.completions.create(
            model=DEPLOYMENT, messages=messages, tools=TOOLS
        )
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
    console.print(
        Panel.fit(
            Text.from_markup(
                "[bold cyan]FabricFinder[/] — ask about HelixMCEDU.\n"
                "[dim]Type 'exit' to quit.  "
                "Tip: '/chart <prompt>' makes a chart, e.g. "
                "'/chart histogram of tenant MAU in TX'.[/]"
            ),
            border_style="cyan",
        )
    )
    schema = db.get_schema_context()
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
        if q.lower().startswith("/chart"):
            prompt = q[len("/chart"):].strip()
            content = (
                f"[CHART REQUEST] Build a chart for: {prompt}\n"
                "Honor the chart type if one is named; otherwise infer the best "
                "fit. Gather data with run_sql (bucket distributions in SQL), "
                "call create_chart, then save_report embedding the image."
            )
        else:
            content = q
        messages.append({"role": "user", "content": content})
        state = {}
        reply = answer(messages, state)
        append_memory(q, reply, state.get("report_path"))
        console.print()
        console.print(Rule("[bold cyan]bot[/]", style="cyan", align="left"))
        console.print(Markdown(reply or "(no response)"))
        console.print(Rule(style="cyan"))
        console.print()


if __name__ == "__main__":
    main()
