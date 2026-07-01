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
        description="意图分类的轻量模型（PRD §6.1 要求）。",
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

    # ==================== 内部数据库 ====================
    DATABASE_URL: str = Field(
        default="",
        description="内部数据库连接串（支持 mysql+aiomysql 或 sqlite+aiosqlite）。必填。"
        "示例：mysql+aiomysql://root:password@127.0.0.1:3306/db_pilot",
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

    # ==================== 日志与可观测性 ====================
    LOG_LEVEL: str = Field(
        default="INFO",
        description="日志级别：DEBUG / INFO / WARNING / ERROR。",
    )
    LOG_FORMAT: str = Field(
        default="text",
        description="日志输出格式：json（生产）或 text（开发彩色控制台）。",
    )
    LOG_FILE: str | None = Field(
        default=None,
        description="日志文件路径（可选）。为空时仅输出到 stderr。",
    )
    LOG_TRACE_ENABLED: bool = Field(
        default=True,
        description="是否启用链路追踪。关闭后 trace_id 仍生成但不强制透传。",
    )

    # ==================== 校验 ====================

    @field_validator("LLM_API_KEY", "LLM_MODEL", "DATABASE_URL")
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
