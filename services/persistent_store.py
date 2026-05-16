# -*- coding: utf-8 -*-
"""본선용 SQLite 영구 저장(진료내역·고객 식별키)."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "redribbon_final.db"

MEDICAL_SOURCE_CODEF_HIRA = "codef_hira"
INSURANCE_SOURCE_CODEF_CREDIT4U = "codef_credit4u"
CREDENTIAL_SOURCE_GENERATED = "generated"


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

            CREATE TABLE IF NOT EXISTS credit4u_credentials (
                customer_key TEXT PRIMARY KEY,
                credit4u_id TEXT NOT NULL,
                password_encrypted TEXT NOT NULL,
                credential_source TEXT NOT NULL,
                credential_version TEXT NOT NULL DEFAULT 'v1',
                id_attempt_no INTEGER NOT NULL DEFAULT 0,
                email_domain TEXT,
                register_completed_at TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS insurance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_key TEXT NOT NULL,
                flow_id TEXT NOT NULL,
                raw_response_json TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_insurance_records_customer_created
                ON insurance_records (customer_key, created_at DESC);
            """
        )
        _migrate_credit4u_credentials_columns(conn)
        conn.commit()
    finally:
        conn.close()


_CREDIT4U_CREDENTIALS_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("credential_version", "TEXT NOT NULL DEFAULT 'v1'"),
    ("credential_source", "TEXT NOT NULL DEFAULT 'generated'"),
    ("id_attempt_no", "INTEGER NOT NULL DEFAULT 0"),
    ("email_domain", "TEXT"),
    ("register_completed_at", "TEXT"),
    ("updated_at", "TEXT"),
    ("metadata_json", "TEXT"),
)


def _migrate_credit4u_credentials_columns(conn: sqlite3.Connection) -> None:
    """기존 DB에 credit4u_credentials 누락 컬럼만 ALTER TABLE로 추가."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='credit4u_credentials'"
    ).fetchone()
    if not table_exists:
        return
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(credit4u_credentials)").fetchall()
    }
    # 레거시 별칭: 과거 스키마에 credit4u_id_attempt_no만 있는 경우 id_attempt_no로 대체
    if "credit4u_id_attempt_no" in columns and "id_attempt_no" not in columns:
        try:
            conn.execute(
                "ALTER TABLE credit4u_credentials "
                "RENAME COLUMN credit4u_id_attempt_no TO id_attempt_no"
            )
        except sqlite3.OperationalError:
            conn.execute(
                "ALTER TABLE credit4u_credentials "
                "ADD COLUMN id_attempt_no INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute(
                "UPDATE credit4u_credentials "
                "SET id_attempt_no = credit4u_id_attempt_no "
                "WHERE id_attempt_no = 0"
            )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(credit4u_credentials)").fetchall()
        }
    for col_name, col_def in _CREDIT4U_CREDENTIALS_MIGRATIONS:
        if col_name in columns:
            continue
        conn.execute(
            f"ALTER TABLE credit4u_credentials ADD COLUMN {col_name} {col_def}"
        )
        columns.add(col_name)


def _ensure_db_schema() -> None:
    """스키마 생성·마이그레이션(모든 DB 접근 전 호출)."""
    ensure_storage()


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


def _credit4u_encryption_key() -> bytes:
    """비밀번호 암호화 키(REDRIBBON_CREDIT4U_SECRET 기반)."""
    secret = (os.getenv("REDRIBBON_CREDIT4U_SECRET") or "").strip()
    if not secret:
        raise PersistentStoreConfigError("REDRIBBON_CREDIT4U_SECRET is missing")
    return hashlib.sha256(f"rr-credit4u-cred-v1:{secret}".encode("utf-8")).digest()


def encrypt_credit4u_password_plaintext(plain_password: str) -> str:
    """AES-GCM 암호문(base64url)."""
    value = (plain_password or "").strip()
    if not value:
        raise ValueError("password is empty")
    key = _credit4u_encryption_key()
    nonce = get_random_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(value.encode("utf-8"))
    blob = nonce + tag + ciphertext
    return base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_credit4u_password_ciphertext(ciphertext: str) -> str:
    """저장된 AES-GCM 암호문 복호화."""
    blob = base64.urlsafe_b64decode((ciphertext or "").encode("ascii"))
    if len(blob) < 28:
        raise ValueError("invalid encrypted password blob")
    nonce, tag, encrypted = blob[:12], blob[12:28], blob[28:]
    key = _credit4u_encryption_key()
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(encrypted, tag).decode("utf-8")


def _email_domain_only(email: str) -> str:
    value = (email or "").strip().lower()
    if "@" not in value:
        return ""
    return value.rsplit("@", 1)[-1]


def save_credit4u_credentials(
    customer: dict[str, Any],
    credentials: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> str:
    """고객별 신용정보원 ID·비밀번호 영구 저장(비밀번호는 AES-GCM)."""
    _ensure_db_schema()
    customer_key = make_customer_key(customer)
    save_customer(customer)
    meta = metadata if isinstance(metadata, dict) else {}
    user_id = str(credentials.get("id") or "").strip()
    password = str(credentials.get("password") or "").strip()
    if not user_id or not password:
        raise ValueError("credit4u id and password are required")
    encrypted = encrypt_credit4u_password_plaintext(password)
    source = str(
        credentials.get("source")
        or meta.get("credential_source")
        or CREDENTIAL_SOURCE_GENERATED
    ).strip()
    attempt_no = int(
        meta.get("credit4u_id_attempt_no")
        or credentials.get("credit4u_id_attempt_no")
        or 0
    )
    email_domain = str(
        meta.get("email_domain")
        or _email_domain_only(
            str(meta.get("email") or credentials.get("email") or "")
        )
    ).strip()
    register_completed_at = str(meta.get("register_completed_at") or "").strip() or None
    credential_version = str(
        meta.get("credential_version")
        or credentials.get("credential_version")
        or "v1"
    ).strip() or "v1"
    metadata_json = _safe_metadata_json(meta)
    now = _utc_now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = conn.execute(
            "SELECT created_at FROM credit4u_credentials WHERE customer_key = ?",
            (customer_key,),
        ).fetchone()
        created_at = str(existing[0]) if existing and existing[0] else now
        conn.execute(
            """
            INSERT INTO credit4u_credentials (
                customer_key, credit4u_id, password_encrypted, credential_source,
                credential_version, id_attempt_no, email_domain, register_completed_at,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_key) DO UPDATE SET
                credit4u_id = excluded.credit4u_id,
                password_encrypted = excluded.password_encrypted,
                credential_source = excluded.credential_source,
                credential_version = excluded.credential_version,
                id_attempt_no = excluded.id_attempt_no,
                email_domain = excluded.email_domain,
                register_completed_at = COALESCE(excluded.register_completed_at, register_completed_at),
                metadata_json = COALESCE(excluded.metadata_json, metadata_json),
                updated_at = excluded.updated_at
            """,
            (
                customer_key,
                user_id,
                encrypted,
                source,
                credential_version,
                attempt_no,
                email_domain or None,
                register_completed_at,
                metadata_json,
                created_at,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("credit4u_credentials saved customer_key=%s", customer_key[:12])
    return customer_key


def _safe_metadata_json(meta: dict[str, Any]) -> str | None:
    """민감 필드 제외 metadata JSON."""
    if not meta:
        return None
    safe: dict[str, Any] = {}
    for key, value in meta.items():
        lowered = str(key).lower()
        if any(
            token in lowered
            for token in ("password", "identity", "phone", "주민", "payload")
        ):
            continue
        if lowered in ("email",) and "@" in str(value):
            domain = _email_domain_only(str(value))
            if domain:
                safe["email_domain"] = domain
            continue
        safe[key] = value
    if not safe:
        return None
    return json.dumps(safe, ensure_ascii=False)


def verify_credit4u_credentials_saved(
    customer: dict[str, Any],
    *,
    expected_id: str | None = None,
) -> bool:
    """저장 직후 DB 재조회로 ID·비밀번호 존재 확인."""
    loaded = load_credit4u_credentials(customer)
    if not loaded:
        return False
    saved_id = str(loaded.get("id") or "").strip()
    if not saved_id or not str(loaded.get("password") or "").strip():
        return False
    if expected_id and saved_id != str(expected_id).strip():
        return False
    return True


def load_credit4u_credentials(customer: dict[str, Any]) -> dict[str, Any] | None:
    """저장된 신용정보원 계정 복원(비밀번호 평문은 메모리에서만)."""
    _ensure_db_schema()
    try:
        customer_key = make_customer_key(customer)
    except PersistentStoreConfigError:
        return None

    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT credit4u_id, password_encrypted, credential_source, credential_version,
                   id_attempt_no, email_domain, register_completed_at, updated_at
            FROM credit4u_credentials
            WHERE customer_key = ?
            """,
            (customer_key,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    try:
        password = decrypt_credit4u_password_ciphertext(str(row[1] or ""))
    except (PersistentStoreConfigError, ValueError):
        return None
    return {
        "id": str(row[0] or "").strip(),
        "password": password,
        "source": str(row[2] or CREDENTIAL_SOURCE_GENERATED).strip(),
        "generated": str(row[2] or "") == CREDENTIAL_SOURCE_GENERATED,
        "credential_version": str(row[3] or "v1").strip() or "v1",
        "credit4u_id_attempt_no": int(row[4] or 0),
        "email_domain": str(row[5] or "").strip(),
        "register_completed_at": str(row[6] or "").strip(),
        "stored_updated_at": str(row[7] or "").strip(),
        "restored_from_store": True,
    }


def has_stored_credit4u_credentials(customer: dict[str, Any]) -> bool:
    if not is_search_hash_secret_configured():
        return False
    return load_credit4u_credentials(customer) is not None


def save_insurance_records(
    customer: dict[str, Any],
    flow_id: str,
    raw_response: Any,
    normalized_records: list[dict[str, Any]],
    normalized_summary: dict[str, Any],
    *,
    source: str = INSURANCE_SOURCE_CODEF_CREDIT4U,
) -> str:
    """보험가입이력 원부·정규화 결과 저장."""
    customer_key = make_customer_key(customer)
    save_customer(customer)
    now = _utc_now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO insurance_records (
                customer_key, flow_id, raw_response_json, normalized_json,
                summary_json, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_key,
                flow_id,
                json.dumps(raw_response, ensure_ascii=False),
                json.dumps(normalized_records, ensure_ascii=False),
                json.dumps(normalized_summary, ensure_ascii=False),
                source,
                now,
            ),
        )
        conn.commit()
        upsert_customer_flow(flow_id, customer_key, current_step=4)
    finally:
        conn.close()
    logger.info(
        "insurance_records saved flow_id=%s records=%s",
        flow_id,
        len(normalized_records),
    )
    return customer_key


def load_latest_insurance_record_by_customer_key(
    customer_key: str,
) -> dict[str, Any] | None:
    """customer_key 기준 insurance_records 최신 1건."""
    key = str(customer_key or "").strip()
    if not key:
        return None
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT id, flow_id, raw_response_json, normalized_json, summary_json,
                   source, created_at
            FROM insurance_records
            WHERE customer_key = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    raw_text = str(row[2] or "")
    normalized_text = str(row[3] or "")
    summary_text = str(row[4] or "")
    return {
        "record_id": int(row[0]),
        "customer_key": key,
        "flow_id": row[1],
        "raw_response": json.loads(raw_text or "{}"),
        "normalized_payload": json.loads(normalized_text or "null"),
        "summary": json.loads(summary_text or "{}"),
        "source": row[5],
        "created_at": row[6],
        "raw_len": len(raw_text),
        "normalized_len": len(normalized_text),
        "summary_len": len(summary_text),
    }


def load_latest_insurance_records(customer: dict[str, Any]) -> dict[str, Any] | None:
    """고객별 최신 보험가입이력 저장본."""
    try:
        customer_key = make_customer_key(customer)
    except PersistentStoreConfigError:
        return None
    loaded = load_latest_insurance_record_by_customer_key(customer_key)
    if not loaded:
        return None
    normalized_payload = loaded.get("normalized_payload")
    return {
        **loaded,
        "normalized_records": normalized_payload,
    }


def init_storage() -> None:
    """스키마 생성·마이그레이션(ensure_storage 별칭)."""
    ensure_storage()


def storage_health() -> dict[str, Any]:
    """DEBUG용 DB 상태(ensure_storage 별칭)."""
    return get_storage_health()


def get_storage_health() -> dict[str, Any]:
    """DEBUG용 DB 상태(민감정보 없음)."""
    ensure_storage()
    exists = DB_PATH.is_file()
    customers_count = 0
    medical_records_count = 0
    credit4u_credentials_count = 0
    insurance_records_count = 0
    latest_medical_created_at: str | None = None
    latest_insurance_created_at: str | None = None
    credit4u_credentials_columns: list[str] = []
    if exists:
        conn = sqlite3.connect(DB_PATH)
        try:
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='credit4u_credentials'"
            ).fetchone()
            if table_exists:
                credit4u_credentials_columns = [
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(credit4u_credentials)"
                    ).fetchall()
                ]
            row = conn.execute("SELECT COUNT(*) FROM customers").fetchone()
            customers_count = int(row[0] or 0) if row else 0
            row = conn.execute("SELECT COUNT(*) FROM medical_records").fetchone()
            medical_records_count = int(row[0] or 0) if row else 0
            row = conn.execute("SELECT COUNT(*) FROM credit4u_credentials").fetchone()
            credit4u_credentials_count = int(row[0] or 0) if row else 0
            row = conn.execute("SELECT COUNT(*) FROM insurance_records").fetchone()
            insurance_records_count = int(row[0] or 0) if row else 0
            row = conn.execute(
                "SELECT created_at FROM medical_records ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if row and row[0]:
                latest_medical_created_at = str(row[0])
            row = conn.execute(
                "SELECT created_at FROM insurance_records ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if row and row[0]:
                latest_insurance_created_at = str(row[0])
        finally:
            conn.close()
    return {
        "db_path": str(DB_PATH),
        "db_exists": exists,
        "customers_count": customers_count,
        "medical_records_count": medical_records_count,
        "credit4u_credentials_count": credit4u_credentials_count,
        "insurance_records_count": insurance_records_count,
        "latest_medical_created_at": latest_medical_created_at or "—",
        "latest_insurance_created_at": latest_insurance_created_at or "—",
        "search_hash_secret_configured": is_search_hash_secret_configured(),
        "credit4u_credentials_columns": credit4u_credentials_columns,
        "credential_version_column_present": "credential_version"
        in credit4u_credentials_columns,
    }
