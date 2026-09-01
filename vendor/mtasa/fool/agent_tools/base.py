from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ToolResult:
    name: str
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolContext:
    input_dir: Path
    run_dir: Path
    memory_scope: str
    dataset_profile: str
    best_report: dict[str, Any] | None = None
    last_report: dict[str, Any] | None = None


ToolFn = Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    run: ToolFn


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def run(self, name: str, context: ToolContext, args: dict[str, Any] | None = None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name=name, ok=False, summary=f"tool not found: {name}")
        try:
            return tool.run(context, args or {})
        except Exception as exc:
            return ToolResult(name=name, ok=False, summary=f"tool failed: {exc}")

    def list_specs(self) -> list[dict[str, str]]:
        return [
            {"name": tool.name, "description": tool.description}
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]


def render_tool_results(results: list[ToolResult], *, max_chars: int = 4200) -> str:
    if not results:
        return "No tool observations."
    lines: list[str] = ["Agent tool observations:"]
    for result in results:
        status = "ok" if result.ok else "failed"
        lines.append(f"- {result.name} [{status}]: {result.summary}")
    text = "\n".join(lines)
    return text[:max_chars]
