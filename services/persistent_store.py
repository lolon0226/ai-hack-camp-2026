# -*- coding: utf-8 -*-
"""본선용 SQLite 영구 저장(진료내역·고객 식별키)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "redribbon_final.db"

MEDICAL_SOURCE_CODEF_HIRA = "codef_hira"


class PersistentStoreConfigError(RuntimeError):
    """REDRIBBON_SEARCH_HASH_SECRET 등 필수 설정 누락."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_search_hash_secret_configured() -> bool:
    return bool((os.getenv("REDRIBBON_SEARCH_HASH_SECRET") or "").strip())


def _search_hash_secret() -> str:
    secret = (os.getenv("REDRIBBON_SEARCH_HASH_SECRET") or "").strip()
    if not secret:
        raise PersistentStoreConfigError("REDRIBBON_SEARCH_HASH_SECRET is missing")
    return secret


def normalize_customer_fields(customer: dict[str, Any]) -> dict[str, str]:
    """customer_key 일치를 위해 이름·주민·전화 정규화(원문 DB 미저장)."""
    return {
        "name": str(customer.get("name") or "").strip(),
        "identity": re.sub(r"\D", "", str(customer.get("identity") or ""))[:13],
        "phone": re.sub(r"\D", "", str(customer.get("phone") or "")),
    }


def can_make_customer_key(customer: dict[str, Any]) -> bool:
    try:
        make_customer_key(customer)
        return True
    except PersistentStoreConfigError:
        return False


def ensure_storage() -> None:
    """data/ 및 SQLite 테이블 생성."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                identity_hash TEXT NOT NULL,
                phone_hash TEXT NOT NULL,
                email TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS medical_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_key TEXT NOT NULL,
                flow_id TEXT NOT NULL,
                basic_json TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                prescribe_json TEXT NOT NULL,
                counts_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_medical_records_customer_created
                ON medical_records (customer_key, created_at DESC);

            CREATE TABLE IF NOT EXISTS customer_flows (
                flow_id TEXT PRIMARY KEY,
                customer_key TEXT NOT NULL,
                current_step INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def make_customer_key(customer: dict[str, Any]) -> str:
    """sha256(name + identity + phone + REDRIBBON_SEARCH_HASH_SECRET)."""
    secret = _search_hash_secret()
    fields = normalize_customer_fields(customer)
    raw = f"{fields['name']}{fields['identity']}{fields['phone']}{secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _identity_hash(identity: str) -> str:
    secret = _search_hash_secret()
    return hashlib.sha256(f"{identity}{secret}".encode("utf-8")).hexdigest()


def _phone_hash(phone: str) -> str:
    secret = _search_hash_secret()
    return hashlib.sha256(f"{phone}{secret}".encode("utf-8")).hexdigest()


def save_customer(customer: dict[str, Any]) -> str:
    """고객 메타 저장(주민·전화 원문 미저장). customer_key 반환."""
    customer_key = make_customer_key(customer)
    fields = normalize_customer_fields(customer)
    name = fields["name"]
    identity = fields["identity"]
    phone = fields["phone"]
    email = str(customer.get("email") or "").strip()
    now = _utc_now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO customers (customer_key, name, identity_hash, phone_hash, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_key) DO UPDATE SET
                name = excluded.name,
                identity_hash = excluded.identity_hash,
                phone_hash = excluded.phone_hash,
                email = excluded.email
            """,
            (
                customer_key,
                name,
                _identity_hash(identity),
                _phone_hash(phone),
                email,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return customer_key


def upsert_customer_flow(
    flow_id: str,
    customer_key: str,
    *,
    current_step: int | None = None,
) -> None:
    now = _utc_now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT flow_id FROM customer_flows WHERE flow_id = ?",
            (flow_id,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE customer_flows
                SET customer_key = ?, current_step = COALESCE(?, current_step), updated_at = ?
                WHERE flow_id = ?
                """,
                (customer_key, current_step, now, flow_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO customer_flows (flow_id, customer_key, current_step, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (flow_id, customer_key, current_step, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def save_medical_records(
    customer: dict[str, Any],
    flow_id: str,
    basic: Any,
    detail: Any,
    prescribe: Any,
    counts: dict[str, Any],
    *,
    source: str = MEDICAL_SOURCE_CODEF_HIRA,
) -> str:
    """진료내역 JSON 저장. secret 없으면 저장 중단. customer_key 반환."""
    customer_key = make_customer_key(customer)
    save_customer(customer)
    now = _utc_now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO medical_records (
                customer_key, flow_id, basic_json, detail_json, prescribe_json,
                counts_json, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_key,
                flow_id,
                json.dumps(basic, ensure_ascii=False),
                json.dumps(detail, ensure_ascii=False),
                json.dumps(prescribe, ensure_ascii=False),
                json.dumps(counts, ensure_ascii=False),
                source,
                now,
            ),
        )
        conn.commit()
        upsert_customer_flow(flow_id, customer_key, current_step=3)
    finally:
        conn.close()
    logger.info(
        "medical_records saved flow_id=%s source=%s counts_basic=%s",
        flow_id,
        source,
        (counts or {}).get("basic", 0) if isinstance(counts, dict) else 0,
    )
    return customer_key


def _row_to_medical_bundle(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "flow_id": row[0],
        "basic": json.loads(row[1] or "[]"),
        "detail": json.loads(row[2] or "[]"),
        "prescribe": json.loads(row[3] or "[]"),
        "counts": json.loads(row[4] or "{}"),
        "source": row[5],
        "created_at": row[6],
    }


def load_latest_medical_records(customer: dict[str, Any]) -> dict[str, Any] | None:
    """customer_key 기준 최신 진료내역 1건(서버 재시작과 무관)."""
    try:
        customer_key = make_customer_key(customer)
    except PersistentStoreConfigError:
        return None

    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT flow_id, basic_json, detail_json, prescribe_json, counts_json, source, created_at
            FROM medical_records
            WHERE customer_key = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (customer_key,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    return _row_to_medical_bundle(row)


def has_medical_records(customer: dict[str, Any]) -> bool:
    """customer_key 기준 저장된 진료내역 존재 여부."""
    if not is_search_hash_secret_configured():
        return False
    return load_latest_medical_records(customer) is not None


def get_storage_health() -> dict[str, Any]:
    """DEBUG용 DB 상태(민감정보 없음)."""
    ensure_storage()
    exists = DB_PATH.is_file()
    customers_count = 0
    medical_records_count = 0
    latest_medical_created_at: str | None = None
    if exists:
        conn = sqlite3.connect(DB_PATH)
        try:
            row = conn.execute("SELECT COUNT(*) FROM customers").fetchone()
            customers_count = int(row[0] or 0) if row else 0
            row = conn.execute("SELECT COUNT(*) FROM medical_records").fetchone()
            medical_records_count = int(row[0] or 0) if row else 0
            row = conn.execute(
                "SELECT created_at FROM medical_records ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if row and row[0]:
                latest_medical_created_at = str(row[0])
        finally:
            conn.close()
    return {
        "db_path": str(DB_PATH),
        "db_exists": exists,
        "customers_count": customers_count,
        "medical_records_count": medical_records_count,
        "latest_medical_created_at": latest_medical_created_at or "—",
        "search_hash_secret_configured": is_search_hash_secret_configured(),
    }
