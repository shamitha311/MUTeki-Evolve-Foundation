from __future__ import annotations

from .analysis_tools import TOOLS as ANALYSIS_TOOLS
from .base import AgentTool, ToolContext, ToolRegistry, ToolResult, render_tool_results
from .template_tools import TOOLS as TEMPLATE_TOOLS


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in [*ANALYSIS_TOOLS, *TEMPLATE_TOOLS]:
        registry.register(tool)
    return registry


__all__ = [
    "AgentTool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
    "render_tool_results",
]
