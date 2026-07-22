"""
认证安全工具：密码哈希（PBKDF2）和 JWT 令牌创建/验证。

使用 Python 标准库 hashlib.pbkdf2_hmac 进行密码哈希（SHA-256，迭代 600000 次），
无需第三方密码库。使用 python-jose 进行 JWT 签名和验证（HS256 算法）。
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from app.config import settings

# JWT 签名算法
ALGORITHM = "HS256"

# PBKDF2 参数
_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 600000
_SALT_LENGTH = 16
_HASH_LENGTH = 32


def _encode_b64(data: bytes) -> str:
    """Base64 URL-safe 编码，去除补位 =。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_b64(s: str) -> bytes:
    """Base64 URL-safe 解码，补回缺失的补位 =。"""
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def hash_password(password: str) -> str:
    """对明文密码进行 PBKDF2-SHA256 哈希。

    格式: $pbkdf2-sha256$iterations$salt_b64$hash_b64
    使用标准库 hashlib，不依赖第三方包。

    Args:
        password: 明文密码。

    Returns:
        编码后的哈希字符串（约 90 字符）。
    """
    salt = os.urandom(_SALT_LENGTH)
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=_HASH_LENGTH,
    )
    return (
        f"$pbkdf2-{_PBKDF2_ALGORITHM}${_PBKDF2_ITERATIONS}$"
        f"{_encode_b64(salt)}${_encode_b64(dk)}"
    )


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配。

    Args:
        plain_password: 明文密码。
        hashed_password: hash_password() 生成的哈希字符串。

    Returns:
        匹配返回 True，否则 False。
    """
    try:
        parts = hashed_password.split("$")
        if len(parts) != 5 or parts[0] != "" or not parts[1].startswith("pbkdf2-"):
            return False
        iterations = int(parts[2])
        salt = _decode_b64(parts[3])
        expected_hash = _decode_b64(parts[4])

        dk = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected_hash),
        )
        return dk == expected_hash
    except (ValueError, IndexError):
        return False


def create_access_token(user_id: str, username: str, role: str) -> str:
    """创建 JWT 访问令牌。

    payload 包含:
      - sub: 用户 ID
      - username: 用户名
      - role: 用户角色（admin / readonly）
      - exp: 过期时间

    Args:
        user_id: 用户 ID。
        username: 用户名。
        role: 用户角色。

    Returns:
        JWT 字符串（三段式，Bearer scheme）。
    """
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """解码并验证 JWT 令牌。

    Args:
        token: JWT 字符串。

    Returns:
        解码后的 payload dict，失败或过期返回 None。
    """
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        return None
