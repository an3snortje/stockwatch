"""MS SQL connectivity for the iSync database (read-only usage)."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import sqlalchemy as sa
from dotenv import find_dotenv, load_dotenv


def _find_env(start: Path | None = None) -> str | None:
    """Locate `.env` independent of the current working directory.

    A scheduled task runs with CWD = C:\\Windows\\System32, where the default
    upward-from-CWD search finds nothing. Fall back to walking up from the
    installed module (editable installs keep the source in the checkout, so the
    project root — and its `.env` — is above this file).
    """
    cwd_hit = find_dotenv(usecwd=True)
    if cwd_hit:
        return cwd_hit
    base = start or Path(__file__).resolve()
    for parent in [base, *base.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            return str(candidate)
    return None


def get_engine() -> sa.Engine:
    env = _find_env()
    if env:
        load_dotenv(env)
    server = os.environ["MSSQL_SERVER"]
    port = os.environ.get("MSSQL_PORT", "1433")
    database = os.environ["MSSQL_DATABASE"]
    username = os.environ["MSSQL_USERNAME"]
    password = os.environ["MSSQL_PASSWORD"]
    driver = os.environ.get("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    encrypt = os.environ.get("MSSQL_ENCRYPT", "no")
    trust_cert = os.environ.get("MSSQL_TRUST_SERVER_CERTIFICATE", "yes")

    odbc = (
        f"DRIVER={{{driver}}};SERVER={server},{port};DATABASE={database};"
        f"UID={username};PWD={password};Encrypt={encrypt};"
        f"TrustServerCertificate={trust_cert};ApplicationIntent=ReadOnly"
    )
    return sa.create_engine(
        f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}",
        pool_pre_ping=True,
    )


def read_sql(engine: sa.Engine, sql: str, params: dict | None = None) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(sa.text(sql), conn, params=params)
