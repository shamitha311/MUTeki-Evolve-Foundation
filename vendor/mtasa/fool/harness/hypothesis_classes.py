"""Hypothesis-class taxonomy used by prompt.py to surface a passive
"recent classes" hint in each round header.

The structural-dedup / class-dedup gates that previously consumed this
taxonomy have been removed (see teacher_review.py for the replacement
periodic review). Only `classify_hypothesis` and `recent_hypothesis_classes`
are kept — anything beyond that was dead code as of the gate removal.
"""

from __future__ import annotations

from fool.harness.context import RoundState


# Order matters: earlier (more specific) classes win when multiple match.
# Keywords cover both Chinese and English wording. "other" is the catch-all.
HYPOTHESIS_CLASSES: list[tuple[str, tuple[str, ...]]] = [
    ("combo_activation", (
        "combo activation", "激活 combo", "组合激活",
        "combo 候选", "combo候选", "combo_delta",
        "启用 combo", "combo 启用", "combo 注入",
    )),
    ("chain_reopt", (
        "chain", "链式", "重排", " swap", "swap ", "swap_",
        "reopt", "再优化", "重优化", "局部搜索", "lns",
    )),
    ("scarce_coverage", (
        "覆盖优先", "coverage greedy", "_scarce_assign",
        "覆盖感知", "coverage aware", "coverage-aware",
        "覆盖率优先", "最大覆盖",
    )),
    ("classify_threshold", (
        "classify", "分类阈值", "courier_ratio<", "courier_ratio >",
        "courier_ratio<0", "courier_ratio>0", "门控阈值", "gate 阈值",
        "low_w 阈值", "low_w阈值", "low_w threshold",
    )),
    ("scoring_refine", (
        "_score_solution", "exact_score", "_exact_score",
        "精确双向 fold", "精确双向fold", "精确评分", "exact scoring",
        "精确罚分", "exact penalty",
    )),
    ("backup_aug", (
        "backup", "备份骑手", "extra notify", "extra_notify",
        "后备骑手", "后备 ", "添加备份", "追加备份", "备份上限",
    )),
    ("sort_anchor", (
        "anchor", "锚点", "排序键", "sort key", "dense_rank",
        "regret", "formula-a", "排序公式", "排序", "key=lambda",
    )),
]


def classify_hypothesis(text: str) -> str:
    if not text:
        return "other"
    t = text.lower()
    for cls, keywords in HYPOTHESIS_CLASSES:
        if any(k.lower() in t for k in keywords):
            return cls
    return "other"


def recent_hypothesis_classes(
    state: RoundState, *, window: int
) -> list[tuple[int, str]]:
    tail = list(state.recent_history)[-window:]
    return [(item.iteration, classify_hypothesis(item.hypothesis)) for item in tail]
