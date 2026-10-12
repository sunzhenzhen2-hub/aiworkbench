"""两台机器互相认对方的签名工具。密钥不写进页面。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Mapping

MAX_SKEW_SEC = 60
SERVICE_WORKBENCH = "workbench"
SERVICE_DSH = "dsh-gate"
DEFAULT_ALLOWLIST = ("30.251.255.64", "30.251.255.132", "30.251.255.142", "127.0.0.1")

_used_nonces: dict[str, float] = {}


def normalize_ip(ip: str | None) -> str:
    text = (ip or "").strip()
    if text.startswith("::ffff:"):
        return text[7:]
    return text


def parse_allowlist(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_ALLOWLIST)
    return [item.strip() for item in raw.replace(",", " ").split() if item.strip()]


def ip_allowed(ip: str | None, allowlist: list[str] | None = None) -> bool:
    allowed = allowlist or list(DEFAULT_ALLOWLIST)
    normalized = normalize_ip(ip)
    return normalized in allowed or normalized in {"127.0.0.1", "::1"}


def body_digest(body: str | bytes) -> str:
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def canonical_string(method: str, path: str, timestamp: str, nonce: str, digest: str) -> str:
    return "\n".join([method.upper(), path, str(timestamp), nonce, digest])


def sign_canonical(secret: str, canonical: str) -> str:
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _cleanup_nonces(now: float) -> None:
    expired = [nonce for nonce, exp in _used_nonces.items() if exp <= now]
    for nonce in expired:
        _used_nonces.pop(nonce, None)


def build_signed_headers(secret: str, service_id: str, method: str, path: str, body: str = "") -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    digest = body_digest(body)
    signature = sign_canonical(secret, canonical_string(method, path, timestamp, nonce, digest))
    return {
        "content-type": "application/json; charset=utf-8",
        "x-service-id": service_id,
        "x-timestamp": timestamp,
        "x-nonce": nonce,
        "x-signature": signature,
    }


def header_of(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value or "")
    return ""


def verify_signed_request(
    *,
    secret: str,
    expected_service_id: str,
    method: str,
    path: str,
    body: str = "",
    headers: Mapping[str, str],
    ip: str = "",
    allowlist: list[str] | None = None,
) -> tuple[bool, str]:
    if not secret:
        return False, "service auth not configured"
    if not ip_allowed(ip, allowlist):
        return False, "ip not allowed"
    if header_of(headers, "x-service-id") != expected_service_id:
        return False, "unknown service"
    try:
        timestamp = int(header_of(headers, "x-timestamp"))
    except ValueError:
        return False, "bad timestamp"
    if abs(int(time.time()) - timestamp) > MAX_SKEW_SEC:
        return False, "timestamp expired"
    nonce = header_of(headers, "x-nonce")
    if len(nonce) < 16:
        return False, "bad nonce"
    now = time.time()
    _cleanup_nonces(now)
    if nonce in _used_nonces:
        return False, "nonce reused"
    digest = body_digest(body)
    expected = sign_canonical(secret, canonical_string(method, path, str(timestamp), nonce, digest))
    actual = header_of(headers, "x-signature")
    if not actual or not hmac.compare_digest(actual, expected):
        return False, "bad signature"
    _used_nonces[nonce] = now + MAX_SKEW_SEC * 2
    return True, ""


def reset_for_tests() -> None:
    _used_nonces.clear()
