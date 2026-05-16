# -*- coding: utf-8 -*-
"""
준비된 보험가입이력 원부·시연용 저장본 보호.

초기화(reset/clear) 시 이 모듈의 assert_* 를 반드시 거칩니다.
원부 JSON/DB 백업 파일 삭제·빈 덮어쓰기·보존 source 행 DELETE 를 차단합니다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# 명시 보호 경로(절대·상대). 초기화 대상 목록에 포함되면 예외.
PROTECTED_INSURANCE_SOURCE_PATHS: tuple[str, ...] = (
    r"C:\Users\DELL\Desktop\success_insurance_record_export.json",
    str(DATA_DIR / "success_insurance_record_export.json"),
)

# data/ 이하 파일명에 포함되면 보험 원부로 간주해 보호(.json/.db 등)
PROTECTED_INSURANCE_FILENAME_MARKERS: tuple[str, ...] = (
    "backup",
    "data_before_final",
    "prepared",
    "seed",
    "demo",
    "success_insurance",
    "insurance_record_export",
    "insurance_record",
)

PROTECTED_INSURANCE_FILE_SUFFIXES: frozenset[str] = frozenset(
    {".json", ".db", ".sqlite", ".sqlite3"}
)

# DB insurance_records.source — 이 값(또는 접두사)은 DELETE 대상에서 제외
PRESERVED_INSURANCE_RECORD_SOURCES: frozenset[str] = frozenset(
    {
        "prepared_demo_record",
        "credit4u_prepared_demo_record",
        "credit4u_import_success_program",
        "credit4u_import",
        "credit4u_prepared_demo",
        "saved_imported",
    }
)

PRESERVED_INSURANCE_RECORD_SOURCE_PREFIXES: tuple[str, ...] = (
    "prepared_demo",
    "credit4u_prepared",
    "credit4u_import",
    "credit4u_prepared_demo",
)

# 운영 DB 본체는 초기화 스크립트가 통째로 삭제하지 않음(행 단위만). 백업 DB 파일은 보호.
PROTECTED_DB_FILENAME_MARKERS: tuple[str, ...] = (
    "backup",
    "data_before_final",
    "prepared",
    "seed",
    "demo",
    "before_credit4u_reset",
    "success_insurance",
)


class InsuranceSourceProtectionError(RuntimeError):
    """보험가입이력 원부 보호 위반."""


def is_preserved_insurance_record_source(source: str | None) -> bool:
    """재주입·시연용으로 보존해야 하는 insurance_records.source 여부."""
    value = (source or "").strip().lower()
    if not value:
        return False
    if value in PRESERVED_INSURANCE_RECORD_SOURCES:
        return True
    return any(value.startswith(prefix) for prefix in PRESERVED_INSURANCE_RECORD_SOURCE_PREFIXES)


def _normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def is_protected_insurance_file_path(path: str | Path) -> bool:
    """준비·백업·시드 보험 원부 파일 경로 여부."""
    resolved = _normalize_path(path)
    for raw in PROTECTED_INSURANCE_SOURCE_PATHS:
        if not raw:
            continue
        try:
            if resolved == _normalize_path(raw):
                return True
        except OSError:
            continue
    if resolved.suffix.lower() not in PROTECTED_INSURANCE_FILE_SUFFIXES:
        return False
    name_lower = resolved.name.lower()
    if any(marker in name_lower for marker in PROTECTED_INSURANCE_FILENAME_MARKERS):
        return True
    try:
        if DATA_DIR.resolve() in resolved.parents or resolved.parent == DATA_DIR.resolve():
            if "insurance" in name_lower and resolved.suffix.lower() == ".json":
                return True
    except OSError:
        pass
    return False


def is_protected_database_file_path(path: str | Path) -> bool:
    """DB 백업·시드 파일 삭제 금지 대상."""
    resolved = _normalize_path(path)
    if resolved.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        return False
    name_lower = resolved.name.lower()
    if name_lower == "redribbon_final.db":
        return False
    return any(marker in name_lower for marker in PROTECTED_DB_FILENAME_MARKERS)


def iter_data_dir_protected_insurance_files() -> list[Path]:
    """data/ 아래 마커 규칙에 해당하는 보호 파일 목록."""
    found: list[Path] = []
    if not DATA_DIR.is_dir():
        return found
    for path in DATA_DIR.rglob("*"):
        if path.is_file() and is_protected_insurance_file_path(path):
            found.append(path)
    return found


def all_protected_insurance_file_paths() -> list[Path]:
    """명시 경로 + data/ 스캔 결과(존재하는 파일만)."""
    paths: dict[str, Path] = {}
    for raw in PROTECTED_INSURANCE_SOURCE_PATHS:
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            paths[str(_normalize_path(candidate))] = _normalize_path(candidate)
    env_path = (os.getenv("PREPARED_INSURANCE_RECORD_JSON") or "").strip()
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file():
            paths[str(_normalize_path(candidate))] = _normalize_path(candidate)
    for path in iter_data_dir_protected_insurance_files():
        paths[str(path)] = path
    return list(paths.values())


def assert_reset_paths_safe(paths: Iterable[str | Path], *, operation: str = "reset") -> None:
    """초기화·삭제 대상 경로에 보호 원부가 포함되면 예외."""
    blocked: list[str] = []
    for path in paths:
        if not path:
            continue
        resolved = _normalize_path(path)
        if is_protected_insurance_file_path(resolved) or is_protected_database_file_path(
            resolved
        ):
            blocked.append(str(resolved))
    if blocked:
        raise InsuranceSourceProtectionError(
            f"{operation} 작업에 보호된 보험가입이력 원부 경로가 포함되어 중단했습니다: "
            + ", ".join(blocked)
        )


def assert_safe_insurance_file_delete(path: str | Path) -> None:
    """보호 원부 파일 삭제 차단."""
    if is_protected_insurance_file_path(path) or is_protected_database_file_path(path):
        raise InsuranceSourceProtectionError(
            f"보호된 보험가입이력 원부 파일은 삭제할 수 없습니다: {path}"
        )


def assert_safe_insurance_file_write(
    path: str | Path,
    *,
    content: bytes | str | None = None,
    min_bytes: int = 32,
) -> None:
    """보호 원부를 빈/과소 payload 로 덮어쓰기 차단."""
    if not is_protected_insurance_file_path(path):
        return
    if content is None:
        raise InsuranceSourceProtectionError(
            f"보호된 보험가입이력 JSON 에 빈 덮어쓰기는 허용되지 않습니다: {path}"
        )
    size = len(content) if isinstance(content, (bytes, bytearray)) else len(
        str(content).encode("utf-8")
    )
    if size < min_bytes:
        raise InsuranceSourceProtectionError(
            f"보호된 보험가입이력 JSON 에 과소한 덮어쓰기는 허용되지 않습니다: {path}"
        )


def sql_preserved_insurance_source_clause(*, alias: str = "") -> tuple[str, list[str]]:
    """DELETE … WHERE 용: 보존 source 제외 조건과 바인딩 값."""
    prefix = f"{alias}." if alias else ""
    sources = sorted(PRESERVED_INSURANCE_RECORD_SOURCES)
    placeholders = ", ".join("?" for _ in sources)
    clause = f"{prefix}source NOT IN ({placeholders})"
    for src_prefix in PRESERVED_INSURANCE_RECORD_SOURCE_PREFIXES:
        clause += f" AND {prefix}source NOT LIKE ?"
    params: list[str] = list(sources)
    params.extend(f"{src_prefix}%" for src_prefix in PRESERVED_INSURANCE_RECORD_SOURCE_PREFIXES)
    return clause, params


def reset_scope_summary() -> dict[str, Any]:
    """문서·DEBUG용 초기화 허용/보존 범위 요약."""
    return {
        "protected_files": [str(p) for p in all_protected_insurance_file_paths()],
        "protected_source_values": sorted(PRESERVED_INSURANCE_RECORD_SOURCES),
        "allowed_on_reset": [
            "FLOW_STORE 보험 단계·DEBUG 필드",
            "credit4u_credentials 전체 또는 고객별",
            "insurance_records 중 보존 source 가 아닌 행(고객별 가능)",
        ],
        "forbidden_on_reset": [
            "PROTECTED_INSURANCE_SOURCE_PATHS 및 data/ 백업·prepared·seed·demo 원부 파일",
            "보존 source 의 insurance_records 행",
            "redribbon_final.db 파일 자체 삭제(백업 DB 포함)",
        ],
    }
