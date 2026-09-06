# =============================================================================
# Stratum AI - Report Delivery Retry Tests
# =============================================================================
"""
Regression test for retrying a delivery whose execution row is gone.

``DeliveryService.retry_delivery`` looked its execution up with
``db.get(ReportExecution, ...)`` - which returns ``None`` for a row that is not
there - and dereferenced it on the very next line. Retrying a delivery whose
execution had been deleted therefore raised ``AttributeError: 'NoneType' object
has no attribute 'schedule_id'`` from inside the retry path, rather than the
``ValueError`` this method raises for every other thing it cannot find.

The neighbouring lookups were both guarded (``if not delivery`` two lines above,
``if schedule`` four lines below), so this was an oversight rather than a
decision - which is also why the fix tolerates the absence rather than raising:
the channel config is an enrichment, and an empty one is exactly what a missing
*schedule* already falls back to.
"""

from types import SimpleNamespace

import pytest


class _Db:
    """Async session stand-in that answers ``get`` from a fixed table."""

    def __init__(self, rows: dict):
        """Seed the (model, pk) -> row mapping; anything absent returns None."""
        self.rows = rows

    async def get(self, model, pk):
        """Return the seeded row, or None as SQLAlchemy would."""
        return self.rows.get((model.__name__, pk))

    async def commit(self):
        """No-op commit."""

    async def flush(self):
        """No-op flush."""

    def add(self, _instance):
        """No-op add."""


def _delivery_service(rows: dict):
    """A DeliveryService bound to the stub session, without running __init__."""
    from app.services.reporting.delivery import DeliveryService

    service = DeliveryService.__new__(DeliveryService)
    service.db = _Db(rows)
    service.tenant_id = 1
    return service


def _failed_delivery():
    """A delivery row in the only state retry accepts."""
    from app.models.reporting import DeliveryStatus

    return SimpleNamespace(
        id=7,
        tenant_id=1,
        status=DeliveryStatus.FAILED,
        execution_id=99,
        channel=SimpleNamespace(value="email"),
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_retrying_a_delivery_whose_execution_is_gone_does_not_crash():
    """
    A missing execution row is tolerated, not dereferenced.

    Before the fix this raised AttributeError on ``execution.schedule_id``.
    Whatever the retry then does with an empty channel config, it must not be
    that - the failure mode being fixed is the type of the exception, and where
    it comes from.
    """
    from app.models.reporting import ReportDelivery

    service = _delivery_service({("ReportDelivery", 7): _failed_delivery()})
    assert await service.db.get(ReportDelivery, 7) is not None
    # The execution is deliberately absent from the table.
    from app.models.reporting import ReportExecution

    assert await service.db.get(ReportExecution, 99) is None

    with pytest.raises(Exception) as excinfo:
        await service.retry_delivery(7)

    assert not isinstance(
        excinfo.value, AttributeError
    ), f"regressed to dereferencing a missing execution: {excinfo.value!r}"


@pytest.mark.asyncio
async def test_a_missing_delivery_still_raises_value_error():
    """The guard that was already there is unchanged."""
    service = _delivery_service({})

    with pytest.raises(ValueError, match="Delivery not found"):
        await service.retry_delivery(7)


@pytest.mark.asyncio
async def test_a_delivery_that_did_not_fail_is_refused():
    """Retry remains restricted to failed deliveries."""
    from app.models.reporting import DeliveryStatus

    delivery = _failed_delivery()
    delivery.status = DeliveryStatus.SENT
    service = _delivery_service({("ReportDelivery", 7): delivery})

    with pytest.raises(ValueError, match="Can only retry failed"):
        await service.retry_delivery(7)


def test_the_execution_lookup_is_guarded_in_source():
    """
    The guard is present at the call site.

    Pinned as source because the runtime test above can only prove the
    exception is no longer AttributeError; this states the shape of the fix.
    """
    import inspect

    from app.services.reporting.delivery import DeliveryService

    source = inspect.getsource(DeliveryService.retry_delivery)

    assert "execution is not None and execution.schedule_id" in source
