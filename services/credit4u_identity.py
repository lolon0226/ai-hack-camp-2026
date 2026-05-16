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


# 생성 알고리즘 버전 — 변경 시 DB 저장본 마이그레이션·버전 상향 필수.
# v1 규칙(고정): ID = prefix(rr) + HMAC digest 앞 8자(총 10자),
# attempt_no=0이면 동일 고객·secret마다 항상 동일 ID/PW, attempt_no>0은 회원가입 재시도 전용.
CREDIT4U_CREDENTIAL_VERSION = "v1"

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


def _hmac_digest_with_attempt(
    customer: dict[str, Any],
    secret: str,
    attempt_no: int,
) -> str:
    payload = f"{_canonical_customer_payload(customer)}|attempt:{int(attempt_no)}"
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


def password_contains_user_id(password: str, user_id: str) -> bool:
    """비밀번호에 아이디가 포함되면 True."""
    uid = (user_id or "").strip().lower()
    if not uid or len(uid) < 3:
        return False
    return uid in (password or "").lower()


def _credentials_from_attempt_digest(
    customer: dict[str, Any],
    secret: str,
    attempt_no: int,
    *,
    previous_id: str | None = None,
) -> dict[str, str]:
    digest = _hmac_digest_with_attempt(customer, secret, attempt_no)
    user_id = _build_credit4u_id(digest, get_credit4u_id_prefix())
    prev = (previous_id or "").strip()
    if prev and user_id == prev:
        digest = _hmac_digest_with_attempt(customer, secret, attempt_no + 1000)
        user_id = _build_credit4u_id(digest, get_credit4u_id_prefix())
    password = _build_credit4u_password(digest)
    if password_contains_user_id(password, user_id):
        digest = _hmac_digest_with_attempt(customer, secret, attempt_no + 2000)
        password = _build_credit4u_password(digest)
    _assert_credit4u_credentials(user_id, password)
    if password_contains_user_id(password, user_id):
        raise ValueError("신용정보원 비밀번호에 아이디가 포함되지 않아야 합니다.")
    return {
        "id": user_id,
        "password": password,
        "credential_version": CREDIT4U_CREDENTIAL_VERSION,
    }


def regenerate_credit4u_credentials(
    customer: dict[str, Any],
    attempt_no: int,
    *,
    previous_id: str | None = None,
) -> dict[str, str]:
    """attempt_no 기준 ID·비밀번호 재생성(이전 ID와 달라야 함)."""
    secret = get_credit4u_secret()
    if not secret:
        raise Credit4uConfigError("REDRIBBON_CREDIT4U_SECRET is not configured")
    if not isinstance(customer, dict):
        raise ValueError("customer must be a dict")
    creds = _credentials_from_attempt_digest(
        customer, secret, attempt_no, previous_id=previous_id
    )
    creds["credential_version"] = CREDIT4U_CREDENTIAL_VERSION
    return creds


def regenerate_credit4u_credentials_for_signup(
    customer: dict[str, Any],
    attempt_no: int,
    *,
    previous_id: str | None = None,
) -> dict[str, str]:
    """register 재시도용 — regenerate_credit4u_credentials 별칭."""
    return regenerate_credit4u_credentials(
        customer, attempt_no, previous_id=previous_id
    )


def regenerate_credit4u_id(
    customer: dict[str, Any],
    attempt_no: int,
    *,
    previous_id: str | None = None,
) -> str:
    return regenerate_credit4u_credentials(
        customer, attempt_no, previous_id=previous_id
    )["id"]


def generate_credit4u_credentials(
    customer: dict[str, Any],
    attempt_no: int = 0,
) -> dict[str, str]:
    """
    고객별 신용정보원 자동 ID/PW 생성(CREDIT4U_CREDENTIAL_VERSION=v1).
    attempt_no=0: 동일 고객·REDRIBBON_CREDIT4U_SECRET이면 항상 동일 ID/PW.
    attempt_no>=1: 회원가입 중복·형식 오류 재시도 시에만 사용.
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

    if int(attempt_no) <= 0:
        digest = _hmac_digest(customer, secret)
        user_id = _build_credit4u_id(digest, get_credit4u_id_prefix())
        password = _build_credit4u_password(digest)
        _assert_credit4u_credentials(user_id, password)
        return {
            "id": user_id,
            "password": password,
            "credential_version": CREDIT4U_CREDENTIAL_VERSION,
        }

    creds = _credentials_from_attempt_digest(customer, secret, int(attempt_no))
    creds["credential_version"] = CREDIT4U_CREDENTIAL_VERSION
    return creds


def mask_email_for_debug(email: str) -> str:
    """DEBUG용 — 도메인만 표시."""
    value = (email or "").strip().lower()
    if "@" not in value:
        return "—"
    local, domain = value.rsplit("@", 1)
    if len(local) <= 1:
        masked_local = "*"
    else:
        masked_local = f"{local[0]}***"
    return f"{masked_local}@{domain}"


def credit4u_credentials_debug(
    user_id: str,
    *,
    password: str = "",
    credential_source: str = "generated",
) -> dict[str, Any]:
    """DEBUG용 — ID/PW 규칙 충족 여부만(원문·digest 미포함)."""
    value = (user_id or "").strip()
    pw = password or ""
    return {
        "generated_id_length": len(value),
        "generated_id_rule_ok": validate_credit4u_id(value),
        "generated_password_rule_ok": validate_credit4u_password(pw) if pw else False,
        "credential_source": credential_source or "—",
    }


def mask_credit4u_id(user_id: str) -> str:
    """DEBUG용 ID 일부 마스킹."""
    value = (user_id or "").strip()
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"


def persist_credit4u_credentials(
    customer: dict[str, Any],
    credentials: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> str:
    """고객별 신용정보원 계정 영구 저장(SQLite, 비밀번호 암호화)."""
    from services.persistent_store import (
        PersistentStoreConfigError,
        save_credit4u_credentials,
    )

    return save_credit4u_credentials(customer, credentials, metadata)


def verify_persisted_credit4u_credentials(
    customer: dict[str, Any],
    *,
    expected_id: str | None = None,
) -> bool:
    """저장 후 DB 재조회 검증."""
    from services.persistent_store import verify_credit4u_credentials_saved

    try:
        return verify_credit4u_credentials_saved(customer, expected_id=expected_id)
    except PersistentStoreConfigError:
        return False


def restore_credit4u_credentials(customer: dict[str, Any]) -> dict[str, Any] | None:
    """저장된 신용정보원 계정 복원."""
    from services.persistent_store import (
        PersistentStoreConfigError,
        load_credit4u_credentials,
    )

    try:
        return load_credit4u_credentials(customer)
    except PersistentStoreConfigError:
        return None
