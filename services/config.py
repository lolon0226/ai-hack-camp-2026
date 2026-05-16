# -*- coding: utf-8 -*-
"""환경변수 상태·검증 — 값은 절대 로그/응답에 포함하지 않는다."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("redribbon.env")


def _filled(name: str) -> bool:
    v = os.getenv(name)
    if v is None:
        return False
    return bool(str(v).strip())


def get_env_status() -> dict[str, bool]:
    """민감값 존재 여부만 bool로 반환한다."""
    encryption = bool(
        _filled("ENCRYPTION_KEY") or _filled("REDDRIBBON_DATA_ENCRYPTION_KEY")
    )
    search_secret = bool(
        _filled("SEARCH_SECRET")
        or _filled("REDDRIBBON_SEARCH_HASH_SECRET")
        or _filled("REDRIBBON_CREDIT4U_SECRET")
    )
    db_configured = bool(_filled("DATABASE_URL") or _filled("REDDRIBBON_STORAGE_DB_PATH"))

    return {
        "CODEF_DEMO_CLIENT_ID": _filled("CODEF_DEMO_CLIENT_ID"),
        "CODEF_DEMO_CLIENT_SECRET": _filled("CODEF_DEMO_CLIENT_SECRET"),
        "CODEF_CLIENT_ID": _filled("CODEF_CLIENT_ID"),
        "CODEF_CLIENT_SECRET": _filled("CODEF_CLIENT_SECRET"),
        "CODEF_PUBLIC_KEY": _filled("CODEF_PUBLIC_KEY"),
        "CODEF_BASE_URL": _filled("CODEF_BASE_URL"),
        "CODEF_USE_DEMO": _filled("CODEF_USE_DEMO"),
        "OPENAI_API_KEY": _filled("OPENAI_API_KEY"),
        "OPENAI_MODEL": _filled("OPENAI_MODEL"),
        "ENCRYPTION_KEY": encryption,
        "SEARCH_SECRET": search_secret,
        "DATABASE_OR_DB_PATH": db_configured,
        "CODEF_HIRA_MEDICAL_PATH": _filled("CODEF_HIRA_MEDICAL_PATH"),
        "CODEF_INSURANCE_CONTRACT_PATH": _filled("CODEF_INSURANCE_CONTRACT_PATH"),
        "PRINT_RECEIVER_OCR_DPI": _filled("PRINT_RECEIVER_OCR_DPI"),
        "PRINT_RECEIVER_OCR_MAX_PAGES": _filled("PRINT_RECEIVER_OCR_MAX_PAGES"),
        "PRINT_RECEIVER_TESSERACT_CMD": _filled("PRINT_RECEIVER_TESSERACT_CMD")
        or _filled("TESSERACT_CMD"),
        "SESSION_SECRET": _filled("SESSION_SECRET"),
        "REDRIBBON_CREDIT4U_SECRET": _filled("REDRIBBON_CREDIT4U_SECRET"),
    }


def log_env_status_safely() -> None:
    """시작 시 설정 여부만 로그한다. 값/접두·접미는 출력하지 않는다."""
    for key, ok in sorted(get_env_status().items()):
        logger.warning("%s: %s", key, "configured" if ok else "missing")


def validate_startup_env() -> list[str]:
    """
    본선 연동에 권장되는 항목 누락 목록(이름만)을 반환한다.
    데모 CODEF 키는 선택이며, 메인 CODEF·암호화·OpenAI는 권장.
    """
    missing: list[str] = []
    if not _filled("CODEF_CLIENT_ID"):
        missing.append("CODEF_CLIENT_ID")
    if not _filled("CODEF_CLIENT_SECRET"):
        missing.append("CODEF_CLIENT_SECRET")
    if not _filled("CODEF_PUBLIC_KEY"):
        missing.append("CODEF_PUBLIC_KEY")
    if not _filled("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not (
        _filled("ENCRYPTION_KEY") or _filled("REDDRIBBON_DATA_ENCRYPTION_KEY")
    ):
        missing.append("REDDRIBBON_DATA_ENCRYPTION_KEY (또는 ENCRYPTION_KEY)")
    if not (
        _filled("SEARCH_SECRET")
        or _filled("REDDRIBBON_SEARCH_HASH_SECRET")
        or _filled("REDRIBBON_CREDIT4U_SECRET")
    ):
        missing.append(
            "REDDRIBBON_SEARCH_HASH_SECRET 또는 SEARCH_SECRET 또는 REDRIBBON_CREDIT4U_SECRET"
        )
    if missing:
        logger.warning(
            "환경변수 권장 항목 누락(값은 기록하지 않음): %s",
            ", ".join(missing),
        )
    strict = (os.getenv("STRICT_ENV") or "").strip().lower() in ("1", "true", "yes")
    if strict and missing:
        raise RuntimeError(
            "STRICT_ENV=1 인데 필수 환경변수가 누락되었습니다. 변수 이름만 확인하세요: "
            + ", ".join(missing)
        )
    return missing


def mask_sensitive_in_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """디버그용으로 dict 내 민감 키 이름만 남기고 값은 마스킹한다."""
    sensitive_substrings = (
        "secret",
        "token",
        "password",
        "api_key",
        "apikey",
        "authorization",
        "ssn",
        "rrn",
        "resident",
        "주민",
        "phone",
        "mobile",
        "연락",
    )
    out: dict[str, Any] = {}
    for k, v in data.items():
        lk = k.lower()
        if any(s in lk for s in sensitive_substrings):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = mask_sensitive_in_mapping(v)
        elif isinstance(v, list):
            out[k] = [
                mask_sensitive_in_mapping(x) if isinstance(x, dict) else x for x in v
            ]
        else:
            out[k] = v
    return out
