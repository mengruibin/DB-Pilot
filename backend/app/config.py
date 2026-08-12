"""
DB-Pilot 配置管理模块。

从 .env / 环境变量加载全量配置。
依据 AGENTS.md §安全与合规红线：启动时校验必填字段，缺失则拒绝启动。
依据 AGENTS.md §LLM 集成：模型名称必须从配置读取，禁止硬编码。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file_path() -> Path | None:
    """定位 .env 文件路径（可通过 ENV_FILE 环境变量覆盖）。"""
    explicit = os.getenv("ENV_FILE")
    if explicit:
        return Path(explicit)
    # 默认路径：backend/.env（config.py 位于 backend/app/，所以上两级）
    candidate = Path(__file__).resolve().parent.parent / ".env"
    return candidate if candidate.exists() else None


class Settings(BaseSettings):
    """应用全局配置，从 .env 和环境变量加载。

    所有敏感值（API Key、DB URL）仅从环境变量读取，禁止硬编码。
    启动时校验必填字段是否为空，拒绝启动不完整的服务。
    """

    model_config = SettingsConfigDict(
        env_file=_env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略未声明的环境变量，避免意外覆盖
    )

    # ==================== LLM 提供方 ====================
    LLM_API_KEY: str = Field(
        default="",
        description="LLM 提供方的 API Key（默认 Anthropic）。必填。",
    )
    LLM_MODEL: str = Field(
        default="",
        description="复杂任务模型（NL2SQL、诊断、故障排查）。必填。",
    )
    LLM_CLASSIFIER_MODEL: str = Field(
        default="claude-haiku-4-5-20251001",
        description="（已废弃 2026-07）意图分类已移除，不再调用 LLM。保留此项仅为向后兼容。",
    )
    LLM_PROVIDER: Literal["anthropic", "openai"] = Field(
        default="anthropic",
        description="LLM 提供方切换：anthropic 或 openai。",
    )
    LLM_API_URL: str = Field(
        default="",
        description="自定义 LLM API URL。为空时使用 provider 默认地址。"
        "（Anthropic: https://api.anthropic.com/v1/messages, "
        "OpenAI: https://api.openai.com/v1/chat/completions）",
    )
    ENABLE_REASONING: bool = Field(
        default=False,
        description="启用模型深度思考模式（DeepSeek thinking / Claude extended thinking）。"
        "开启后 LLM 输出分为 reasoning_content（内部推理）和 content（对外回答），"
        "前端分别渲染到思考面板和主聊天区。",
    )
    LLM_CONTEXT_WINDOW: int = Field(
        default=0,
        ge=0,
        description=(
            "模型上下文窗口（token）。0=自动：自定义端点探测 /models → "
            "内置模型注册表 → 默认 64K（见 app/agent/context_window.py）。"
            "显式配置时覆盖自动推导——探测不到或注册表没有的私有模型必须手填。"
        ),
    )

    # ==================== 内部数据库 ====================
    DATABASE_URL: str = Field(
        default="",
        description="内部数据库连接串（支持 mysql+aiomysql 或 sqlite+aiosqlite）。必填。"
        "示例：mysql+aiomysql://root:password@127.0.0.1:3306/db_pilot",
    )

    # ==================== Checkpointer（PostgreSQL） ====================
    CHECKPOINT_DB_URL: str = Field(
        default="",
        description="LangGraph AsyncPostgresSaver 连接串（psycopg 格式）。"
        "checkpointer-redis-migration-plan（PostgreSQL 方案）。"
        "示例：postgresql://user:password@127.0.0.1:5432/db_pilot_checkpoint",
    )

    # ==================== 服务端 ====================
    SERVER_HOST: str = Field(
        default="127.0.0.1",
        description="服务监听地址。",
    )
    SERVER_PORT: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="服务监听端口。",
    )
    CORS_ORIGINS: str = Field(
        default="http://localhost:5173",
        description="允许的 CORS 来源（逗号分隔）。",
    )

    # ==================== 安全 ====================
    SESSION_IDLE_TIMEOUT_MINUTES: int = Field(
        default=30,
        ge=1,
        description="会话空闲超时（分钟），PRD §8.2 要求 30 分钟自动清理。",
    )

    # ==================== 认证 ====================
    JWT_SECRET: str = Field(
        default="",
        description="JWT 签名密钥（至少 32 字符）。必填，缺失时拒绝启动。",
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=1440,
        ge=5,
        description="JWT 访问令牌过期时间（分钟），默认 1440（24 小时）。",
    )

    # ==================== 日志与可观测性 ====================
    LOG_LEVEL: str = Field(
        default="DEBUG",
        description="日志级别：DEBUG / INFO / WARNING / ERROR。",
    )
    LOG_FORMAT: str = Field(
        default="text",
        description="日志输出格式：json（生产）或 text（开发彩色控制台）。",
    )
    LOG_DIR: str = Field(
        default="logs",
        description="日志文件输出目录（自动创建）。仅当 LOG_FILE 为空时生效。",
    )
    LOG_FILE: str | None = Field(
        default=None,
        description="日志文件路径（可选）。为空时使用 LOG_DIR 按日期自动生成。",
    )
    LOG_BACKUP_DAYS: int = Field(
        default=30,
        ge=1,
        le=365,
        description="日志文件保留天数（每日轮转）。",
    )
    AGENT_DEBUG: bool = Field(
        default=False,
        description="Agent 调试模式。开启后 SSE 事件额外携带完整 LLM prompt/响应和安全检查详情。",
    )
    AGENT_MAX_CONCURRENT_TOOLS: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Agent 每轮 ReAct 迭代中最大并行工具数，防止 DB 连接池被冲垮。",
    )
    LOG_TRACE_ENABLED: bool = Field(
        default=True,
        description="是否启用链路追踪。关闭后 trace_id 仍生成但不强制透传。",
    )

    # ==================== LLM 并发限流（llm-concurrency-limit-plan） ====================
    AGENT_MAX_CONCURRENT_LLM: int = Field(
        default=5,
        ge=1,
        le=20,
        description="全局 LLM 调用并发上限：同一时刻最多允许的 LLM 请求数（进程级，"
        "单进程部署下即全局硬上限）。防止并发请求打爆 provider 配额触发 429 限流，"
        "同时控制 token 成本。全后端唯一的 LLM 调用点（graph.py agent_node 的 ainvoke）"
        "统一经 LLMLimiter 限流。",
    )
    AGENT_LLM_WAIT_TIMEOUT_SECONDS: float = Field(
        default=30,
        ge=1,
        le=600,
        description="LLM 并发饱和时的排队等待超时（秒）。超限时新请求排队等待并发槽位，"
        "超过该时间仍未轮到的返回「AI 服务繁忙」友好提示并结束本轮，避免无限挂起。",
    )

    # ==================== 上下文压缩（context-compression-plan） ====================
    AGENT_CONTEXT_COMPRESS_ENABLED: bool = Field(
        default=True,
        description="是否启用上下文压缩。关闭后 _build_llm_messages 全量发送历史（旧行为）。",
    )
    AGENT_COMPACT_TRIGGER_TOKENS: int = Field(
        default=0,
        ge=0,
        description="上下文压缩懒触发阈值（token）。0=自动：按模型上下文窗口 ×0.5 推导"
        "（含 4K 下限与 12K 输出留白护栏，窗口来源见 LLM_CONTEXT_WINDOW）。>0 时显式覆盖自动推导。",
    )
    AGENT_KEEP_RECENT_TURNS: int = Field(
        default=5,
        ge=0,
        le=10,
        description="近 K 轮窗口：最近 K 个已完成轮次逐字保留（追问/未解决需要的数据）。"
        "0 表示只保留当前轮，全部旧轮进摘要。",
    )
    AGENT_IDLE_DECAY_MINUTES: int = Field(
        default=60,
        ge=0,
        description="闲置衰减：新轮距上一轮超过该分钟数时，窗口 K 强制归 0"
        "（数据已过时 + prompt cache 已过期 + 可重查）。",
    )
    AGENT_DIGEST_MAX_TURNS: int = Field(
        default=20,
        ge=1,
        description="摘要最多保留最近 N 轮，最旧轮次丢弃，防止摘要自身膨胀。",
    )
    AGENT_DIGEST_MAX_Q_CHARS: int = Field(
        default=300,
        ge=20,
        description="摘要中每轮用户问题的最大字符数（语义锚点，正常问题远小于此，仅兜异常长粘贴）。",
    )
    AGENT_DIGEST_MAX_CONCLUSION_CHARS: int = Field(
        default=800,
        ge=50,
        description="摘要中每轮助手最终结论的最大字符数（结论是摘要核心载荷，有界多留）。",
    )
    AGENT_DIGEST_MAX_ARG_CHARS: int = Field(
        default=300,
        ge=20,
        description=(
            "摘要中单个工具参数的最大字符数"
            "（实测 SQL avg 115 / max 244，正常不截，兜尾部极端查询）。"
        ),
    )
    AGENT_ESTIMATE_CHARS_PER_TOKEN: int = Field(
        default=3,
        ge=1,
        le=6,
        description="token 估算启发式：每字符折算 token 数（中文约 1-2 字符/token，"
        "中英混合取 3）。仅用于触发阈值判断，需保守（小值→估高→提前触发）。",
    )
    EXPLAIN_MAX_CHARS: int = Field(
        default=8_000,
        ge=1_000,
        description="EXPLAIN 类结果（explain_output / explain_result）截断上限字符数。"
        "JSON 执行计划高度重复，LLM 只需看节点形状，不需要全部节点。",
    )

    # ==================== 结果导出（query-result-export-plan） ====================
    EXPORT_MAX_ROWS: int = Field(
        default=100_000,
        ge=1,
        description="单次导出最大行数硬上限，防止大批量导出拖垮数据库。"
        "超过该值的查询结果会被截断并在 CSV 末尾标注。",
    )
    EXPORT_BATCH_SIZE: int = Field(
        default=2_000,
        ge=100,
        description="导出游标分批拉取行数（内存有界的关键）。",
    )

    # ==================== 校验 ====================

    @field_validator("LLM_API_KEY", "LLM_MODEL", "DATABASE_URL", "JWT_SECRET")
    @classmethod
    def _required_not_empty(cls, v: str, info: Field.field_validator) -> str:
        """必填字段非空校验。缺失任一字段则拒绝启动（AGENTS.md §安全与合规红线）。"""
        if not v or not v.strip():
            msg = (
                f"缺少必填配置项：{info.field_name}。"
                "请在 .env 或环境变量中设置。"
                f"参考 .env.example 模板。"
            )
            raise ValueError(msg)
        return v.strip()

    @field_validator("JWT_SECRET")
    @classmethod
    def _jwt_secret_min_length(cls, v: str) -> str:
        """JWT 签名密钥长度至少 32 字符。"""
        v_stripped = v.strip()
        if len(v_stripped) < 32:
            raise ValueError(
                "JWT_SECRET 长度必须至少为 32 字符。"
                f"当前长度为 {len(v_stripped)}。"
            )
        return v_stripped

    @property
    def cors_origin_list(self) -> list[str]:
        """将逗号分隔的 CORS_ORIGINS 解析为列表。"""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_anthropic(self) -> bool:
        """判断当前是否使用 Anthropic 作为 LLM 提供方。"""
        return self.LLM_PROVIDER == "anthropic"


# 模块级单例——导入时立即加载配置。
# 必填字段缺失时立即抛出 SystemExit，在 FastAPI 应用创建前阻止启动。
try:
    settings = Settings()  # type: ignore[call-arg]
except ValidationError as exc:
    print(f"[配置错误] 配置加载失败，服务无法启动：\n{exc}")
    raise SystemExit(1) from exc
