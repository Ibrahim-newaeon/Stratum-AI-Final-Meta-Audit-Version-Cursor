# =============================================================================
# Stratum AI - Critical Features Unit Tests
# =============================================================================
"""
Unit tests for critical audit items:
1. CAPI connectors with circuit breaker/retry
2. Enhanced ROAS model with creative/audience features
3. Model retraining pipeline
4. Real EMQ measurement service
5. Offline conversion upload service
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_event():
    """Sample CAPI event for testing."""
    return {
        "event_id": "test_123",
        "event_name": "Purchase",
        "event_time": int(datetime.now(UTC).timestamp()),
        "user_data": {
            "email": "test@example.com",
            "phone": "+1234567890",
        },
        "parameters": {
            "value": 99.99,
            "currency": "USD",
        },
    }


@pytest.fixture
def sample_training_data():
    """Sample training data for ML tests."""
    np.random.seed(42)
    n_samples = 500

    data = {
        "campaign_id": [f"camp_{i % 50}" for i in range(n_samples)],
        "date": pd.date_range(start="2024-01-01", periods=n_samples, freq="D"),
        "platform": np.random.choice(["meta"], n_samples),
        "spend": np.random.uniform(100, 5000, n_samples),
        "impressions": np.random.uniform(10000, 500000, n_samples).astype(int),
        "clicks": np.random.uniform(100, 5000, n_samples).astype(int),
        "conversions": np.random.uniform(5, 200, n_samples).astype(int),
        "creative_type": np.random.choice(["image", "video", "carousel"], n_samples),
        "audience_type": np.random.choice(["broad", "lookalike", "retargeting"], n_samples),
        "objective": np.random.choice(["conversions", "traffic", "awareness"], n_samples),
    }

    df = pd.DataFrame(data)
    df["revenue"] = df["conversions"] * np.random.uniform(20, 100, n_samples)

    return df


@pytest.fixture
def sample_offline_conversions():
    """Sample offline conversions for testing."""
    from app.services.offline_conversion_service import OfflineConversion, OfflineConversionSource

    return [
        OfflineConversion(
            conversion_id="offline_001",
            platform="meta",
            email="customer1@example.com",
            phone="+1234567890",
            event_name="Purchase",
            event_time=datetime.now(UTC),
            conversion_value=149.99,
            currency="USD",
            order_id="ORD-001",
            source=OfflineConversionSource.CRM,
        ),
        OfflineConversion(
            conversion_id="offline_002",
            platform="meta",
            email="customer2@example.com",
            event_name="Purchase",
            event_time=datetime.now(UTC) - timedelta(hours=2),
            conversion_value=299.99,
            currency="USD",
            order_id="ORD-002",
            source=OfflineConversionSource.POS,
        ),
    ]


# =============================================================================
# 1. CAPI Connector Tests
# =============================================================================


class TestCircuitBreaker:
    """Tests for circuit breaker implementation."""

    def test_circuit_breaker_starts_closed(self):
        """Circuit breaker should start in closed state."""
        from app.services.capi.platform_connectors import CircuitBreaker, CircuitState

        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() == True

    def test_circuit_opens_after_failures(self):
        """Circuit should open after threshold failures."""
        from app.services.capi.platform_connectors import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=3)

        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() == False

    def test_circuit_resets_on_success(self):
        """Failure count should reset on success."""
        from app.services.capi.platform_connectors import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=5)

        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2

        cb.record_success()
        assert cb.failure_count == 0

    def test_circuit_half_open_after_timeout(self):
        """Circuit should go half-open after recovery timeout."""
        import time

        from app.services.capi.platform_connectors import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(1.1)

        # Should now be allowed to execute (half-open)
        assert cb.can_execute() == True
        assert cb.state == CircuitState.HALF_OPEN


class TestRateLimiter:
    """Tests for rate limiter implementation."""

    def test_rate_limiter_allows_within_limit(self):
        """Should allow requests within limit."""
        from app.services.capi.platform_connectors import RateLimiter

        rl = RateLimiter(max_tokens=10, refill_rate=1.0)

        # Should be able to acquire tokens
        assert rl.acquire(5) == True
        assert rl.acquire(5) == True

    def test_rate_limiter_blocks_over_limit(self):
        """Should block requests over limit."""
        from app.services.capi.platform_connectors import RateLimiter

        rl = RateLimiter(max_tokens=10, refill_rate=0.1)

        # Exhaust tokens
        assert rl.acquire(10) == True
        # Should now fail
        assert rl.acquire(1) == False


class TestCAPIDeliveryPersistence:
    """
    CAPI delivery attempts must be persisted, and attributed to a tenant.

    They used to be appended to a module-level ``_event_delivery_logs`` list in
    platform_connectors: capped at 10000 entries, lost on restart, invisible to
    any other process, and not scoped by tenant at all. Signal health and EMQ
    were computed from it, which is why they were computed from nothing.
    """

    @pytest.mark.asyncio
    async def test_delivery_is_recorded_against_the_tenant(self, monkeypatch):
        """A delivery attempt is written to capi_delivery_logs for its tenant."""
        from app.services.capi import delivery_logger as delivery_logger_module
        from app.services.capi.platform_connectors import (
            CAPIResponse,
            MetaCAPIConnector,
        )

        recorded: list[dict] = []

        class _RecordingLogger:
            async def log_delivery(self, **kwargs):
                recorded.append(kwargs)

        monkeypatch.setattr(
            delivery_logger_module, "get_delivery_logger", lambda: _RecordingLogger()
        )

        connector = MetaCAPIConnector(tenant_id=42)
        response = CAPIResponse(
            success=True,
            events_received=1,
            events_processed=1,
            errors=[],
            platform="meta",
            request_id="req_1",
        )
        await connector._record_delivery(
            {"event_id": "evt_1", "event_name": "Purchase", "user_data": {"em": "hash"}},
            response,
            latency_ms=120.0,
            retry=0,
        )

        assert len(recorded) == 1
        assert recorded[0]["tenant_id"] == 42
        assert recorded[0]["platform"] == "meta"
        assert recorded[0]["event_id"] == "evt_1"
        # Identifiers are recorded as a hash so match quality is measurable
        # without any PII reaching the table.
        assert recorded[0]["user_data_hash"] is not None
        assert "em" not in str(recorded[0]["user_data_hash"])

    @pytest.mark.asyncio
    async def test_delivery_without_a_tenant_is_not_recorded(self, monkeypatch):
        """An unattributable delivery is dropped, never filed under a guess."""
        from app.services.capi import delivery_logger as delivery_logger_module
        from app.services.capi.platform_connectors import (
            CAPIResponse,
            MetaCAPIConnector,
        )

        recorded: list[dict] = []

        class _RecordingLogger:
            async def log_delivery(self, **kwargs):
                recorded.append(kwargs)

        monkeypatch.setattr(
            delivery_logger_module, "get_delivery_logger", lambda: _RecordingLogger()
        )

        connector = MetaCAPIConnector()
        await connector._record_delivery(
            {"event_id": "evt_1", "event_name": "Purchase"},
            CAPIResponse(
                success=True,
                events_received=1,
                events_processed=1,
                errors=[],
                platform="meta",
            ),
            latency_ms=10.0,
            retry=0,
        )

        assert recorded == []

    @pytest.mark.asyncio
    async def test_events_dropped_before_a_send_are_still_recorded(self, monkeypatch):
        """
        A dropped event is a recorded failure, not a gap in the evidence.

        ``send_events`` returned early when the connector was disconnected or
        the circuit breaker was open, without reaching ``_record_delivery``.
        Signal health computes event loss as a share of *recorded* attempts, so
        those drops were invisible: with a 60s recovery timeout a sustained
        outage recorded five failed batches and then nothing at all, and the
        measured success rate recovered while the platform was still refusing
        everything.
        """
        from app.services.capi import delivery_logger as delivery_logger_module
        from app.services.capi.platform_connectors import MetaCAPIConnector

        recorded: list[dict] = []
        flushed: list[int] = []

        class _RecordingLogger:
            async def log_delivery(self, **kwargs):
                recorded.append(kwargs)

            async def flush(self):
                flushed.append(len(recorded))

        monkeypatch.setattr(
            delivery_logger_module, "get_delivery_logger", lambda: _RecordingLogger()
        )

        events = [{"event_id": "a", "event_name": "Purchase"}, {"event_id": "b"}]

        # 1. Not connected.
        connector = MetaCAPIConnector(tenant_id=7)
        response = await connector.send_events(events)
        assert response.success is False
        assert [entry["status"].value for entry in recorded] == ["failed", "failed"]

        # 2. Circuit breaker open.
        recorded.clear()
        connector._connected = True
        for _ in range(connector._circuit_breaker.failure_threshold):
            connector._circuit_breaker.record_failure()

        response = await connector.send_events(events)
        assert response.success is False
        assert [entry["status"].value for entry in recorded] == [
            "circuit_open",
            "circuit_open",
        ]
        # Recorded rows are persisted at the end of the send, not left in a
        # buffer that only flushes from inside a later log_delivery call.
        assert flushed

    def test_only_match_quality_identifiers_count_as_identified(self):
        """
        ``user_data_hash`` reflects real identifiers, and hashes their values.

        It used to hash the field *names* - ``sha256("em|ph")`` - so every event
        with the same key set produced a byte-identical digest that correlated
        nothing, under a comment claiming it hashed the identifiers. It was also
        non-null for any non-empty ``user_data``, including the IP/user-agent
        pair the landing-page path sends on every event, so a tenant sending no
        email, phone or external id scored 100% identifier coverage - 30% of the
        EMQ component.
        """
        from app.services.capi.platform_connectors import _match_quality_hash

        assert _match_quality_hash(None) is None
        assert _match_quality_hash({}) is None
        assert (
            _match_quality_hash(
                {"client_ip_address": "1.2.3.4", "client_user_agent": "Mozilla"}
            )
            is None
        )

        one = _match_quality_hash({"em": "a@example.com"})
        two = _match_quality_hash({"em": "b@example.com"})
        assert one is not None and two is not None
        # Different identifiers hash differently: the digest is of the values,
        # not of the key set.
        assert one != two
        # And no PII survives into the column.
        assert "example.com" not in one

    def test_delivery_logger_buffers_per_instance(self):
        """
        The buffer is per-instance, not shared by every DeliveryLogger.

        ``_buffer`` and ``_last_flush`` were class attributes, so two loggers
        shared one list - harmless for the module singleton, a silent
        cross-instance leak for anyone who constructed a second one.
        """
        from app.services.capi.delivery_logger import DeliveryLogger

        first, second = DeliveryLogger(), DeliveryLogger()
        first._buffer.append(object())

        assert second._buffer == []

    def test_connector_health_is_unknown_without_delivery_attempts(self):
        """No delivery attempts to judge means unknown, not 100% healthy."""
        from app.services.capi.platform_connectors import (
            ConnectorHealthMonitor,
            MetaCAPIConnector,
        )

        health = ConnectorHealthMonitor().check_health(MetaCAPIConnector())

        assert health.status == "unknown"
        assert health.success_rate_1h is None


class TestMetaCAPIConnector:
    """Tests for Meta CAPI connector."""

    @pytest.mark.asyncio
    async def test_connect_validates_credentials(self):
        """Should validate credentials on connect."""
        from app.services.capi.platform_connectors import ConnectionStatus, MetaCAPIConnector

        connector = MetaCAPIConnector()

        # Missing credentials
        result = await connector.connect({})
        assert result.status == ConnectionStatus.ERROR
        assert "Missing" in result.message

    @pytest.mark.asyncio
    async def test_send_events_requires_connection(self):
        """Should fail if not connected."""
        from app.services.capi.platform_connectors import MetaCAPIConnector

        connector = MetaCAPIConnector()

        result = await connector.send_events([{"event_name": "test"}])
        assert result.success == False
        assert "Not connected" in result.errors[0]["message"]


# =============================================================================
# 2. Enhanced ML Training Tests
# =============================================================================


class TestEnhancedMLTraining:
    """Tests for enhanced ROAS model with creative/audience features."""

    def test_prepare_data_creates_creative_features(self, sample_training_data):
        """Should create creative type features."""
        from app.ml.train import ModelTrainer

        trainer = ModelTrainer()
        prepared = trainer._prepare_data(sample_training_data)

        # Check creative features exist
        assert "creative_image" in prepared.columns
        assert "creative_video" in prepared.columns
        assert "creative_carousel" in prepared.columns

    def test_prepare_data_creates_audience_features(self, sample_training_data):
        """Should create audience type features."""
        from app.ml.train import ModelTrainer

        trainer = ModelTrainer()
        prepared = trainer._prepare_data(sample_training_data)

        # Check audience features exist
        assert "audience_broad" in prepared.columns
        assert "audience_lookalike" in prepared.columns
        assert "audience_retargeting" in prepared.columns

    def test_prepare_data_creates_objective_features(self, sample_training_data):
        """Should create objective features."""
        from app.ml.train import ModelTrainer

        trainer = ModelTrainer()
        prepared = trainer._prepare_data(sample_training_data)

        # Check objective features exist
        assert "objective_conversions" in prepared.columns
        assert "objective_traffic" in prepared.columns

    def test_prepare_data_creates_platform_features(self, sample_training_data):
        """Should create platform one-hot features."""
        from app.ml.train import ModelTrainer

        trainer = ModelTrainer()
        prepared = trainer._prepare_data(sample_training_data)

        # Check platform features exist
        assert "platform_meta" in prepared.columns

    def test_prepare_data_calculates_derived_metrics(self, sample_training_data):
        """Should calculate CTR, CVR, ROAS, etc."""
        from app.ml.train import ModelTrainer

        trainer = ModelTrainer()
        prepared = trainer._prepare_data(sample_training_data)

        # Check derived metrics
        assert "ctr" in prepared.columns
        assert "cvr" in prepared.columns
        assert "roas" in prepared.columns
        assert "cpm" in prepared.columns
        assert "cpc" in prepared.columns

    def test_prepare_data_creates_log_transforms(self, sample_training_data):
        """Should create log-transformed features."""
        from app.ml.train import ModelTrainer

        trainer = ModelTrainer()
        prepared = trainer._prepare_data(sample_training_data)

        assert "log_spend" in prepared.columns
        assert "log_impressions" in prepared.columns
        assert "log_clicks" in prepared.columns

    def test_train_roas_predictor_returns_metrics(self, sample_training_data):
        """ROAS predictor training should return metrics."""
        import tempfile

        from app.ml.train import ModelTrainer

        with tempfile.TemporaryDirectory() as tmpdir:
            trainer = ModelTrainer(models_path=tmpdir)
            prepared = trainer._prepare_data(sample_training_data)

            metrics = trainer.train_roas_predictor(prepared)

            assert "r2" in metrics
            assert "mae" in metrics
            assert "rmse" in metrics
            assert metrics["r2"] >= -1  # R2 can be negative for bad fits
            assert metrics["num_features"] > 5  # Should have multiple features


# =============================================================================
# 3. Retraining Pipeline Tests
# =============================================================================


class TestRetrainingPipeline:
    """Tests for model retraining pipeline."""

    def test_retraining_config_defaults(self):
        """Should have sensible default config."""
        from app.ml.retraining_pipeline import RetrainingConfig

        config = RetrainingConfig()

        assert config.retrain_interval_days == 7
        assert config.min_samples_for_retrain == 1000
        assert config.min_r2_improvement == 0.02

    def test_check_retraining_needed_new_model(self):
        """Should need retraining if model doesn't exist."""
        import tempfile

        from app.ml.retraining_pipeline import RetrainingPipeline, RetrainingTrigger

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = RetrainingPipeline(
                config=type(
                    "Config",
                    (),
                    {
                        "models_path": tmpdir,
                        "archive_path": f"{tmpdir}/archive",
                        "staging_path": f"{tmpdir}/staging",
                        "retrain_interval_days": 7,
                        "min_samples_for_retrain": 1000,
                        "min_r2_improvement": 0.02,
                        "max_r2_degradation": 0.05,
                        "drift_detection_window_days": 7,
                        "max_model_versions": 5,
                        "staging_validation_hours": 24,
                    },
                )()
            )

            needs, trigger, reason = pipeline.check_retraining_needed("nonexistent_model")

            assert needs == True
            assert trigger == RetrainingTrigger.MANUAL
            assert "does not exist" in reason

    def test_model_version_tracking(self):
        """Should track model versions in history."""
        from app.ml.retraining_pipeline import ModelStatus, ModelVersion, RetrainingTrigger

        version = ModelVersion(
            version_id="20240101_120000",
            model_name="roas_predictor",
            created_at=datetime.now(UTC),
            metrics={"r2": 0.75, "mae": 0.5},
            status=ModelStatus.ACTIVE,
            trigger=RetrainingTrigger.SCHEDULED,
            training_samples=10000,
            features=["log_spend", "ctr", "cvr"],
            path="./models/roas_predictor.pkl",
        )

        assert version.version_id == "20240101_120000"
        assert version.status == ModelStatus.ACTIVE


# =============================================================================
# 5. Offline Conversion Upload Tests
# =============================================================================


class TestOfflineConversionService:
    """Tests for offline conversion upload service."""

    def test_parse_csv_basic(self):
        """Should parse basic CSV content."""
        from app.services.offline_conversion_service import OfflineConversionService

        service = OfflineConversionService()

        csv_content = """email,phone,value,currency,event_time
test1@example.com,+1234567890,99.99,USD,2024-01-15
test2@example.com,+0987654321,149.99,USD,2024-01-16"""

        conversions = service.parse_csv(csv_content, "meta")

        assert len(conversions) == 2
        assert conversions[0].email == "test1@example.com"
        assert conversions[0].conversion_value == 99.99

    def test_parse_csv_with_mapping(self):
        """Should parse CSV with custom column mapping."""
        from app.services.offline_conversion_service import OfflineConversionService

        service = OfflineConversionService()

        csv_content = """customer_email,mobile_number,order_total
test@example.com,+1234567890,199.99"""

        mapping = {
            "email": ["customer_email"],
            "phone": ["mobile_number"],
            "conversion_value": ["order_total"],
        }

        conversions = service.parse_csv(csv_content, "meta", column_mapping=mapping)

        assert len(conversions) == 1
        assert conversions[0].email == "test@example.com"
        assert conversions[0].conversion_value == 199.99

    def test_parse_csv_skips_invalid_rows(self):
        """Should skip rows without identifiers."""
        from app.services.offline_conversion_service import OfflineConversionService

        service = OfflineConversionService()

        csv_content = """email,phone,value
test@example.com,,99.99
,,49.99
,+1234567890,149.99"""

        conversions = service.parse_csv(csv_content, "meta")

        # First and third rows should be valid (have email or phone)
        assert len(conversions) == 2

    def test_meta_uploader_format_conversion(self, sample_offline_conversions):
        """Should format conversion for Meta API."""
        from app.services.offline_conversion_service import MetaOfflineUploader

        uploader = MetaOfflineUploader()
        conv = sample_offline_conversions[0]

        formatted = uploader._format_conversion(conv)

        assert "match_keys" in formatted
        assert "event_name" in formatted
        assert "event_time" in formatted
        assert "value" in formatted
        assert formatted["event_name"] == "Purchase"

    def test_upload_history_tracking(self, sample_offline_conversions):
        """Should track upload history."""
        from app.services.offline_conversion_service import OfflineConversionService

        service = OfflineConversionService()

        # Get history (may be empty or have previous test data)
        history = service.get_upload_history(platform="meta", limit=10)

        assert isinstance(history, list)

    def test_batch_status_lookup(self):
        """Should return None for unknown batch."""
        from app.services.offline_conversion_service import OfflineConversionService

        service = OfflineConversionService()

        status = service.get_batch_status("nonexistent_batch_123")

        assert status is None


# =============================================================================
# Integration Tests
# =============================================================================


class TestCriticalFeaturesIntegration:
    """Integration tests for critical features."""

    def test_ml_training_end_to_end(self, sample_training_data):
        """End-to-end ML training test."""
        import tempfile
        from pathlib import Path

        from app.ml.train import ModelTrainer

        with tempfile.TemporaryDirectory() as tmpdir:
            trainer = ModelTrainer(models_path=tmpdir)
            tmpdir_path = Path(tmpdir)

            # Train all models
            results = trainer.train_all(sample_training_data, include_platform_models=False)

            assert "roas_predictor" in results
            assert "conversion_predictor" in results
            assert "budget_impact" in results

            # Check files were created
            assert (tmpdir_path / "roas_predictor.pkl").exists()
            assert (tmpdir_path / "roas_predictor_metadata.json").exists()


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
