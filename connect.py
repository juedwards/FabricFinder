"""Auth + connection to the Microsoft Fabric SQL endpoint (HelixMCEDU).

Uses interactive-browser Azure AD sign-in (azure-identity). On a managed /
compliant device this satisfies the org's device-compliance Conditional Access
policy, because the token is issued *and* used on that device. (Device-code /
container auth is blocked by that policy — see README.)

Each user signs in as themselves; the token is cached so later runs are silent.
The token is handed to ODBC Driver 18 via SQL_COPT_SS_ACCESS_TOKEN.
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
SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256

_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = InteractiveBrowserCredential(
            tenant_id=TENANT_ID,
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
    print("Signing in (browser) ...")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT SUSER_SNAME();")
        print("Connected as:", cur.fetchone()[0])
