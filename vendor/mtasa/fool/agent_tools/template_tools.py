from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AgentTool, ToolContext, ToolResult


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "fool" / "templates"

TEMPLATE_HINTS: dict[str, dict[str, str]] = {
    "solver_greedy.py": {
        "tags": "baseline,formula_a,parser_safe,incremental_refine",
        "summary": "真实 TSV 解析 + 去重 + Formula-A 风格贪心，是稳定起点模板。",
    },
    "solver_beam.py": {
        "tags": "large,high_noise,beam,coverage_guard",
        "summary": "任务驱动 Beam，带候选裁剪和合法性约束，适合 large/high_noise 定向探索。",
    },
    "solver_bitset_lns.py": {
        "tags": "large,high_noise,lns,bitset,ruin_repair",
        "summary": "Bitset + ruin-repair LNS，适合在不改 parser 的前提下做局部结构优化。",
    },
    "solver_loww_regret.py": {
        "tags": "low_willingness,regret,guarded_backup",
        "summary": "低意愿 regret 排序 + 受控备选，适合 low_w 桶的小步迭代。",
    },
    "solver_multi_anchor.py": {
        "tags": "medium,large,anchor,rarity_aware",
        "summary": "多锚点候选竞赛（visible/formula/rarity），适合 medium/large 稳态降分。",
    },
    "solver_scarce_repair.py": {
        "tags": "scarce_couriers,coverage_recovery,repair,multi_bundle",
        "summary": "scarce 覆盖优先 + hole-fill 修复，适合处理 courier 稀缺场景。",
    },
    "solver_output_checker.py": {
        "tags": "validation,safety,duplicate_guard,contract",
        "summary": "输出结构与重复冲突检查模板，适合防止 invalid_rows 回归。",
    },
    "solver_minimal.py": {
        "tags": "minimal,safety,parser_safe",
        "summary": "最小可运行真实模板（TSV 解析 + 去重 + 合法贪心），用于紧急恢复。",
    },
}


def _catalog() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if not TEMPLATE_DIR.exists():
        return entries
    for path in sorted(TEMPLATE_DIR.glob("solver_*.py")):
        meta = TEMPLATE_HINTS.get(path.name, {})
        entries.append(
            {
                "name": path.name,
                "path": str(path),
                "tags": meta.get("tags", "general"),
                "summary": meta.get("summary", "本地策略模板。"),
            }
        )
    return entries


def list_strategy_templates(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    entries = _catalog()
    if not entries:
        return ToolResult(
            name="list_strategy_templates",
            ok=True,
            summary="No strategy templates found.",
            data={"templates": []},
        )
    lines = [f"{item['name']} ({item['tags']}): {item['summary']}" for item in entries]
    return ToolResult(
        name="list_strategy_templates",
        ok=True,
        summary="; ".join(lines),
        data={"templates": entries},
    )


TOOLS = [
    AgentTool(
        name="list_strategy_templates",
        description="List available local solver strategy templates and their intended use.",
        run=list_strategy_templates,
    ),
]
