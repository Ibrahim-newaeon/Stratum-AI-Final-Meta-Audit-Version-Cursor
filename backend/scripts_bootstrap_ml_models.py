#!/usr/bin/env python3
# =============================================================================
# Stratum AI - Bootstrap sample ML model artifacts (Module: local inference)
# =============================================================================
"""Train sample ``.pkl`` artifacts into ``settings.ml_models_path``.

Local inference (``LocalInferenceStrategy``) loads ``roas_predictor.pkl``,
``conversion_predictor.pkl``, and ``budget_impact.pkl`` from disk. Those
binaries are gitignored; this script regenerates them from synthetic data so
dev / demo environments can run predictions without a warehouse.

Idempotent: exits successfully without re-training when the three core
``.pkl`` files already exist, unless ``--force`` is passed.

Usage::

    python scripts_bootstrap_ml_models.py
    python scripts_bootstrap_ml_models.py --force --campaigns 40 --days 14
    BOOTSTRAP_ML_MODELS=true  # via docker-entrypoint when SEED_DEMO flows run

Do not commit the generated ``.pkl`` files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CORE_MODELS = (
    "roas_predictor",
    "conversion_predictor",
    "budget_impact",
)


def _models_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    try:
        from app.core.config import settings

        return Path(settings.ml_models_path)
    except Exception:
        return Path("./ml_models")


def core_artifacts_present(models_dir: Path) -> bool:
    """Return True when every core predictor ``.pkl`` exists."""
    return all((models_dir / f"{name}.pkl").exists() for name in CORE_MODELS)


def bootstrap(
    *,
    models_dir: Path,
    campaigns: int = 60,
    days: int = 21,
    force: bool = False,
    include_platform_models: bool = False,
) -> dict:
    """Train sample models into ``models_dir``; skip when artifacts exist."""
    models_dir.mkdir(parents=True, exist_ok=True)

    if core_artifacts_present(models_dir) and not force:
        print(f"ML artifacts already present under {models_dir}; skipping (use --force to retrain)")
        return {"skipped": True, "models_path": str(models_dir)}

    from app.ml.data_loader import TrainingDataLoader
    from app.ml.train import ModelTrainer

    print(
        f"Bootstrapping ML models → {models_dir} "
        f"(campaigns={campaigns}, days={days}, force={force})"
    )
    df = TrainingDataLoader.generate_sample_data(
        num_campaigns=campaigns,
        days_per_campaign=days,
    )
    trainer = ModelTrainer(str(models_dir))
    results = trainer.train_all(df, include_platform_models=include_platform_models)

    missing = [name for name in CORE_MODELS if not (models_dir / f"{name}.pkl").exists()]
    if missing:
        raise RuntimeError(f"Bootstrap finished but missing artifacts: {missing}")

    print("Bootstrap complete:")
    for name in CORE_MODELS:
        print(f"  - {models_dir / f'{name}.pkl'}")
    return {"skipped": False, "models_path": str(models_dir), "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap sample Stratum ML .pkl artifacts")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Models directory (default: settings.ml_models_path)",
    )
    parser.add_argument("--campaigns", type=int, default=60, help="Synthetic campaigns")
    parser.add_argument("--days", type=int, default=21, help="Days per campaign")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retrain even when core .pkl files already exist",
    )
    parser.add_argument(
        "--with-platform-models",
        action="store_true",
        help="Also train platform-specific ROAS variants (slower)",
    )
    args = parser.parse_args(argv)

    try:
        bootstrap(
            models_dir=_models_dir(args.output),
            campaigns=args.campaigns,
            days=args.days,
            force=args.force,
            include_platform_models=args.with_platform_models,
        )
    except Exception as exc:
        print(f"ML bootstrap failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
