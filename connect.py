"""Auth + connection to the Microsoft Fabric SQL endpoint (HelixMCEDU).

Interactive Azure AD sign-in via azure-identity, with a *persistent* token
cache so you only see the browser popup once. The token is handed to ODBC
Driver 18 through SQL_COPT_SS_ACCESS_TOKEN (reliable on macOS).
"""
import struct
import pyodbc
from azure.identity import (
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
)

SERVER = (
    "x6eps4xrq2xudenlfv6naeo3i4-y5zi6fikvfiezh6qzzse7aehui"
    ".msit-datawarehouse.fabric.microsoft.com"
)
DEFAULT_DATABASE = "HelixMCEDU"
TENANT_ID = "72f988bf-86f1-41af-91ab-2d7cd011db47"  # Microsoft corp (MSIT)
LOGIN_HINT = "juedwards@microsoft.com"
SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256

# Reuse one credential per process so its in-memory + persistent cache is hit.
_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = InteractiveBrowserCredential(
            tenant_id=TENANT_ID,
            login_hint=LOGIN_HINT,
            cache_persistence_options=TokenCachePersistenceOptions(
                name="fabricfinder"
            ),
        )
    return _credential


def _token_struct():
    token = _get_credential().get_token(SCOPE).token
    tok_bytes = token.encode("utf-16-le")
    return struct.pack("<i", len(tok_bytes)) + tok_bytes


def connect(database=DEFAULT_DATABASE):
    parts = [
        "Driver={ODBC Driver 18 for SQL Server}",
        f"Server={SERVER},1433",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        "Connection Timeout=60",
    ]
    if database:
        parts.append(f"Database={database}")
    conn_str = ";".join(parts) + ";"
    return pyodbc.connect(
        conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_struct()}
    )


if __name__ == "__main__":
    print("Signing in as", LOGIN_HINT, "...")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT SUSER_SNAME();")
        print("Connected as:", cur.fetchone()[0])
