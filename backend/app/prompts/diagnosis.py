"""
EXPLAIN 诊断 Prompt 模板定义。

依据 backend AGENTS.md §提示（Prompt 模板定义）：
  - Prompt 模板使用 Python 字面量（模块级常量），不可含运行时变量
  - 运行时变量通过 _build_prompt() 函数注入
  - 瓶颈识别模式参考 PRD §5.2
"""

from __future__ import annotations

from typing import TypedDict, cast

# =============================================================================
# 系统提示：SQL 性能分析专家
# =============================================================================

DIAGNOSIS_SYSTEM_PROMPT = """你是一个专业的数据库 SQL 性能分析专家。
你的任务是根据 EXPLAIN 执行计划分析 SQL 查询的性能瓶颈，并提供优化建议。

## 分析要点
1. 首先判断查询是否存在性能瓶颈（全表扫描、文件排序、临时表、索引缺失等）
2. 指出瓶颈的具体位置和可能原因
3. 提供可操作的优化建议（SQL 改写、索引建议、配置调整等）
4. 评估优化后预期可提升的效果

## 输出格式
请严格按以下 JSON 格式输出，不要添加额外说明：

{
  "bottleneck": "瓶颈描述，一句话说明主要性能问题",
  "suggestion": "优化建议，如 CREATE INDEX xxx ON yyy(col); 或 SQL 改写建议",
  "estimated_improvement": "预估优化效果，如'扫描行数将从 100万 降至 100'",
  "is_destructive": true或false
}

注意：
- is_destructive 表示该建议是否会修改数据/结构（如 CREATE INDEX = true，SQL 改写 = false）
- 如果无法确定瓶颈，将 bottleneck 设为「未发现明显瓶颈」"""

# =============================================================================
# 用户查询模板
# =============================================================================

DIAGNOSIS_USER_TEMPLATE = """请分析以下 {db_type} 数据库的 EXPLAIN 输出：

## EXPLAIN 信息
```
{explain_output}
```

## 原始 SQL
```sql
{sql}
```
"""

# =============================================================================
# 规则引擎——瓶颈模式匹配
# =============================================================================

# AC-3: 可识别的瓶颈模式列表
class _BottleneckPattern(TypedDict):
    """瓶颈模式类型定义。"""
    pattern: str
    keywords: list[str]
    suggestion: str
    is_destructive: bool


BOTTLENECK_PATTERNS: list[_BottleneckPattern] = cast(list[_BottleneckPattern], [
    {
        "pattern": "全表扫描",
        "keywords": [
            "table scan", "full scan", "全表扫描",
            "seq scan", "sequential scan", "ALL",
            # MySQL JSON EXPLAIN 格式: access_type=ALL 表示全表扫描
            '"access_type": "ALL"',
            # PostgreSQL EXPLAIN JSON 格式
            "Seq Scan", "Index Scan Backward",
        ],
        "suggestion": "建议为查询涉及的过滤和关联列添加索引",
        "is_destructive": False,
    },
    {
        "pattern": "文件排序",
        "keywords": [
            "filesort", "file sort", "using filesort",
            "sort", "external merge",
            # MySQL JSON EXPLAIN 格式: using_filesort=true
            '"using_filesort": true',
        ],
        "suggestion": "考虑为 ORDER BY 和 GROUP BY 列创建复合索引",
        "is_destructive": False,
    },
    {
        "pattern": "临时表",
        "keywords": [
            "temporary", "using temporary", "temp",
            "临时表", "派生表",
            # MySQL JSON EXPLAIN 格式: using_temporary_table=true
            '"using_temporary_table": true',
        ],
        "suggestion": "优化 GROUP BY 和 DISTINCT 查询，添加合适索引避免临时表",
        "is_destructive": False,
    },
    {
        "pattern": "全表扫描/顺序扫描",
        "keywords": [
            "seq scan", "sequential scan",
            "table scan", "full scan",
            # PostgreSQL EXPLAIN 格式
            "Seq Scan",
        ],
        "suggestion": "考虑为过滤条件列创建索引",
        "is_destructive": False,
    },
])


# =============================================================================
# 模板构建函数
# =============================================================================

def build_diagnosis_prompt(
    explain_output: str,
    sql: str,
    db_type: str,
) -> tuple[str, str]:
    """构建 EXPLAIN 诊断提示词。

    Args:
        explain_output: EXPLAIN 执行计划输出文本。
        sql: 被分析的原始 SQL 语句。
        db_type: 数据库类型（mysql / postgresql / oracle）。

    Returns:
        (system_prompt, user_prompt) 二元组。
    """
    db_label = db_type.upper()
    user_prompt = DIAGNOSIS_USER_TEMPLATE.format(
        db_type=db_label,
        explain_output=explain_output,
        sql=sql,
    )
    return DIAGNOSIS_SYSTEM_PROMPT, user_prompt


# =============================================================================
# 规则引擎降级分析
# =============================================================================

def rule_based_analyze(
    explain_output: str,
    sql: str,
) -> dict:
    """规则引擎：当 LLM 不可用时的降级分析。

    通过关键词匹配识别已知瓶颈模式（AC-4：LLM 降级方案）。
    返回结构与 LLM 分析一致，确保前端渲染不变。

    Args:
        explain_output: EXPLAIN 执行计划输出。
        sql: 被分析的原始 SQL。

    Returns:
        与 generate_llm_analysis 相同结构的 dict。
    """
    output_lower = explain_output.lower()

    for bp in BOTTLENECK_PATTERNS:
        if any(kw in output_lower for kw in bp["keywords"]):
            return {
                "bottleneck": f"发现瓶颈：{bp['pattern']}",
                "suggestion": (f"{bp['suggestion']}\n"
                               f"# SUGGESTION — {bp['pattern']}"),
                "estimated_improvement": f"通过解决 {bp['pattern']} 问题，"
                                         f"查询效率可显著提升",
                "is_destructive": bp["is_destructive"],
            }

    # 无匹配模式时，返回通用信息
    return {
        "bottleneck": "未发现明显瓶颈",
        "suggestion": f"请人工分析以下 EXPLAIN 输出：\n{explain_output[:200]}",
        "estimated_improvement": "N/A",
        "is_destructive": False,
    }
