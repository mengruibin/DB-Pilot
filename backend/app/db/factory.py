"""
适配器工厂——按 db_type 懒加载并创建适配器实例。

依据 AGENTS.md §数据库驱动：
  - 适配器层不 import 目标数据库驱动（懒加载）
  - connect() 失败时给出明确安装提示
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.schemas import ConnectionCreateRequest

if TYPE_CHECKING:
    from app.db.base import BaseAdapter


# 支持的数据类型与对应适配器模块路径（懒加载）
_ADAPTER_MAP: dict[str, str] = {
    "mysql": "app.db.mysql",
    "postgresql": "app.db.postgresql",
    "oracle": "app.db.oracle",
}

# 各驱动安装提示
_DRIVER_HINTS: dict[str, str] = {
    "mysql": "请安装 aiomysql: pip install aiomysql",
    "postgresql": "请安装 asyncpg: pip install asyncpg",
    "oracle": "请安装 oracledb>=2.0: pip install oracledb",
}


class AdapterFactory:
    """适配器工厂，负责按数据库类型创建适配器实例。

    用法：
        adapter = AdapterFactory.create("mysql", config)
        await adapter.connect(config)
    """

    @staticmethod
    def create(db_type: str, config: ConnectionCreateRequest) -> BaseAdapter:
        """创建并返回对应数据库类型的适配器实例。

        懒加载驱动模块（不在 import 时加载）。
        db_type 非法时抛出 ValueError 并列出可接受值。

        Args:
            db_type: 数据库类型（mysql / postgresql / oracle）。
            config: 包含密码在内的完整连接配置。

        Returns:
            BaseAdapter 子类实例。

        Raises:
            ValueError: db_type 不在支持列表中时抛出。
        """
        db_type = db_type.lower().strip()

        # 查找模块路径
        module_path = _ADAPTER_MAP.get(db_type)
        if module_path is None:
            supported = ", ".join(sorted(_ADAPTER_MAP.keys()))
            raise ValueError(
                f"不支持的数据库类型：'{db_type}'。"
                f"支持的类型：{supported}。"
                f"驱动安装提示：{_DRIVER_HINTS.get(db_type, '')}"
            )

        # 懒加载驱动模块
        # AGENTS.md §数据库驱动：适配器层不 import 目标数据库驱动
        try:
            import importlib

            module = importlib.import_module(module_path)
        except ImportError as exc:
            hint = _DRIVER_HINTS.get(db_type, f"请安装对应数据库驱动: pip install {module_path}")
            raise ImportError(
                f"无法加载适配器模块 '{module_path}'：{exc}。{hint}"
            ) from exc

        # 获取适配器类（约定类名为 <Type>Adapter，如 MySQLAdapter）
        adapter_class_name = f"{db_type.capitalize()}Adapter"
        adapter_class = getattr(module, adapter_class_name, None)

        if adapter_class is None:
            raise AttributeError(
                f"适配器模块 '{module_path}' 中未找到类 '{adapter_class_name}'。"
            )

        return adapter_class(config)

    @staticmethod
    def get_supported_types() -> list[str]:
        """返回当前支持的所有数据库类型列表。"""
        return sorted(_ADAPTER_MAP.keys())
