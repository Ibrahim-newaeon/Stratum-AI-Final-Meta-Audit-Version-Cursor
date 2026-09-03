"""Configure the running embedded Postgres: TCP on 5432, app role + database."""

import subprocess
import sys
from pathlib import Path

import psycopg2

SOCKET_DIR = str(sorted((Path.home() / "Library/Caches/TemporaryItems/python_PostgresServer").glob("*"), key=lambda p: p.stat().st_mtime)[-1])
PGDATA = "/private/tmp/claude-501/-Users-ibrahimabedrabboh-Desktop-Final-Updates-Dec-2025-copy/35a0e022-79ba-406c-b86d-812829334cc7/scratchpad/pgdata"
PG_CTL = str(Path(__file__).parent / ".venv/lib/python3.12/site-packages/pgserver/pginstall/bin/pg_ctl")


def sql(query: str, autocommit: bool = True, dbname: str = "postgres") -> list:
    conn = psycopg2.connect(host=SOCKET_DIR, dbname=dbname, user="postgres")
    conn.autocommit = autocommit
    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall() if cur.description else []
    cur.close()
    conn.close()
    return rows


sql("ALTER SYSTEM SET listen_addresses TO '127.0.0.1'")
sql("ALTER SYSTEM SET port TO 5432")

if not sql("SELECT 1 FROM pg_roles WHERE rolname='stratum'"):
    sql("CREATE ROLE stratum LOGIN SUPERUSER PASSWORD 'stratum_secure_password_2024'")
if not sql("SELECT 1 FROM pg_database WHERE datname='stratum_ai'"):
    sql("CREATE DATABASE stratum_ai OWNER stratum")

subprocess.run(
    [PG_CTL, "-D", PGDATA, "-w", "-o", f"-k {SOCKET_DIR}", "restart"],
    check=True,
)

# Verify TCP connectivity.
conn = psycopg2.connect(
    host="127.0.0.1", port=5432, dbname="stratum_ai", user="stratum",
    password="stratum_secure_password_2024",
)
conn.close()
print("POSTGRES READY on 127.0.0.1:5432 (db=stratum_ai, user=stratum)")
sys.exit(0)
