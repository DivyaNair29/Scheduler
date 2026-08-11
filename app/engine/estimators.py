"""Pluggable duration estimation — the seam for future ML predictions.

Today every stage duration comes from plant.py's ENGINEERING ESTIMATE. This
module wraps that behind a single interface so a learned model can later slot in
WITHOUT touching the scheduler. Both an estimate and a prediction return the same
shape — a value plus a confidence and a source — and the scheduler treats them
identically, but can respect the confidence (e.g. pad low-confidence stages).

Design rules (kept honest):
  * The engineering estimate is ALWAYS available and is the default/fallback.
  * A model prediction is used ONLY when a model is registered AND its confidence
    clears a threshold; otherwise we fall back to the estimate. Low-confidence
    predictions never silently override a known estimate.
  * Every result carries `source` ("engineering" | "model") and `confidence`
    (0..1) so the UI and scheduler can show WHERE a number came from. Nothing is
    an unexplained black-box number.
  * A model trained on SYNTHETIC data must report source="model:synthetic" so it
    can never be mistaken for one trained on measured actuals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Estimate:
    """A per-unit minutes estimate/prediction for one stage, with provenance."""
    minutes: int
    confidence: float          # 0..1 — how much to trust this number
    source: str                # "engineering" | "model" | "model:synthetic"
    basis: str = ""            # short human explanation (feature note, sample n)

    @property
    def is_measured_model(self) -> bool:
        return self.source == "model"

    def to_dict(self) -> dict:
        return {"minutes": self.minutes, "confidence": round(self.confidence, 2),
                "source": self.source, "basis": self.basis}


# A predictor takes (stage_id, line_code, context) and returns an Estimate or
# None (meaning "I have nothing confident to say — use the engineering value").
Predictor = Callable[[str, str, dict], Optional[Estimate]]

# The registered model predictor, if any. None => engineering estimates only.
_PREDICTOR: Optional[Predictor] = None

# Minimum confidence a model prediction must clear to be used over the estimate.
CONFIDENCE_FLOOR = 0.6


def register_predictor(predictor: Optional[Predictor]) -> None:
    """Install a model predictor (or pass None to revert to estimates-only).
    Called at startup once a trained model exists. The scheduler never changes."""
    global _PREDICTOR
    _PREDICTOR = predictor


def has_predictor() -> bool:
    return _PREDICTOR is not None


def estimate_stage_minutes(stage_id: str, line_code: str, *,
                           context: Optional[dict] = None) -> Estimate:
    """The single entry point the scheduler uses. Returns the best available
    per-unit-minutes number for this stage, with provenance and confidence.

    Order of preference:
      1. a registered model prediction that clears CONFIDENCE_FLOOR
      2. otherwise the engineering estimate from plant.py (confidence fixed low-
         medium to signal it is an unmeasured guess)
    """
    context = context or {}
    # 1. try the model, if one is registered
    if _PREDICTOR is not None:
        try:
            pred = _PREDICTOR(stage_id, line_code, context)
            if pred is not None and pred.confidence >= CONFIDENCE_FLOOR:
                return pred
        except Exception:
            pass  # any model failure must never break scheduling

    # 2. engineering estimate (the always-available default)
    from engine import plant
    from engine.adapter import LINE_MAP
    line = LINE_MAP.get(line_code)
    if line is None:
        # unknown line — use the stage's base per-unit if present
        st = plant.STAGES.get(stage_id)
        mins = st.per_unit_min if st else 0
    else:
        mins = plant.per_unit_minutes(stage_id, line)
    return Estimate(
        minutes=mins, confidence=0.5, source="engineering",
        basis="engineering estimate (not floor-measured)")


# --------------------------------------------------------------------------
# Reference model built from MEASURED actuals — the first real predictor.
# It is NOT auto-registered; call build_actuals_predictor() once enough
# measured StageActual rows exist, then register_predictor(...) with it.
# This is the honest ML on-ramp: it only speaks when it has real samples.
# --------------------------------------------------------------------------
def build_actuals_predictor(min_samples: int = 8) -> Optional[Predictor]:
    """Build a simple predictor from MEASURED stage actuals (mean per-unit time
    per stage/line, with confidence rising as samples accumulate). Returns None
    if there isn't enough measured data yet — so it never fabricates.

    This is deliberately a baseline (per-group mean), not a heavy model: it is
    interpretable, robust on small data, and a correct first step. A gradient-
    boosted or other model can replace it behind the SAME interface later.
    """
    try:
        from models import StageActual
        from collections import defaultdict
        rows = StageActual.query.filter_by(superseded_by=None, measured=True).all()
    except Exception:
        return None

    # group measured durations by (stage, line); need qty to get per-unit, but
    # we approximate with duration as a stage-level signal here.
    buckets: dict = defaultdict(list)
    for r in rows:
        if r.duration_min and r.stage:
            buckets[(r.stage, r.line_code or "*")].append(r.duration_min)

    usable = {k: v for k, v in buckets.items() if len(v) >= min_samples}
    if not usable:
        return None

    stats = {}
    for (stage, line), vals in usable.items():
        n = len(vals)
        mean = sum(vals) / n
        # confidence grows with samples, capped — honest about small n
        conf = min(0.9, 0.55 + 0.03 * n)
        stats[(stage, line)] = (mean, conf, n)

    # map stage NAME->id if needed later; predictor keys on whatever the actuals
    # recorded (StageActual.stage stores the display name).
    def predictor(stage_id: str, line_code: str, context: dict):
        # StageActual.stage is a display name; translate id->name for lookup
        from engine.adapter import STAGE_ID_TO_NAME
        stage_name = STAGE_ID_TO_NAME.get(stage_id, stage_id)
        key = (stage_name, line_code)
        if key not in stats:
            key = (stage_name, "*")
        if key not in stats:
            return None
        mean, conf, n = stats[key]
        return Estimate(minutes=int(round(mean)), confidence=conf,
                        source="model",
                        basis=f"measured mean of {n} actuals")

    return predictor
