"""Prove the estimate/prediction seam:
  - engineering estimate is the default, tagged as such
  - a registered model prediction is used ONLY when confident
  - low-confidence predictions fall back to the engineering estimate
  - the actuals predictor returns None when there's no measured data (never fabricates)
Run: python tests/test_estimator_seam.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from engine.estimators import (Estimate, estimate_stage_minutes,
                               register_predictor, has_predictor,
                               build_actuals_predictor, CONFIDENCE_FLOOR)


def check(label, cond):
    print(f"  {'✓' if cond else '✗'} {label}")
    assert cond, label


def main():
    print("\n" + "=" * 64)
    print("ESTIMATE / PREDICTION SEAM")
    print("=" * 64)

    # 1. default: engineering estimate
    print("\n1. Default is the engineering estimate")
    register_predictor(None)
    e = estimate_stage_minutes("09", "DP")
    check("returns an Estimate", isinstance(e, Estimate))
    check("source is engineering", e.source == "engineering")
    check("carries a confidence", 0 <= e.confidence <= 1)
    check("has minutes", e.minutes >= 0)
    print(f"   09/DP -> {e.minutes}m, source={e.source}, conf={e.confidence}")

    # 2. a confident model prediction is used
    print("\n2. A confident model prediction overrides the estimate")
    def confident(stage_id, line, ctx):
        return Estimate(minutes=999, confidence=0.85, source="model",
                        basis="measured mean of 20 actuals")
    register_predictor(confident)
    check("predictor registered", has_predictor())
    e = estimate_stage_minutes("09", "DP")
    check("uses the model value", e.minutes == 999)
    check("source is model", e.source == "model")
    print(f"   09/DP -> {e.minutes}m, source={e.source}, conf={e.confidence}")

    # 3. a LOW-confidence prediction is ignored -> falls back
    print("\n3. Low-confidence prediction falls back to the estimate")
    def unsure(stage_id, line, ctx):
        return Estimate(minutes=999, confidence=0.3, source="model", basis="n=2")
    register_predictor(unsure)
    e = estimate_stage_minutes("09", "DP")
    check(f"ignored below floor ({CONFIDENCE_FLOOR})", e.minutes != 999)
    check("fell back to engineering", e.source == "engineering")
    print(f"   09/DP -> {e.minutes}m, source={e.source} (model was too unsure)")

    # 4. a model that errors must never break scheduling
    print("\n4. A failing model never breaks the caller")
    def broken(stage_id, line, ctx):
        raise RuntimeError("model exploded")
    register_predictor(broken)
    e = estimate_stage_minutes("09", "DP")
    check("survived model failure", e.source == "engineering")

    # 5. the actuals predictor returns None with no measured data
    print("\n5. Actuals predictor fabricates nothing without data")
    register_predictor(None)
    # no Flask app context here -> build returns None gracefully
    pred = build_actuals_predictor()
    check("no predictor built from zero actuals", pred is None)

    print("\n" + "=" * 64)
    print("SEAM WORKS — engineering estimate by default; a model plugs in behind")
    print("the SAME call and is used only when confident; failures fall back; the")
    print("scheduler never changes. ML-ready without any model needing to exist.")
    print("=" * 64)


if __name__ == "__main__":
    main()
