"""Database access for the bot: schema introspection + safe read-only queries."""
from collections import defaultdict
import re

from connect import DEFAULT_DATABASE, connect

# Statements that must never appear in a generated query.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|"
    r"REVOKE|EXEC|EXECUTE|SP_|XP_|BACKUP|RESTORE|INTO)\b",
    re.IGNORECASE,
)

MAX_ROWS = 500  # rows returned to the model per query


class QueryError(Exception):
    pass


def _is_read_only(sql: str) -> bool:
    """Allow a single SELECT/WITH statement only."""
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:  # block multiple statements
        return False
    if not re.match(r"^\s*(SELECT|WITH)\b", stripped, re.IGNORECASE):
        return False
    if _FORBIDDEN.search(stripped):
        return False
    return True


def run_query(sql: str, max_rows: int = MAX_ROWS) -> dict:
    """Run a read-only query. Returns {columns, rows, row_count, truncated}."""
    if not _is_read_only(sql):
        raise QueryError(
            "Rejected: only a single read-only SELECT/WITH query is allowed."
        )
    with connect(DEFAULT_DATABASE) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        data = [[None if v is None else str(v) for v in row] for row in rows]
    return {
        "columns": columns,
        "rows": data,
        "row_count": len(data),
        "truncated": truncated,
    }


def _read_dbo_columns() -> list[tuple]:
    """Return dbo object/column metadata ordered by object then ordinal."""
    with connect(DEFAULT_DATABASE) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.TABLE_SCHEMA,
                c.TABLE_NAME,
                t.TABLE_TYPE,
                c.ORDINAL_POSITION,
                c.COLUMN_NAME,
                c.DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS c
            JOIN INFORMATION_SCHEMA.TABLES t
                ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
               AND c.TABLE_NAME = t.TABLE_NAME
            WHERE c.TABLE_SCHEMA = 'dbo'
              AND t.TABLE_TYPE IN ('BASE TABLE', 'VIEW')
            ORDER BY
                c.TABLE_SCHEMA,
                c.TABLE_NAME,
                c.ORDINAL_POSITION;
            """
        )
        return cur.fetchall()


def _is_key_like(col: str) -> bool:
    lc = col.lower()
    return (
        lc == "id"
        or lc.endswith("id")
        or lc.endswith("_id")
        or "guid" in lc
    )


def _is_time_like(col: str) -> bool:
    lc = col.lower()
    return any(
        token in lc for token in ("date", "month", "week", "year", "time")
    )


def _is_metric_type(dtype: str) -> bool:
    return dtype.lower() in {
        "bigint",
        "int",
        "smallint",
        "tinyint",
        "decimal",
        "numeric",
        "float",
        "real",
        "money",
        "smallmoney",
    }


def _classify_table(table_name: str, columns: list[tuple]) -> str:
    """Heuristic role label for prompt guidance only."""
    name = table_name.lower()
    col_names = [c[0].lower() for c in columns]
    if "mapping" in name or name.startswith("dim"):
        return "dimension/mapping"
    if "lookup" in name or name.startswith("ref"):
        return "lookup/reference"
    has_metrics = sum(1 for _c, dt in columns if _is_metric_type(dt)) >= 3
    has_time = any(_is_time_like(cn) for cn in col_names)
    if has_metrics and has_time:
        return "fact/time-series"
    if has_metrics:
        return "fact/aggregate"
    return "descriptive"


def get_schema_bundle() -> dict:
    """Return full schema listing + architecture briefing for prompt context."""
    rows = _read_dbo_columns()
    per_table = defaultdict(list)
    types = {}
    for schema, table, table_type, _ord, col, dtype in rows:
        key = f"{schema}.{table}"
        per_table[key].append((col, dtype))
        types[key] = table_type

    schema_lines = []
    for key in sorted(per_table):
        schema_lines.append(f"\n{key}")
        for col, dtype in per_table[key]:
            schema_lines.append(f"  - {col} ({dtype})")
    schema_text = "\n".join(schema_lines).strip()

    # Infer likely join keys by shared ID-like column names.
    col_to_tables = defaultdict(set)
    for key, cols in per_table.items():
        for col, _dtype in cols:
            if _is_key_like(col):
                col_to_tables[col].add(key)
    join_candidates = [
        (col, sorted(tbls))
        for col, tbls in col_to_tables.items()
        if len(tbls) >= 2
    ]
    join_candidates.sort(key=lambda x: (-len(x[1]), x[0].lower()))

    type_counts = defaultdict(int)
    for key, cols in per_table.items():
        role = _classify_table(key.split(".", 1)[1], cols)
        type_counts[role] += 1

    brief = []
    brief.append("Architecture briefing (auto-inferred from INFORMATION_SCHEMA):")
    brief.append(
        f"- Objects in dbo: {len(per_table)} "
        f"({sum(1 for t in types.values() if t == 'BASE TABLE')} tables, "
        f"{sum(1 for t in types.values() if t == 'VIEW')} views)."
    )
    brief.append(
        "- Role mix: "
        + ", ".join(
            f"{role}={count}" for role, count in sorted(type_counts.items())
        )
        + "."
    )

    if join_candidates:
        brief.append("- Likely join keys (shared ID-like columns):")
        for col, tables in join_candidates[:12]:
            sample = ", ".join(tables[:4])
            suffix = " ..." if len(tables) > 4 else ""
            brief.append(
                f"  - {col}: {sample}{suffix} ({len(tables)} objects)"
            )

    brief.append("- Table structure highlights:")
    for key in sorted(per_table)[:40]:
        cols = per_table[key]
        names = [c for c, _ in cols]
        key_cols = [c for c in names if _is_key_like(c)][:4]
        time_cols = [c for c in names if _is_time_like(c)][:4]
        metric_cols = [c for c, dt in cols if _is_metric_type(dt)][:4]
        role = _classify_table(key.split(".", 1)[1], cols)
        brief.append(f"  - {key}: role={role}; columns={len(cols)}")
        if key_cols:
            brief.append(f"    keys: {', '.join(key_cols)}")
        if time_cols:
            brief.append(f"    time: {', '.join(time_cols)}")
        if metric_cols:
            brief.append(f"    measures: {', '.join(metric_cols)}")

    return {
        "architecture": "\n".join(brief),
        "schema": schema_text,
    }


def get_schema_context() -> str:
    """Back-compat helper returning only the full schema listing."""
    return get_schema_bundle()["schema"]


def get_architecture_context() -> str:
    """Return only the architecture/table-structure briefing."""
    return get_schema_bundle()["architecture"]


if __name__ == "__main__":
    bundle = get_schema_bundle()
    print(bundle["architecture"])
    print("\n" + "=" * 80 + "\n")
    print(bundle["schema"])
