"""MS SQL connectivity for the iSync database (read-only usage)."""

from __future__ import annotations

import os
from urllib.parse import quote_plus

import pandas as pd
import sqlalchemy as sa
from dotenv import load_dotenv


def get_engine() -> sa.Engine:
    load_dotenv()
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
