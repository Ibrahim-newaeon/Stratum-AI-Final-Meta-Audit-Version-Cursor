# =============================================================================
# Stratum AI - Worker Queue Coverage Guard
# =============================================================================
"""
Regression guard for the "worker executes nothing" defect.

Every task in this app is routed to a named queue, and a Celery worker only
consumes the queues it is started with. The worker used to be started with no
``-Q`` flag at all, so it drained only Celery's built-in "celery" queue while
beat published to sync/rules/intel/ml/cdp/default. Beat logged 500 "Sending due
task" lines and the worker received none of them: the signal health rollup, the
GA4 pull, the attribution variance rollup, campaign sync, rule evaluation and
every ML task have never run in any environment.

These tests fail if a queue named by ``task_routes`` or ``beat_schedule`` is
missing from the list the worker is actually started with - in the entrypoint,
in any compose file, or in the deployment guide.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.workers.celery_app import (
    BEAT_SCHEDULE,
    CELERY_BUILTIN_QUEUE,
    CELERY_QUEUES,
    DEFAULT_QUEUE,
    TASK_ROUTES,
    celery_app,
    collect_queue_names,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = BACKEND_ROOT / "docker-entrypoint.sh"
QUEUE_SCRIPT = BACKEND_ROOT / "scripts_print_celery_queues.py"

# Every file that starts a Celery worker with a literal command line. The
# entrypoint derives its list from CELERY_QUEUES at run time and is checked
# separately.
WORKER_COMMAND_FILES = [
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.prod.yml",
    REPO_ROOT / "editions/starter/docker-compose.yml",
    REPO_ROOT / "editions/professional/docker-compose.yml",
    REPO_ROOT / "editions/enterprise/docker-compose.yml",
    REPO_ROOT / "SERVER_DEPLOYMENT_GUIDE.md",
]

# Matches a worker command, including one wrapped over several lines with
# trailing backslashes (as in docker-entrypoint.sh).
WORKER_COMMAND_RE = re.compile(
    r"celery\s+-A\s+app\.workers\.celery_app\s+worker\b(?:[^\n\\]|\\\n)*"
)
QUEUE_FLAG_RE = re.compile(r"(?:-Q|--queues)[= ]([A-Za-z0-9_,.-]+)")


def _run_queue_script() -> subprocess.CompletedProcess:
    """Run the entrypoint's queue derivation exactly as the container does.

    Returns:
        The completed process, with ``stdout`` stripped of a trailing newline
        only if one is present (the script is expected not to emit one).
    """
    env = {**os.environ, "APP_ENV": os.environ.get("APP_ENV", "development")}
    return subprocess.run(
        [sys.executable, str(QUEUE_SCRIPT)],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _routed_queues() -> set[str]:
    """Every queue named by a routing rule."""
    return {route["queue"] for route in TASK_ROUTES.values() if route.get("queue")}


def _scheduled_queues() -> set[str]:
    """Every queue named in a beat entry's options."""
    return {
        entry["options"]["queue"]
        for entry in BEAT_SCHEDULE.values()
        if (entry.get("options") or {}).get("queue")
    }


class TestDerivedQueueList:
    """CELERY_QUEUES must cover everything work can be published to."""

    def test_every_routed_queue_is_consumed(self):
        """A task routed to a queue nobody consumes would never execute."""
        missing = _routed_queues() - set(CELERY_QUEUES)
        assert not missing, f"task_routes publish to unconsumed queues: {sorted(missing)}"

    def test_every_scheduled_queue_is_consumed(self):
        """A beat entry aimed at an unconsumed queue would never execute."""
        missing = _scheduled_queues() - set(CELERY_QUEUES)
        assert not missing, f"beat_schedule publishes to unconsumed queues: {sorted(missing)}"

    def test_default_and_builtin_queues_are_consumed(self):
        """Unrouted tasks and Celery's own default queue must be drained too."""
        assert DEFAULT_QUEUE in CELERY_QUEUES
        assert CELERY_BUILTIN_QUEUE in CELERY_QUEUES

    def test_celery_app_is_configured_from_the_same_tables(self):
        """The live Celery config must be the tables the list is derived from."""
        assert celery_app.conf.task_routes == TASK_ROUTES
        assert celery_app.conf.beat_schedule == BEAT_SCHEDULE
        assert celery_app.conf.task_default_queue == DEFAULT_QUEUE
        assert celery_app.conf.task_default_queue in CELERY_QUEUES

    def test_queue_list_is_sorted_and_deduplicated(self):
        """The list is used verbatim in shell commands; keep it stable."""
        assert list(CELERY_QUEUES) == sorted(set(CELERY_QUEUES))

    def test_collector_picks_up_a_newly_added_queue(self):
        """The derivation is real: a new route changes the derived list."""
        derived = collect_queue_names(
            {"app.workers.tasks.brand_new.task": {"queue": "brand_new"}},
            {"brand-new-beat": {"options": {"queue": "another_new"}}},
        )
        assert "brand_new" in derived
        assert "another_new" in derived
        assert DEFAULT_QUEUE in derived
        assert CELERY_BUILTIN_QUEUE in derived


class TestWorkerStartCommands:
    """Every place that starts a worker must consume the full queue list."""

    def test_entrypoint_derives_the_queue_list_at_runtime(self):
        """The container entrypoint must pass -Q built from CELERY_QUEUES."""
        text = ENTRYPOINT.read_text()
        assert QUEUE_SCRIPT.name in text, (
            f"docker-entrypoint.sh must derive its queue list with {QUEUE_SCRIPT.name}"
        )
        worker_commands = WORKER_COMMAND_RE.findall(text)
        assert worker_commands, "no celery worker command found in docker-entrypoint.sh"
        for command in worker_commands:
            assert "--queues" in command or "-Q" in command, (
                f"worker started without a queue list: {command}"
            )

    def test_queue_script_prints_the_queue_list_and_nothing_else(self):
        """The derivation's stdout must be the queue list alone.

        Importing the Celery app logs to stdout (structlog PrintLoggerFactory
        plus a StreamHandler on sys.stdout), so an inline ``python -c`` glued
        ``celery_memory_hooks_connected`` onto the first queue name. Celery
        splits ``--queues`` on commas, which silently dropped ``cdp``. Checking
        the file text cannot catch that; only running it can.
        """
        proc = _run_queue_script()

        assert proc.returncode == 0, f"queue script failed: {proc.stderr}"
        assert proc.stdout == ",".join(CELERY_QUEUES), (
            "stdout must be exactly the queue list, with no log output and no "
            f"trailing newline; got {proc.stdout!r}"
        )
        assert "\n" not in proc.stdout, f"stdout contains a newline: {proc.stdout!r}"
        assert re.fullmatch(r"[a-z0-9_.,-]+", proc.stdout), (
            f"stdout is not a bare queue list: {proc.stdout!r}"
        )

    def test_entrypoint_starts_the_worker_on_every_queue(self, tmp_path):
        """End-to-end: run the entrypoint's worker branch and inspect argv.

        This covers the shell capture and the guard as well as the derivation,
        so a regression anywhere along that path fails here rather than in
        production. ``celery`` and ``python`` are shimmed onto PATH so nothing
        is actually started.
        """
        argv_dump = tmp_path / "argv.txt"
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()

        celery_shim = fake_bin / "celery"
        celery_shim.write_text(
            "#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done > "
            f'"{argv_dump}"\n'
        )
        celery_shim.chmod(0o755)

        python_shim = fake_bin / "python"
        python_shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        python_shim.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "SERVICE_ROLE": "worker",
            "APP_ENV": os.environ.get("APP_ENV", "development"),
        }
        proc = subprocess.run(
            ["sh", str(ENTRYPOINT)],
            cwd=BACKEND_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

        assert proc.returncode == 0, (
            f"entrypoint worker branch failed: {proc.stdout}\n{proc.stderr}"
        )
        assert argv_dump.exists(), "the entrypoint never reached the celery command"

        argv = argv_dump.read_text().splitlines()
        assert "--queues" in argv or "-Q" in argv, f"worker started without -Q: {argv}"
        flag = "--queues" if "--queues" in argv else "-Q"
        consumed = argv[argv.index(flag) + 1]

        # Celery splits the value on commas (celery.utils.text.str_to_list).
        declared = {q for q in consumed.split(",") if q}
        missing = set(CELERY_QUEUES) - declared
        assert not missing, (
            f"the containerised worker never consumes {sorted(missing)}; "
            f"it was started with -Q {consumed!r}"
        )
        assert declared == set(CELERY_QUEUES), (
            f"worker consumes unexpected queues: {sorted(declared ^ set(CELERY_QUEUES))}"
        )

    @pytest.mark.parametrize(
        "path", WORKER_COMMAND_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT))
    )
    def test_literal_worker_commands_consume_every_queue(self, path: Path):
        """Compose files and docs spell the list out; it must stay complete."""
        assert path.exists(), f"{path} is missing"
        text = path.read_text()

        commands = WORKER_COMMAND_RE.findall(text)
        assert commands, f"no celery worker command found in {path}"

        for command in commands:
            match = QUEUE_FLAG_RE.search(command)
            assert match, f"worker started without -Q in {path}: {command.strip()}"
            declared = {q for q in match.group(1).split(",") if q}
            missing = set(CELERY_QUEUES) - declared
            assert not missing, (
                f"{path} starts a worker missing queues {sorted(missing)}; "
                f"expected -Q {','.join(CELERY_QUEUES)}"
            )
