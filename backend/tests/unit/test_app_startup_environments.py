"""The application must complete startup in every deployable environment.

A staging deploy died on startup because the lifespan read
``app.state.memory_auditor`` under ``not is_production or debug`` while
``create_application`` only sets it under ``is_development``. Staging is
neither, so the attribute never existed. Production survived only because its
own condition happened to be false, and would have crashed too with DEBUG=true.

The lifespan is driven directly rather than through TestClient so the test
needs no Postgres or Redis: a failing database connection is logged and
tolerated by design, which is exactly the path a fresh deploy takes.
"""

import asyncio

import pytest


def _run_startup(app_env: str, debug: bool) -> None:
    """Enter the real lifespan for one environment and leave it again.

    The test process imports ``app.main`` once, and ``create_application()``
    runs at import time under the test environment (development), so the
    auditor already exists on ``app.state``. Simply flipping ``app_env``
    afterwards would NOT reproduce a staging container, where the auditor was
    never created at all. The attribute is therefore removed for every
    non-development case, which is the state a real staging build starts in.
    """
    from app.core.config import settings

    original_env, original_debug = settings.app_env, settings.debug
    settings.app_env, settings.debug = app_env, debug

    from app.main import app, lifespan

    had_auditor = hasattr(app.state, "memory_auditor")
    saved_auditor = getattr(app.state, "memory_auditor", None)
    if not settings.is_development and had_auditor:
        delattr(app.state, "memory_auditor")

    try:

        async def drive() -> None:
            async with lifespan(app):
                pass

        asyncio.run(drive())
    finally:
        if had_auditor and not hasattr(app.state, "memory_auditor"):
            app.state.memory_auditor = saved_auditor
        settings.app_env, settings.debug = original_env, original_debug


@pytest.mark.parametrize("app_env", ["development", "staging", "production"])
@pytest.mark.parametrize("debug", [False, True])
def test_startup_succeeds_in_every_environment(app_env: str, debug: bool) -> None:
    """Startup must not raise, whatever APP_ENV and DEBUG are set to.

    The failing combination was (staging, any) and (production, debug=True).
    """
    _run_startup(app_env, debug)


def test_memory_auditor_exists_only_in_development() -> None:
    """Nothing outside development may assume the auditor was created."""
    from app.core.config import settings
    from app.main import app

    # create_application() already ran at import time under the test env.
    if settings.is_development:
        assert hasattr(app.state, "memory_auditor")
    else:
        assert not hasattr(app.state, "memory_auditor")
