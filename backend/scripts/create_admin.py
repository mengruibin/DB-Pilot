"""创建初始管理员账号。

用法：
  cd backend && python scripts/create_admin.py

直接运行后会交互式输入用户名和密码。
也可作为模块导入调用 create_admin() 函数。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，使 app 模块可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.security import hash_password
from app.database import async_session_factory
from app.models.user import UserModel


async def create_admin(
    username: str = "admin",
    password: str = "",
) -> str:
    """创建管理员用户。

    Args:
        username: 管理员用户名（默认: admin）。
        password: 管理员密码（必须 >= 6 字符）。

    Returns:
        创建成功的用户 ID。

    Raises:
        ValueError: 密码长度不足或用户名已存在。
    """
    if len(password) < 6:
        raise ValueError("密码长度至少 6 字符")

    async with async_session_factory() as db:
        # 检查用户名是否已存在
        from sqlalchemy import select

        result = await db.execute(select(UserModel).where(UserModel.username == username))
        existing = result.scalar_one_or_none()
        if existing is not None:
            raise ValueError(f"用户名 '{username}' 已存在（ID: {existing.id}）")

        # 创建管理员
        user = UserModel(
            username=username,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id


async def main() -> None:
    """交互式创建管理员账号。"""
    print("=" * 50)
    print("  创建 DB-Pilot 管理员账号")
    print("=" * 50)

    username = input("\n管理员用户名 [admin]: ").strip() or "admin"
    password = input("管理员密码（至少 6 位）: ").strip()

    if len(password) < 6:
        print("\n❌ 密码长度至少 6 字符")
        sys.exit(1)

    try:
        user_id = await create_admin(username=username, password=password)
        print(f"\n✅ 管理员账号创建成功！")
        print(f"   用户名: {username}")
        print(f"   用户 ID: {user_id}")
        print(f"   角色: admin")
    except ValueError as e:
        print(f"\n❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
