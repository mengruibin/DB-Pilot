"""
软删除标识列检测（soft-delete-plan）。

从列信息（适配器 get_columns 统一返回的 {name, type, nullable, is_primary, comment}）
中启发式识别软删除标识列，供 describe_table 工具标注，让 Agent 在删除数据时
生成软删除 UPDATE（SET 标识=已删除）而非硬 DELETE。

命名 + 注释双通道，零配置、纯逻辑、可单测：
  - 列名关键词：归一化（小写 + 去下划线）后精确匹配，防误伤 create_time / status 等
  - 列注释关键词：列名未命中时兜底（如"是否删除 / 删除标记 / 软删除"）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# =============================================================================
# 关键词定义
# =============================================================================

# flag 型（标志位）软删除列名：归一化后精确匹配，1=已删除 0=正常
_FLAG_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "isdeleted",
        "isdelete",
        "deleted",
        "delete",
        "deleteflag",
        "deletedflag",
        "delflag",
        "isdel",
        "isdeletedflag",
        "deletedmark",
        "delmark",
        "removed",
        "isremoved",
        "deletestate",
        "deletedstate",
        "delstate",
    }
)

# deleted_at 型（时间戳）软删除列名：归一化后精确匹配，NULL=正常 非NULL=已删除
_TIMESTAMP_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "deletedat",
        "deleteat",
        "deletetime",
        "deletedtime",
        "deltime",
        "removedat",
    }
)

# flag 型列注释关键词（列名未命中时兜底）
_FLAG_COMMENT_KEYWORDS: tuple[str, ...] = (
    "软删除",
    "删除标记",
    "删除标识",
    "删除标志",
    "是否删除",
    "逻辑删除",
)

# deleted_at 型列注释关键词
_TIMESTAMP_COMMENT_KEYWORDS: tuple[str, ...] = (
    "删除时间",
    "删除时刻",
    "软删除时间",
)

# 时间类类型关键词（用于类型合理性：flag 名 + 时间类型 → 归为 deleted_at 型）
_TIME_TYPE_KEYWORDS: tuple[str, ...] = ("datetime", "timestamp", "date", "time")


# =============================================================================
# 数据结构
# =============================================================================


@dataclass
class SoftDeleteInfo:
    """软删除标识列检测结果。

    Attributes:
        column: 标识列名。
        kind: 软删除形态——flag=标志位（SET 列=deleted_value），
            deleted_at=时间戳（SET 列=NOW()，NULL 表示未删除）。
        deleted_value: flag 型的已删除取值（默认 1）；deleted_at 型为 None。
        active_value: flag 型的正常取值（默认 0）；deleted_at 型为 None。
        reason: 命中依据（"列名 xxx" / "注释 xxx"），供 LLM 理解来源。
    """

    column: str
    kind: Literal["flag", "deleted_at"]
    deleted_value: int | None
    active_value: int | None
    reason: str


# =============================================================================
# 检测逻辑
# =============================================================================


def _normalize_column_name(name: str) -> str:
    """归一化列名：小写 + 去除下划线，用于关键词精确匹配。"""
    return (name or "").lower().replace("_", "").strip()


def _is_time_type(type_str: str) -> bool:
    """判断列类型是否为时间类（datetime / timestamp / date / time）。"""
    t = (type_str or "").lower()
    return any(k in t for k in _TIME_TYPE_KEYWORDS)


def detect_soft_delete_columns(columns: list[dict[str, Any]]) -> list[SoftDeleteInfo]:
    """从列信息中检测软删除标识列。

    检测优先级：
      1. 列名关键词精确匹配（flag / deleted_at 两类命名集合）
      2. 列注释关键词兜底（列名未命中时）
      类型合理性：flag 命名的列但类型为时间类 → 归为 deleted_at 型。

    Args:
        columns: 适配器 get_columns 返回的列列表，每项含
            {name, type, nullable, is_primary, comment}。

    Returns:
        命中的软删除标识列列表（通常 0~1 个，按 columns 原始顺序）。
    """
    results: list[SoftDeleteInfo] = []
    for col in columns:
        name = col.get("name", "")
        type_str = col.get("type", "")
        comment = (col.get("comment") or "").strip()
        norm = _normalize_column_name(name)
        is_time = _is_time_type(type_str)

        info: SoftDeleteInfo | None = None
        if norm in _FLAG_COLUMN_NAMES:
            # flag 命名但类型是时间类 → 语义更接近"删除时间"，归为 deleted_at 型
            if is_time:
                info = SoftDeleteInfo(
                    column=name,
                    kind="deleted_at",
                    deleted_value=None,
                    active_value=None,
                    reason=f"列名 {name}",
                )
            else:
                info = SoftDeleteInfo(
                    column=name,
                    kind="flag",
                    deleted_value=1,
                    active_value=0,
                    reason=f"列名 {name}",
                )
        elif norm in _TIMESTAMP_COLUMN_NAMES:
            info = SoftDeleteInfo(
                column=name,
                kind="deleted_at",
                deleted_value=None,
                active_value=None,
                reason=f"列名 {name}",
            )
        else:
            # 注释兜底（列名未命中时）：flag 型注释要求非时间类型，避免把
            # "删除时间" 这类注释误判为标志位
            if any(k in comment for k in _FLAG_COMMENT_KEYWORDS) and not is_time:
                info = SoftDeleteInfo(
                    column=name,
                    kind="flag",
                    deleted_value=1,
                    active_value=0,
                    reason=f"注释 {comment[:20]}",
                )
            elif any(k in comment for k in _TIMESTAMP_COMMENT_KEYWORDS):
                info = SoftDeleteInfo(
                    column=name,
                    kind="deleted_at",
                    deleted_value=None,
                    active_value=None,
                    reason=f"注释 {comment[:20]}",
                )

        if info is not None:
            results.append(info)
    return results
