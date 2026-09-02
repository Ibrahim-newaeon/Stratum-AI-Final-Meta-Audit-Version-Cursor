"""Run an embedded Postgres (pgserver) on 127.0.0.1:5432 for local development.

Creates role `stratum` and database `stratum_ai` to match the app defaults.
Keeps running until killed.
"""

import signal
import sys
import time
from pathlib import Path

import pgserver

DATA_DIR = Path(
    "/private/tmp/claude-501/-Users-ibrahimabedrabboh-Desktop-Final-Updates-Dec-2025-copy/35a0e022-79ba-406c-b86d-812829334cc7/scratchpad/pgdata"
)
DATA_DIR.mkdir(parents=True, exist_ok=True)

server = pgserver.get_server(DATA_DIR, cleanup_mode=None)
# Listen on TCP 5432 (pgserver defaults to a unix socket).
server.psql("ALTER SYSTEM SET listen_addresses TO '127.0.0.1';")
server.psql("ALTER SYSTEM SET port TO 5432;")

# Create app role/db if missing (idempotent).
out = server.psql("SELECT 1 FROM pg_roles WHERE rolname='stratum';")
if "1" not in out:
    server.psql("CREATE ROLE stratum LOGIN SUPERUSER PASSWORD 'stratum_secure_password_2024';")
out = server.psql("SELECT 1 FROM pg_database WHERE datname='stratum_ai';")
if "1" not in out:
    server.psql("CREATE DATABASE stratum_ai OWNER stratum;")

# Restart so listen_addresses/port take effect.
server.cleanup()
server = pgserver.get_server(DATA_DIR, cleanup_mode=None)
print(f"postgres running, uri={server.get_uri()}", flush=True)
print("tcp: postgresql://stratum:stratum_secure_password_2024@127.0.0.1:5432/stratum_ai", flush=True)


def _stop(*_args):
    server.cleanup()
    sys.exit(0)


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

while True:
    time.sleep(60)
