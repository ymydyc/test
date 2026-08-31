"""鉴权安全工具（阶段七）：密码哈希 + JWT 签发/校验。

- 密码：pbkdf2_hmac（标准库），无第三方依赖。
- JWT：HS256 手工实现（hmac + base64url + json），避免引入 PyJWT 依赖；足够本项目使用。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from app.core.config import settings

_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16
_HASH_BYTES = 32


# ---------- 密码 ----------
def hash_password(password: str) -> str:
    """返回 `pbkdf2_sha256$iter$salt$hash`（salt/hash 均 base64）。"""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, _HASH_BYTES)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations), _HASH_BYTES)
        return hmac.compare_digest(dk, base64.urlsafe_b64decode(hash_b64.encode()))
    except Exception:
        return False


# ---------- JWT（HS256） ----------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(header_b64: str, payload_b64: str) -> bytes:
    data = f"{header_b64}.{payload_b64}".encode()
    return hmac.new(settings.jwt_secret.encode(), data, hashlib.sha256).digest()


def create_token(payload: dict[str, Any], ttl_seconds: int) -> str:
    """签发 HS256 JWT（含 iat/exp）。"""
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    now = int(time.time())
    body = json.dumps({**payload, "iat": now, "exp": now + ttl_seconds}, separators=(",", ":")).encode()
    payload_b64 = _b64url(body)
    sig = _sign(header_b64, payload_b64)
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def create_access_token(user_id: int, workspace_id: int, jti: str, ttl: int | None = None) -> str:
    """签发访问令牌：sub=user_id，ws=workspace_id，jti 用于会话吊销。"""
    return create_token(
        {"sub": user_id, "ws": workspace_id, "jti": jti},
        ttl_seconds=ttl or settings.jwt_access_ttl_seconds,
    )


def decode_token(token: str) -> dict[str, Any] | None:
    """校验签名与过期；返回 payload，非法返回 None。"""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        expected = _sign(header_b64, payload_b64)
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def sha256(text: str) -> str:
    """通用去敏/哈希（如会话 jti 存储）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()