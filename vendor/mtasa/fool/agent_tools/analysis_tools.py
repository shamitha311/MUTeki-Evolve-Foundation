from __future__ import annotations

from typing import Any

from .base import AgentTool, ToolContext, ToolResult


def _case_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    rows = report.get("cases", [])
    return [row for row in rows if isinstance(row, dict)]


def profile_dataset(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    text = context.dataset_profile.strip() or "dataset profile unavailable"
    return ToolResult(
        name="profile_dataset",
        ok=True,
        summary=text[:1400],
        data={"profile": text},
    )


def rank_bottlenecks(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    top_k = max(1, int(args.get("top_k", 4)))
    report = context.best_report or context.last_report
    rows = _case_rows(report)
    if not rows:
        return ToolResult(
            name="rank_bottlenecks",
            ok=True,
            summary="No scored report yet; first round should establish a baseline.",
            data={"cases": []},
        )

    ranked = sorted(
        rows,
        key=lambda row: (
            float(row.get("score", 0.0)),
            int(row.get("uncovered_tasks", row.get("uncovered", 0))),
            int(row.get("extra_notify", 0)),
        ),
        reverse=True,
    )[:top_k]
    compact: list[dict[str, Any]] = []
    lines: list[str] = []
    for row in ranked:
        name = str(row.get("case_name", row.get("case", row.get("name", "?"))))
        score = float(row.get("score", 0.0))
        covered = int(row.get("covered", 0))
        total = int(row.get("total_tasks", row.get("total", 0)))
        uncovered = int(row.get("uncovered_tasks", row.get("uncovered", max(0, total - covered))))
        compact.append(
            {
                "case": name,
                "score": score,
                "covered": covered,
                "total": total,
                "uncovered": uncovered,
            }
        )
        lines.append(f"{name}: score={score:.2f}, coverage={covered}/{total}, uncovered={uncovered}")
    return ToolResult(
        name="rank_bottlenecks",
        ok=True,
        summary="; ".join(lines),
        data={"cases": compact},
    )


TOOLS = [
    AgentTool(
        name="profile_dataset",
        description="Summarize the active input dataset profile for planning.",
        run=profile_dataset,
    ),
    AgentTool(
        name="rank_bottlenecks",
        description="Rank currently worst scored cases from the best or last report.",
        run=rank_bottlenecks,
    ),
]
