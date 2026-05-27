"""Per-turn usage logging: prompts, token counts, and estimated cost.

Each user turn appends one JSON line to ``logs/usage-YYYY-MM.jsonl`` with:
    timestamp, session_id, user, version, question, reply_preview,
    prompt_tokens, completion_tokens, total_tokens, cost_usd, model, llm_calls

Cost is *estimated* using a small price table in USD per 1K tokens (edit
``MODEL_PRICES`` below to match your Azure OpenAI contract). If the model is
unknown the cost is recorded as ``null`` but token counts are still kept.

The ``/log`` command in bot.py calls :func:`bundle_logs` to zip the ``logs/``
directory into ``reports/`` so the user can hand it to support.
"""
from __future__ import annotations

import getpass
import json
import os
import socket
import uuid
import zipfile
from datetime import datetime
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.environ.get("FABRICFINDER_LOG_DIR", os.path.join(BASE_DIR, "logs"))

# USD per 1,000 tokens. Update for your deployment / contract.
# Keys are matched case-insensitively against the deployment name.
MODEL_PRICES = {
    "gpt-5-chat":     {"input": 0.00250, "output": 0.01000},
    "gpt-4o":         {"input": 0.00250, "output": 0.01000},
    "gpt-4o-mini":    {"input": 0.00015, "output": 0.00060},
    "gpt-4.1":        {"input": 0.00200, "output": 0.00800},
    "gpt-4.1-mini":   {"input": 0.00040, "output": 0.00160},
    "gpt-4-turbo":    {"input": 0.01000, "output": 0.03000},
    "gpt-35-turbo":   {"input": 0.00050, "output": 0.00150},
    "gpt-3.5-turbo":  {"input": 0.00050, "output": 0.00150},
}

SESSION_ID = uuid.uuid4().hex[:12]


def _price_for(model: str) -> Optional[dict]:
    if not model:
        return None
    m = model.lower()
    if m in MODEL_PRICES:
        return MODEL_PRICES[m]
    # Permissive substring match (e.g. "gpt-4o-2024-08-06" -> "gpt-4o").
    for key, price in MODEL_PRICES.items():
        if key in m:
            return price
    return None


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int):
    p = _price_for(model)
    if p is None:
        return None
    return round(
        (prompt_tokens / 1000.0) * p["input"]
        + (completion_tokens / 1000.0) * p["output"],
        6,
    )


def _log_path() -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, f"usage-{datetime.now():%Y-%m}.jsonl")


def log_turn(
    *,
    question: str,
    reply: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    llm_calls: int,
    model: str,
    version: str,
    report_path: Optional[str] = None,
    chart_path: Optional[str] = None,
    error: Optional[str] = None,
) -> dict:
    """Append a single turn record to today's log file. Returns the record."""
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "session_id": SESSION_ID,
        "user": getpass.getuser(),
        "host": socket.gethostname(),
        "version": version,
        "model": model,
        "question": (question or "")[:2000],
        "reply_preview": (reply or "")[:500],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "llm_calls": llm_calls,
        "cost_usd": estimate_cost(model, prompt_tokens, completion_tokens),
        "report": os.path.basename(report_path) if report_path else None,
        "chart": os.path.basename(chart_path) if chart_path else None,
        "error": error,
    }
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return record


def session_totals() -> dict:
    """Sum tokens and cost for the current SESSION_ID across all log files."""
    totals = {
        "turns": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_known": True,
    }
    if not os.path.isdir(LOG_DIR):
        return totals
    for name in os.listdir(LOG_DIR):
        if not name.startswith("usage-") or not name.endswith(".jsonl"):
            continue
        path = os.path.join(LOG_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("session_id") != SESSION_ID:
                        continue
                    totals["turns"] += 1
                    totals["prompt_tokens"] += rec.get("prompt_tokens") or 0
                    totals["completion_tokens"] += rec.get("completion_tokens") or 0
                    totals["total_tokens"] += rec.get("total_tokens") or 0
                    cost = rec.get("cost_usd")
                    if cost is None:
                        totals["cost_known"] = False
                    else:
                        totals["cost_usd"] += float(cost)
        except OSError:
            continue
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals


def bundle_logs(out_dir: str) -> Optional[str]:
    """Zip all logs into ``out_dir`` and return the zip path (or None)."""
    if not os.path.isdir(LOG_DIR):
        return None
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    zip_path = os.path.join(out_dir, f"fabricfinder-logs-{stamp}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        added = 0
        for root, _, files in os.walk(LOG_DIR):
            for name in files:
                full = os.path.join(root, name)
                arc = os.path.relpath(full, LOG_DIR)
                zf.write(full, arcname=arc)
                added += 1
        if added == 0:
            zf.writestr(
                "README.txt",
                "No usage logs were recorded yet for this installation.\n",
            )
    return zip_path
