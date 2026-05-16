# -*- coding: utf-8 -*-
"""신용정보원(내보험다보여) 고객별 자동 ID/PW 생성."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any


class Credit4uConfigError(Exception):
    """신용정보원 연동 설정 오류."""


_ID_PATTERN = re.compile(r"^[a-zA-Z0-9]+$")


def get_credit4u_secret() -> str | None:
    """REDRIBBON_CREDIT4U_SECRET 조회(원문 로그·화면 출력 금지)."""
    value = (os.getenv("REDRIBBON_CREDIT4U_SECRET") or "").strip()
    return value or None


def get_credit4u_id_prefix() -> str:
    prefix = (os.getenv("CREDIT4U_ID_PREFIX") or "rr").strip()
    return prefix or "rr"


def _canonical_customer_payload(customer: dict[str, Any]) -> str:
    name = str(customer.get("name") or "").strip()
    identity = "".join(c for c in str(customer.get("identity") or "") if c.isdigit())
    phone = "".join(c for c in str(customer.get("phone") or "") if c.isdigit())
    return f"{name}|{identity}|{phone}"


def _hmac_digest(customer: dict[str, Any], secret: str) -> str:
    payload = _canonical_customer_payload(customer)
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def validate_credit4u_id(user_id: str) -> bool:
    """신용정보원 ID 규칙: 6~12자, 영문·숫자만."""
    value = (user_id or "").strip()
    if not (6 <= len(value) <= 12):
        return False
    return bool(_ID_PATTERN.fullmatch(value))


def validate_credit4u_password(password: str) -> bool:
    """신용정보원 PW 규칙: 9~20자, 영문·숫자·특수문자 포함."""
    value = password or ""
    if not (9 <= len(value) <= 20):
        return False
    if not re.search(r"[A-Za-z]", value):
        return False
    if not re.search(r"\d", value):
        return False
    if not re.search(r"[^A-Za-z0-9]", value):
        return False
    return True


def _build_credit4u_id(digest: str, prefix: str) -> str:
    """prefix + digest 앞 8자(기본 rr + 8 = 10자). 전체 6~12자."""
    prefix_clean = re.sub(r"[^a-zA-Z0-9]", "", prefix) or "rr"
    if len(prefix_clean) > 6:
        raise ValueError("CREDIT4U_ID_PREFIX is too long; total ID must be 6~12 characters")
    body_len = min(8, 12 - len(prefix_clean))
    if body_len < max(0, 6 - len(prefix_clean)):
        raise ValueError("CREDIT4U_ID_PREFIX is too long; total ID must be 6~12 characters")
    body = digest[:body_len].lower()
    return f"{prefix_clean}{body}"


def _build_credit4u_password(digest: str) -> str:
    """영문 대·소문자, 숫자, 특수문자 포함(결정론적)."""
    segment = re.sub(r"[^a-zA-Z0-9]", "", digest)
    mid = segment[12:18] or "RedRbN"
    digits = f"{int(digest[20:26], 16) % 10000:04d}"
    return f"Aa!{mid}{digits}#"


def _assert_credit4u_credentials(user_id: str, password: str) -> None:
    if not validate_credit4u_id(user_id):
        raise ValueError("신용정보원 아이디 생성 규칙을 확인해야 합니다.")
    if not validate_credit4u_password(password):
        raise ValueError("신용정보원 비밀번호 생성 규칙을 확인해야 합니다.")


def generate_credit4u_credentials(customer: dict[str, Any]) -> dict[str, str]:
    """
    고객별 신용정보원 자동 ID/PW 생성.
    동일 고객 + 동일 secret이면 항상 동일 결과.
    """
    secret = get_credit4u_secret()
    if not secret:
        raise Credit4uConfigError("REDRIBBON_CREDIT4U_SECRET is not configured")

    if not isinstance(customer, dict):
        raise ValueError("customer must be a dict")

    name = str(customer.get("name") or "").strip()
    identity = str(customer.get("identity") or "").strip()
    phone = str(customer.get("phone") or "").strip()
    if not all((name, identity, phone)):
        raise ValueError("customer name, identity, and phone are required")

    digest = _hmac_digest(customer, secret)
    user_id = _build_credit4u_id(digest, get_credit4u_id_prefix())
    password = _build_credit4u_password(digest)
    _assert_credit4u_credentials(user_id, password)
    return {"id": user_id, "password": password}


def credit4u_credentials_debug(
    user_id: str,
    *,
    credential_source: str = "generated",
) -> dict[str, Any]:
    """DEBUG용 — ID 길이·규칙 충족 여부만(원문·digest 미포함)."""
    value = (user_id or "").strip()
    return {
        "generated_id_length": len(value),
        "generated_id_rule_ok": validate_credit4u_id(value),
        "credential_source": credential_source or "—",
    }


def mask_credit4u_id(user_id: str) -> str:
    """DEBUG용 ID 일부 마스킹."""
    value = (user_id or "").strip()
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"
