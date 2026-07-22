"""
认证 API 路由。

端点：
  - POST /api/auth/login  用户登录，返回 JWT 令牌
  - GET  /api/auth/me     获取当前登录用户信息
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.security import create_access_token, verify_password
from app.database import get_session
from app.models.schemas import LoginRequest, LoginResponse, UserResponse
from app.models.user import UserModel

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_session),
):
    """用户登录，验证凭据后返回 JWT 令牌。"""
    # 查询用户
    result = await db.execute(
        select(UserModel).where(UserModel.username == body.username)
    )
    user = result.scalar_one_or_none()

    # 验证用户名和密码
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "INVALID_CREDENTIALS",
                "user_message": "用户名或密码错误",
            },
        )

    # 检查账号是否启用
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "USER_INACTIVE",
                "user_message": "账号已被禁用，请联系管理员",
            },
        )

    # 生成 JWT
    access_token = create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
    )

    user_response = UserResponse.model_validate(user)
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=user_response,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: UserModel = Depends(get_current_user),
):
    """获取当前登录用户信息（用于前端初始化 auth store）。"""
    return UserResponse.model_validate(current_user)
