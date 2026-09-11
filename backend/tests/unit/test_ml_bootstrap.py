# =============================================================================
# ML model bootstrap — sample .pkl artifacts for local inference
# =============================================================================
"""Regression tests for ``scripts_bootstrap_ml_models`` path + train/load loop."""

from pathlib import Path

import pytest

from scripts_bootstrap_ml_models import CORE_MODELS, bootstrap, core_artifacts_present

pytestmark = pytest.mark.unit


def test_core_artifacts_present_false_when_empty(tmp_path: Path):
    assert core_artifacts_present(tmp_path) is False


def test_bootstrap_skips_when_artifacts_exist(tmp_path: Path):
    for name in CORE_MODELS:
        (tmp_path / f"{name}.pkl").write_bytes(b"placeholder")

    result = bootstrap(models_dir=tmp_path, force=False)
    assert result["skipped"] is True
    # Placeholders must not be overwritten when skipping.
    for name in CORE_MODELS:
        assert (tmp_path / f"{name}.pkl").read_bytes() == b"placeholder"


@pytest.mark.asyncio
async def test_bootstrap_trains_and_inference_loads(tmp_path: Path):
    """End-to-end: sample train writes .pkl files LocalInferenceStrategy can load."""
    result = bootstrap(
        models_dir=tmp_path,
        campaigns=20,
        days=8,
        force=True,
        include_platform_models=False,
    )
    assert result["skipped"] is False
    for name in CORE_MODELS:
        assert (tmp_path / f"{name}.pkl").exists()
        assert (tmp_path / f"{name}_metadata.json").exists()

    from app.ml.inference import LocalInferenceStrategy

    strategy = LocalInferenceStrategy(models_path=str(tmp_path))
    prediction = await strategy.predict(
        "roas_predictor",
        {
            "log_spend": 5.0,
            "log_impressions": 8.0,
            "log_clicks": 4.0,
            "ctr": 1.2,
            "cpc": 0.4,
            "platform_meta": 1,
        },
    )
    assert prediction["inference_strategy"] == "local"
    assert isinstance(prediction["value"], float)


def test_model_trainer_defaults_to_settings_ml_models_path():
    from app.core.config import settings
    from app.ml.train import ModelTrainer

    trainer = ModelTrainer()
    assert Path(trainer.models_path) == Path(settings.ml_models_path)
