"""Run the embedded Postgres (pgserver) for local development and keep it alive.

The data directory lives in a space-free path (the project path breaks pg_ctl
arguments). TCP on 127.0.0.1:5432 and the `stratum` role/database are configured
once by setup_local_pg.py (persisted in postgresql.auto.conf), so this script only
starts the server; it never calls pgserver's psql helper, which cannot handle the
venv path.
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
print(f"postgres running: {server.get_uri()}", flush=True)
print("tcp: postgresql://stratum:stratum_secure_password_2024@127.0.0.1:5432/stratum_ai", flush=True)


def _stop(*_args) -> None:
    server.cleanup()
    sys.exit(0)


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

while True:
    time.sleep(60)
