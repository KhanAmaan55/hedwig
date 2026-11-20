"""Tools the brain can use (docs/13 §5, §12)."""

from hedwig.tools.builtin import calculate_tool, datetime_tool, evaluate, search_memory_tool
from hedwig.tools.registry import Tool, ToolError, ToolRegistry
from hedwig.tools.research import research_memory, research_tool

__all__ = [
    "Tool",
    "ToolError",
    "ToolRegistry",
    "calculate_tool",
    "datetime_tool",
    "evaluate",
    "research_memory",
    "research_tool",
    "search_memory_tool",
]
