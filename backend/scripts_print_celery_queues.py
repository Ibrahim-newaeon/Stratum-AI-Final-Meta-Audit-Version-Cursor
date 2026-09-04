#!/usr/bin/env python
# =============================================================================
# Stratum AI - Print the Celery queue list for `celery worker -Q`
# =============================================================================
"""Print the queues a Celery worker must consume, as a comma-separated list.

A Celery worker only consumes the queues it is started with, and every task in
this app is routed to a named queue. ``docker-entrypoint.sh`` therefore builds
its ``--queues`` argument from this script rather than from a hand-maintained
literal, so a newly routed queue cannot silently go unconsumed.

The whole point is that **stdout carries the queue list and nothing else**.
Importing ``app.workers.celery_app`` configures structlog, which is wired to
``structlog.PrintLoggerFactory()`` and a ``logging.StreamHandler(sys.stdout)``
(see ``app/core/logging.py``), and connecting the Celery memory hooks logs
``celery_memory_hooks_connected`` at import time. Without the guard below that
line ends up glued to the first queue name, Celery splits the result on commas
and the ``cdp`` queue is never consumed. So the import happens with stdout
swapped for stderr, the captured log output is replayed to stderr where it
belongs, and the result is validated before it is written.

Usage::

    python scripts_print_celery_queues.py   # -> cdp,celery,default,intel,ml,rules,sync

Exits non-zero (writing nothing to stdout) if the import fails or the derived
list does not look like a list of queue names, so that ``set -e`` in the
entrypoint stops the container instead of starting a worker on a bogus queue.
"""

import contextlib
import io
import re
import sys

# Queue names are used verbatim in a shell command line and as AMQP routing
# keys: letters, digits and the few separators Celery itself allows.
QUEUE_LIST_RE = re.compile(r"[a-z0-9_.-]+(?:,[a-z0-9_.-]+)*\Z", re.IGNORECASE)


def render_queue_list() -> str:
    """Return the comma-separated queue list, free of any import-time output.

    Returns:
        The queues from ``app.workers.celery_app.CELERY_QUEUES`` joined with
        commas, ready to hand to ``celery worker --queues``.

    Raises:
        ValueError: If the derived list is empty or contains anything that is
            not a queue name (which would mean log output leaked into it).
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        from app.workers.celery_app import CELERY_QUEUES

        # Flush any logging handler that bound to the redirected stream while
        # it was installed, so nothing is emitted after the swap is undone.
        sys.stdout.flush()

    # Import-time logging is real output; keep it, just not on stdout.
    noise = captured.getvalue()
    if noise:
        sys.stderr.write(noise)

    queue_list = ",".join(CELERY_QUEUES)
    if not queue_list or not QUEUE_LIST_RE.fullmatch(queue_list):
        raise ValueError(
            f"derived queue list is not a list of queue names: {queue_list!r}"
        )
    return queue_list


def main() -> int:
    """Write the queue list to stdout with no trailing newline.

    Returns:
        0 on success, 1 if the queue list could not be derived.
    """
    try:
        queue_list = render_queue_list()
    except Exception as exc:  # noqa: BLE001 - the entrypoint only needs the message
        sys.stderr.write(f"failed to derive the Celery queue list: {exc}\n")
        return 1

    sys.stdout.write(queue_list)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
