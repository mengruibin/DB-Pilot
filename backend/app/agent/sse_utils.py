"""
SSE 格式化公共工具。

提供 _format_sse() 和 _truncate_preview() 等工具函数，
供 api/chat.py 和 api/troubleshoot.py / api/report.py 共享使用。

依据 AGENTS.md §SSE 流式格式。
"""

from __future__ import annotations

import json
from typing import Any


def format_sse(data: dict[str, Any]) -> str:
    """将 dict 格式化为 SSE 消息字符串。

    SSE 格式（AGENTS.md §SSE 流式格式）：
      event: message\\ndata: {json}\\n\\n

    Args:
        data: 事件数据 dict，必须包含 "type" 字段。

    Returns:
        SSE 格式化字符串。
    """
    return f"event: message\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def truncate_preview(rows: list, max_rows: int = 100) -> list:
    """截断结果预览行数。

    AC-9：data_preview 仅返回前 100 行，完整数据通过分页接口获取。

    Args:
        rows: 原始数据行列表。
        max_rows: 最大返回行数。

    Returns:
        截断后的行列表。
    """
    return rows[:max_rows]
