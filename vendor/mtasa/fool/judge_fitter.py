from __future__ import annotations

from typing import Iterable


FEATURE_NAMES = ["bias", "avg_uncovered", "avg_extra_notify", "avg_merged_rows"]


def extract_round_features(report_obj: dict) -> dict[str, float]:
    cases = report_obj.get("cases", []) or []
    if not cases:
        return {"avg_uncovered": 0.0, "avg_extra_notify": 0.0, "avg_merged_rows": 0.0}

    n = float(len(cases))
    return {
        "avg_uncovered": sum(float(c.get("uncovered_tasks", 0)) for c in cases) / n,
        "avg_extra_notify": sum(float(c.get("extra_notify", 0)) for c in cases) / n,
        "avg_merged_rows": sum(float(c.get("merged_rows", 0)) for c in cases) / n,
    }


def _solve_linear_system(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    aug = [row[:] + [b[i]] for i, row in enumerate(a)]

    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            continue
        aug[col], aug[pivot] = aug[pivot], aug[col]

        scale = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= scale

        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if abs(factor) < 1e-14:
                continue
            for j in range(col, n + 1):
                aug[r][j] -= factor * aug[col][j]

    return [aug[i][n] for i in range(n)]


def fit_judge_model(samples: Iterable[tuple[dict[str, float], float]]) -> dict:
    sample_list = list(samples)
    if len(sample_list) < 2:
        return {
            "enabled": False,
            "n": len(sample_list),
            "weights": [0.0, 100.0, 10.0, 2.0],
            "feature_names": FEATURE_NAMES,
            "mae": None,
        }

    xs = []
    ys = []
    for feat, target in sample_list:
        x = [
            1.0,
            float(feat.get("avg_uncovered", 0.0)),
            float(feat.get("avg_extra_notify", 0.0)),
            float(feat.get("avg_merged_rows", 0.0)),
        ]
        xs.append(x)
        ys.append(float(target))

    d = len(xs[0])
    xtx = [[0.0 for _ in range(d)] for _ in range(d)]
    xty = [0.0 for _ in range(d)]

    for x, y in zip(xs, ys):
        for i in range(d):
            xty[i] += x[i] * y
            for j in range(d):
                xtx[i][j] += x[i] * x[j]

    ridge = 1e-5
    for i in range(d):
        xtx[i][i] += ridge

    w = _solve_linear_system(xtx, xty)

    preds = [sum(w[i] * x[i] for i in range(d)) for x in xs]
    mae = sum(abs(p - y) for p, y in zip(preds, ys)) / len(ys)

    return {
        "enabled": True,
        "n": len(sample_list),
        "weights": w,
        "feature_names": FEATURE_NAMES,
        "mae": mae,
    }


def predict_score(model: dict, feat: dict[str, float]) -> float | None:
    if not model.get("enabled"):
        return None
    w = model.get("weights")
    if not isinstance(w, list) or len(w) != 4:
        return None
    x = [
        1.0,
        float(feat.get("avg_uncovered", 0.0)),
        float(feat.get("avg_extra_notify", 0.0)),
        float(feat.get("avg_merged_rows", 0.0)),
    ]
    return float(sum(w[i] * x[i] for i in range(4)))


def model_hint_text(model: dict) -> str:
    if not model.get("enabled"):
        return "Judge model fitting not ready (insufficient rounds)."
    w = model.get("weights", [0.0, 0.0, 0.0, 0.0])
    n = model.get("n", 0)
    mae = model.get("mae", 0.0)
    return (
        "Fitted judge model from previous rounds:\n"
        f"score ~= {w[0]:.3f} + {w[1]:.3f}*avg_uncovered + {w[2]:.3f}*avg_extra_notify + {w[3]:.3f}*avg_merged_rows\n"
        f"fit_n={n}, fit_mae={mae:.3f}"
    )
