"""Render charts to PNG for the bot. Headless (Agg) so it works anywhere.

The bot gathers data via run_sql, then calls render_chart() with a simple spec.
Supported types: histogram, bar, line, pie, scatter.
"""
import os
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

CHARTS_DIR = os.path.join(os.path.dirname(__file__), "reports")


def _slug(text):
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in text or "")
    return "-".join(keep.lower().split())[:60] or "chart"


def _floats(seq):
    out = []
    for v in seq or []:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(None)
    return out


def render_chart(spec):
    """spec keys: chart_type, title, x_label, y_label, x, y, values, bins,
    series_name. Returns the saved PNG path."""
    ctype = (spec.get("chart_type") or "bar").lower()
    title = spec.get("title") or "Chart"
    x = spec.get("x") or spec.get("labels") or []
    y = spec.get("y") or spec.get("values") or []

    fig, ax = plt.subplots(figsize=(9, 5))

    if ctype == "histogram":
        vals = [v for v in _floats(spec.get("values") or y) if v is not None]
        bins = spec.get("bins") or "auto"
        ax.hist(vals, bins=bins, edgecolor="black", color="#4C78A8")
    elif ctype == "bar":
        ax.bar([str(i) for i in x], _floats(y), color="#4C78A8")
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    elif ctype == "line":
        ax.plot([str(i) for i in x], _floats(y), marker="o", color="#4C78A8",
                label=spec.get("series_name"))
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
        if spec.get("series_name"):
            ax.legend()
    elif ctype == "scatter":
        ax.scatter(_floats(x), _floats(y), color="#4C78A8", alpha=0.7)
    elif ctype == "pie":
        ax.pie(_floats(y), labels=[str(i) for i in x], autopct="%1.1f%%")
        ax.axis("equal")
    else:
        raise ValueError(f"Unsupported chart_type: {ctype}")

    ax.set_title(title)
    if ctype != "pie":
        if spec.get("x_label"):
            ax.set_xlabel(spec["x_label"])
        if spec.get("y_label"):
            ax.set_ylabel(spec["y_label"])
        ax.grid(axis="y", linestyle=":", alpha=0.5)

    fig.tight_layout()
    os.makedirs(CHARTS_DIR, exist_ok=True)
    fname = f"{datetime.now():%Y-%m-%d_%H%M%S}_{_slug(title)}.png"
    path = os.path.join(CHARTS_DIR, fname)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


if __name__ == "__main__":
    p = render_chart(
        {
            "chart_type": "bar",
            "title": "Demo Bar",
            "x_label": "Bucket",
            "y_label": "Count",
            "x": ["0-100", "100-1k", "1k-10k", "10k+"],
            "y": [120, 45, 18, 4],
        }
    )
    print("wrote", p)
