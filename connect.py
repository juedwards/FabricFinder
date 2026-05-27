"""Auth + connection to the Microsoft Fabric SQL endpoint (HelixMCEDU).

Uses device-code Azure AD sign-in (azure-identity) so it works headless / in a
container: on first use it prints a URL + code; you sign in as yourself on any
device. The token is cached (persisted under $HOME) so later runs are silent.
Each user authenticates as themselves, so queries run under their own access.

The token is handed to ODBC Driver 18 via SQL_COPT_SS_ACCESS_TOKEN.
"""
import os
import struct

import pyodbc
from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions

SERVER = (
    "x6eps4xrq2xudenlfv6naeo3i4-y5zi6fikvfiezh6qzzse7aehui"
    ".msit-datawarehouse.fabric.microsoft.com"
)
DEFAULT_DATABASE = "HelixMCEDU"
TENANT_ID = "72f988bf-86f1-41af-91ab-2d7cd011db47"  # Microsoft corp (MSIT)
# Azure CLI public client id: a first-party public client pre-authorized for
# the SQL scope, so device-code sign-in needs no app registration / consent.
CLIENT_ID = os.environ.get(
    "FABRICFINDER_CLIENT_ID", "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
)
SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256

_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = DeviceCodeCredential(
            client_id=CLIENT_ID,
            tenant_id=TENANT_ID,
            cache_persistence_options=TokenCachePersistenceOptions(
                name="fabricfinder",
                # No system keyring in a slim container; allow file cache.
                allow_unencrypted_storage=True,
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
    print("Signing in (device code) ...")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT SUSER_SNAME();")
        print("Connected as:", cur.fetchone()[0])
