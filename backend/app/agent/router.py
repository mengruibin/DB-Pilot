"""
意图路由器——将用户自然语言输入分类为预定义意图。

使用两层策略：
  1. 快速规则匹配（关键词正则，置信度 >0.8 时直接返回）
  2. 低置信度时调用轻量模型（通过 LLMClient）分类

依据 PRD §6.3 意图路由流程。
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agent.state import Intent
from app.config import settings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

logger = structlog.get_logger(__name__)

# =============================================================================
# 关键词规则定义
# =============================================================================

# 各意图的关键词映射表（含置信度权重）
_INTENT_KEYWORDS: dict[Intent, list[dict[str, Any]]] = {
    Intent.QUERY: [
        {"patterns": [r"查", r"查询", r"搜索", r"找", r"显示", r"列出", r"统计"], "weight": 0.6},
        {"patterns": [r"多少", r"哪些", r"谁", r"哪个", r"怎么"], "weight": 0.5},
        {"patterns": [r"select", r"sel"], "weight": 0.9},
        {"patterns": [r"表结构", r"字段", r"注释信息", r"数据表"], "weight": 0.7},
        {"patterns": [r"列", r"schema", r"表名", r"表信息"], "weight": 0.55},
    ],
    Intent.DIAGNOSIS: [
        {"patterns": [r"慢查询", r"执行计划", r"explain", r"性能", r"优化",
                       r"索引"], "weight": 0.85},
        {"patterns": [r"为什么.*慢", r"慢.*怎么", r"分析.*sql"], "weight": 0.9},
        {"patterns": [r"慢", r"延迟", r"响应慢"], "weight": 0.7},
    ],
    Intent.TROUBLESHOOT: [
        {"patterns": [r"死锁", r"锁等待", r"卡死", r"无法连接", r"连接池满"], "weight": 0.9},
        {"patterns": [r"连不上", r"连不上去", r"报错", r"错误", r"挂掉", r"挂了"], "weight": 0.85},
        {"patterns": [r"故障", r"宕机", r"异常", r"打不开"], "weight": 0.8},
        {"patterns": [r"锁", r"连接数"], "weight": 0.75},
    ],
    Intent.HEALTH_CHECK: [
        {"patterns": [r"巡检", r"健康检查", r"体检", r"检查.*健康"], "weight": 0.9},
        {"patterns": [r"状态", r"运行状况", r"检查.*状态"], "weight": 0.7},
        {"patterns": [r"health", r"check.*db", r"status"], "weight": 0.8},
    ],
    Intent.GENERAL: [
        {"patterns": [r"你好", r"你好", r"^hi", r"^hello", r"^hey"], "weight": 0.6},
        {"patterns": [r"你是谁", r"你能做什么", r"帮助", r"功能"], "weight": 0.7},
    ],
}

# 中文停用词（降低误匹配）
_STOP_WORDS = {"的", "了", "是", "在", "有", "不", "和", "就", "也", "都", "而"}


def _compute_keyword_score(text: str) -> dict[Intent, float]:
    """计算文本中每个意图的关键词匹配得分。

    对每个意图的所有关键词规则进行匹配，取最高权重作为该意图的得分。
    得分范围 [0, 1]，0 表示无匹配，1 表示完全匹配。
    """
    text_lower = text.lower().strip()
    scores: dict[Intent, float] = {intent: 0.0 for intent in Intent}

    for intent, rules in _INTENT_KEYWORDS.items():
        max_weight = 0.0
        for rule in rules:
            for pattern in rule["patterns"]:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    max_weight = max(max_weight, rule["weight"])
        scores[intent] = max_weight

    return scores


class IntentRouter:
    """用户意图路由器。

    用法：
        router = IntentRouter()
        intent = router.classify("最近一小时慢查询有哪些？")
        # -> Intent.DIAGNOSIS
    """

    def __init__(self, llm_model: BaseChatModel | None = None) -> None:
        """初始化意图路由器。

        Args:
            llm_model: 可选的 LangChain Chat 模型实例。若提供，在低置信度时
                调用 LLM 分类（通过 LLM_CLASSIFIER_MODEL 轻量模型）。
        """
        self._llm_model = llm_model
        # 关键词匹配的置信度阈值（> 此值直接返回，不调用 LLM）
        self._keyword_threshold = 0.8

    async def classify(
        self,
        user_message: str,
        conversation_history: str | None = None,
    ) -> Intent:
        """将用户消息分类为预定义意图。

        使用两层策略（PRD §6.3）：
          1. 快速规则匹配：关键词命中且置信度 > 0.8 时直接返回
          2. 低置信度时：调用 LLM 分类（当前实现返回最高得分意图）

        Args:
            user_message: 用户输入的原始消息。
            conversation_history: 可选的会话历史文本，用于 LLM 分类时
                理解上下文（如指代消解、省略补全）。

        Returns:
            分类后的 Intent 枚举值。
        """
        scores = _compute_keyword_score(user_message)

        # 找出最高得分的意图
        best_intent = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best_intent]

        # 置信度 > 阈值时直接返回（AC-3）
        if best_score >= self._keyword_threshold:
            self._last_method = "keyword"
            logger.info("意图分类完成", intent=best_intent.value,
                         confidence=round(best_score, 2),
                         method="keyword")
            return best_intent

        # 低置信度时尝试 LLM 分类（AC-4）
        if self._llm_model is not None:
            llm_intent = await self._classify_with_llm(
                user_message, conversation_history=conversation_history,
            )
            if llm_intent is not None:
                self._last_method = "llm"
                logger.info("意图分类完成", intent=llm_intent.value,
                             confidence=round(best_score, 2),
                             method="llm")
                return llm_intent

        # 若仍低于阈值但 QUERY 得分相对较高，倾向 QUERY
        if scores.get(Intent.QUERY, 0.0) >= 0.4 and best_score < 0.6:
            self._last_method = "fallback_query"
            logger.info("意图分类完成", intent=Intent.QUERY.value,
                         confidence=round(scores[Intent.QUERY], 2),
                         method="fallback_query")
            return Intent.QUERY

        # 高于最低阈值返回最佳匹配，否则返回 GENERAL
        if best_score >= 0.3:
            self._last_method = "keyword_low"
            logger.info("意图分类完成", intent=best_intent.value,
                         confidence=round(best_score, 2),
                         method="keyword_low")
            return best_intent

        self._last_method = "default"
        logger.info("意图分类完成", intent=Intent.GENERAL.value,
                     confidence=round(best_score, 2),
                     method="default")
        return Intent.GENERAL

    async def _classify_with_llm(
        self,
        user_message: str,
        conversation_history: str | None = None,
    ) -> Intent | None:
        """使用 LangChain Chat 模型对低置信度消息进行分类（任务 8：标准化重构）。

        调用 LangChain BaseChatModel.ainvoke() 进行快速分类。
        超时 10s，异常时静默降级（返回 None，由 classify() 走 fallback 逻辑）。

        Args:
            user_message: 用户消息。
            conversation_history: 可选的会话历史。

        Returns:
            Intent 枚举值，或 None（LLM 不可用或分类失败时）。
        """
        if self._llm_model is None:
            return None

        content = (
            "请将以下数据库运维相关的问题分类为以下意图之一：\n"
            "QUERY, DIAGNOSIS, TROUBLESHOOT, HEALTH_CHECK, GENERAL\n"
            "仅返回意图名称，不要包含其他内容。\n"
            f"消息：{user_message}"
        )

        # 若有会话历史，添加上下文提示（AC-4：支持指代消解和省略补全）
        if conversation_history:
            content = (
                "请将以下数据库运维相关的问题分类为以下意图之一：\n"
                "QUERY, DIAGNOSIS, TROUBLESHOOT, HEALTH_CHECK, GENERAL\n"
                "仅返回意图名称，不要包含其他内容。\n\n"
                f"对话历史：\n{conversation_history}\n\n"
                f"用户的最新消息：{user_message}"
            )

        try:
            model_name = getattr(self._llm_model, 'model_name', None) or getattr(self._llm_model, 'model', 'unknown')
            logger.debug("LLM 意图分类请求",
                         content_preview=content[:500],
                         model=model_name)
            # LangChain 标准调用：ainvoke 返回 AIMessage
            response = await self._llm_model.ainvoke([HumanMessage(content=content)])
            intent_name = response.content.strip().upper() if isinstance(response.content, str) else ""
            logger.debug("LLM 意图分类结果",
                         raw_response=str(response.content)[:200],
                         intent=intent_name)
            return Intent(intent_name)
        except Exception:
            logger.warning("LLM 意图分类失败，降级到规则匹配", exc_info=True)
            return None
