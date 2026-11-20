"""Tools the brain can use (docs/13 §5, §12)."""

from hedwig.tools.builtin import calculate_tool, datetime_tool, evaluate, search_memory_tool
from hedwig.tools.registry import Tool, ToolError, ToolRegistry

__all__ = [
    "Tool",
    "ToolError",
    "ToolRegistry",
    "calculate_tool",
    "datetime_tool",
    "evaluate",
    "search_memory_tool",
]
