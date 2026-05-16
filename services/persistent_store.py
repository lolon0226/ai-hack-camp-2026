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
from datetime import datetime, timedelta, timezone
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


_KST = timezone(timedelta(hours=9))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def print_receiver_received_at_utc() -> str:
    """Print Receiver 업로드 수신 시각(UTC, Z 접미)."""
    return datetime.utcnow().isoformat() + "Z"


def format_received_at_kst(received_at: str) -> str:
    """UTC received_at → 한국시간 표시 (예: 2026-05-16 22:22:13)."""
    raw = str(received_at or "").strip()
    if not raw:
        return "—"
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return raw


_HOSPITAL_LABELS_NOT_CUSTOMER = frozenset({"TEST_HOSPITAL"})


def _parse_received_document_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _is_hospital_label_not_customer(name: str, hospital_name: str = "") -> bool:
    label = str(name or "").strip()
    if not label or label == "—":
        return False
    if label.upper() in _HOSPITAL_LABELS_NOT_CUSTOMER:
        return True
    hospital = str(hospital_name or "").strip()
    if hospital and label == hospital:
        return True
    return False


def _format_amount_display(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        amount = int(value)
        return f"{amount:,}원" if amount > 0 else ""
    digits = re.sub(r"\D", "", str(value))
    if digits:
        try:
            return f"{int(digits):,}원"
        except ValueError:
            return ""
    return ""


def enrich_operator_received_document_for_display(
    doc: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """수신문서함: OCR·매칭 결과 표시 필드 정리."""
    meta = metadata if metadata is not None else _parse_received_document_metadata(
        doc.get("metadata_json")
    )
    ocr = meta.get("ocr") if isinstance(meta.get("ocr"), dict) else {}
    match = meta.get("match") if isinstance(meta.get("match"), dict) else {}

    def _is_test_hospital_label(value: str) -> bool:
        return str(value or "").strip().upper() in ("TEST_HOSPITAL", "TEST HOSPITAL")

    hospital_name = str(ocr.get("hospital_name") or "").strip()
    if not hospital_name:
        meta_hosp = str(meta.get("hospital_name") or "").strip()
        if meta_hosp and not _is_test_hospital_label(meta_hosp):
            hospital_name = meta_hosp
    if _is_test_hospital_label(hospital_name):
        hospital_name = ""

    linked_name = str(doc.get("linked_customer_name") or "").strip()
    if linked_name == "—":
        linked_name = ""

    if not hospital_name and linked_name and _is_hospital_label_not_customer(linked_name):
        hospital_name = linked_name
        linked_name = ""

    customer_key = doc.get("customer_key")
    match_status = str(match.get("match_status") or "").strip()
    matched_fields = match.get("matched_fields") if isinstance(match.get("matched_fields"), list) else []

    if match_status == "auto_matched" and customer_key:
        customer_label = linked_name or str(ocr.get("patient_name") or "") or "고객"
        customer_link_linked = True
    elif match_status == "review_required":
        customer_label = "추가 확인 필요"
        customer_link_linked = False
    elif customer_key and linked_name:
        customer_label = linked_name
        customer_link_linked = True
    else:
        customer_label = "미연결"
        customer_link_linked = False

    received_at = str(doc.get("received_at") or "").strip()
    if not received_at:
        received_at = (
            str(doc.get("created_at") or "").strip() or print_receiver_received_at_utc()
        )
        doc["received_at"] = received_at

    visit_dates = ocr.get("visit_dates") if isinstance(ocr.get("visit_dates"), list) else []
    amounts = ocr.get("amounts") if isinstance(ocr.get("amounts"), dict) else {}
    amount_parts: list[str] = []
    total_disp = _format_amount_display(amounts.get("total_amount"))
    self_disp = _format_amount_display(amounts.get("self_pay_amount"))
    paid_disp = _format_amount_display(amounts.get("paid_amount"))
    if amounts.get("confirmation_required"):
        amount_parts.append(str(amounts.get("display_message") or "금액 후보 확인 필요"))
    else:
        if total_disp:
            amount_parts.append(f"총액 {total_disp}")
        if self_disp:
            amount_parts.append(f"본인부담 {self_disp}")
        if paid_disp:
            amount_parts.append(f"납부 {paid_disp}")

    if matched_fields:
        match_basis = "+".join(str(f) for f in matched_fields) + " 일치"
    else:
        match_basis = "—"

    ocr_status_raw = str(doc.get("ocr_status") or "pending").strip().lower()
    has_core_ocr = bool(
        str(ocr.get("patient_name") or "").strip()
        and str(ocr.get("hospital_name") or "").strip()
        and visit_dates
        and amount_parts
    )
    if ocr_status_raw == "completed" and not has_core_ocr:
        ocr_status_raw = "completed_partial"
    if ocr_status_raw == "failed" and not str(ocr.get("error_message") or "").strip():
        ocr["error_message"] = "OCR 실패: 텍스트 추출 불가"
    ocr_status_labels = {
        "pending": "OCR 대기",
        "completed": "OCR 완료",
        "completed_partial": "OCR 부분완료",
        "failed": "OCR 실패",
    }
    duplicate_uploads = meta.get("duplicate_uploads")
    if not isinstance(duplicate_uploads, list):
        duplicate_uploads = []
    duplicate_count = len(duplicate_uploads)
    if duplicate_count > 0:
        duplicate_display = f"중복 업로드 {duplicate_count}회"
    elif meta.get("last_upload_duplicate"):
        duplicate_display = "중복 문서"
    else:
        duplicate_display = ""

    doc["metadata"] = meta
    doc["hospital_name_display"] = hospital_name or "병원명 확인 필요"

    dtype_raw = str(doc.get("document_type_candidate") or "").strip()
    dtype_inferred = bool(meta.get("document_type_inferred"))
    if not dtype_raw or (dtype_raw == "병원출력물" and not dtype_inferred):
        doc["document_type_display"] = "문서종류 확인 필요"
    else:
        doc["document_type_display"] = dtype_raw
    doc["customer_link_label"] = customer_label
    doc["customer_link_linked"] = customer_link_linked
    doc["received_at_display"] = format_received_at_kst(received_at)
    doc["visit_dates_display"] = ", ".join(str(d) for d in visit_dates) if visit_dates else "—"
    doc["amounts_display"] = " · ".join(amount_parts) if amount_parts else "—"
    doc["match_basis_display"] = match_basis
    doc["ocr_match_status"] = match_status or "—"
    doc["ocr_patient_name_display"] = str(ocr.get("patient_name") or "").strip() or "—"
    doc["ocr_status_display"] = ocr_status_labels.get(
        ocr_status_raw, ocr_status_raw or "OCR 대기"
    )
    if ocr_status_raw == "failed":
        doc["ocr_error_display"] = str(
            ocr.get("error_message") or "OCR 실패: 텍스트 추출 불가"
        ).strip()
    else:
        doc["ocr_error_display"] = str(ocr.get("error_message") or "").strip()
    preview = str(ocr.get("text_preview") or "").strip()
    doc["ocr_preview_display"] = preview[:1000] if preview else ""
    debug_obj = ocr.get("debug") if isinstance(ocr.get("debug"), dict) else {}
    debug_parts = [str(ocr.get("debug_message") or "").strip()]
    if debug_obj:
        for key in (
            "extraction_source",
            "pdf_text_len",
            "ocr_text_len",
            "used_dpi",
            "tesseract_path",
            "ocr_error_message",
        ):
            val = debug_obj.get(key)
            if val not in (None, ""):
                debug_parts.append(f"{key}={val}")
    doc["ocr_debug_display"] = " | ".join(p for p in debug_parts if p)
    doc["ocr_can_retry"] = ocr_status_raw in (
        "pending",
        "failed",
        "completed_partial",
        "completed",
    )
    doc["ocr_can_strong"] = bool(str(doc.get("file_path") or "").strip())
    doc["duplicate_upload_display"] = duplicate_display
    doc["has_duplicate_upload"] = bool(duplicate_display)
    return doc


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

            CREATE TABLE IF NOT EXISTS operator_received_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_key TEXT,
                document_title TEXT NOT NULL,
                document_type_candidate TEXT,
                ocr_status TEXT NOT NULL DEFAULT 'pending',
                file_path TEXT,
                file_url TEXT,
                linked_customer_name TEXT,
                file_sha256 TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_operator_received_documents_customer
                ON operator_received_documents (customer_key, created_at DESC);

            CREATE TABLE IF NOT EXISTS actual_loss_claim_demo_transmissions (
                customer_key TEXT PRIMARY KEY,
                demo_status TEXT NOT NULL,
                note TEXT,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _migrate_credit4u_credentials_columns(conn)
        _migrate_operator_received_documents_columns(conn)
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


def _migrate_operator_received_documents_columns(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='operator_received_documents'"
    ).fetchone()
    if not table_exists:
        return
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(operator_received_documents)").fetchall()
    }
    if "file_sha256" not in columns:
        conn.execute(
            "ALTER TABLE operator_received_documents ADD COLUMN file_sha256 TEXT"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_operator_received_documents_sha
            ON operator_received_documents (file_sha256)
            """
        )
    if "received_at" not in columns:
        conn.execute(
            "ALTER TABLE operator_received_documents ADD COLUMN received_at TEXT"
        )
        conn.execute(
            """
            UPDATE operator_received_documents
            SET received_at = created_at
            WHERE received_at IS NULL OR received_at = ''
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_operator_received_documents_received_at
            ON operator_received_documents (received_at DESC)
            """
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
    offset = 0
    record_id: int | None = None
    if len(row) >= 8:
        record_id = int(row[0])
        offset = 1
    return {
        "record_id": record_id,
        "flow_id": row[offset + 0],
        "basic": json.loads(row[offset + 1] or "[]"),
        "detail": json.loads(row[offset + 2] or "[]"),
        "prescribe": json.loads(row[offset + 3] or "[]"),
        "counts": json.loads(row[offset + 4] or "{}"),
        "source": row[offset + 5],
        "created_at": row[offset + 6],
    }


def load_latest_medical_records_by_customer_key(
    customer_key: str,
) -> dict[str, Any] | None:
    """customer_key 기준 medical_records 최신 1건."""
    key = str(customer_key or "").strip()
    if not key:
        return None
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT id, flow_id, basic_json, detail_json, prescribe_json, counts_json, source, created_at
            FROM medical_records
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
    bundle = _row_to_medical_bundle(row)
    bundle["customer_key"] = key
    return bundle


def load_latest_medical_records(customer: dict[str, Any]) -> dict[str, Any] | None:
    """customer_key 기준 최신 진료내역 1건(서버 재시작과 무관)."""
    try:
        customer_key = make_customer_key(customer)
    except PersistentStoreConfigError:
        return None
    return load_latest_medical_records_by_customer_key(customer_key)


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


def update_insurance_record_summary(record_id: int, summary_json: dict[str, Any]) -> bool:
    """insurance_records.summary_json 갱신(CODEF 미호출)."""
    if not record_id:
        return False
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE insurance_records SET summary_json = ? WHERE id = ?",
            (json.dumps(summary_json, ensure_ascii=False), int(record_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def rebuild_insurance_summary_for_customer(customer: dict[str, Any]) -> dict[str, Any] | None:
    """저장 원부로 insured_summary 재생성 후 summary_json 저장."""
    saved = load_latest_insurance_records(customer)
    if not saved:
        return None
    from services.insurance_summary import compute_insured_summary_package

    customer_name = str(customer.get("name") or "")
    package = compute_insured_summary_package(
        saved.get("raw_response"),
        saved.get("normalized_payload") or saved.get("normalized_records"),
        customer_name,
        summary_payload=saved.get("summary"),
    )
    summary_json = package.get("summary_json")
    if not isinstance(summary_json, dict):
        return None
    update_insurance_record_summary(int(saved["record_id"]), summary_json)
    logger.info(
        "insurance summary rebuilt record_id=%s products=%s",
        saved.get("record_id"),
        (package.get("insured_summary") or {}).get("counts", {}).get("product_count"),
    )
    return package


def init_storage() -> None:
    """스키마 생성·마이그레이션(ensure_storage 별칭)."""
    ensure_storage()


def storage_health() -> dict[str, Any]:
    """DEBUG용 DB 상태(ensure_storage 별칭)."""
    return get_storage_health()


def get_customer_profile_by_key(customer_key: str) -> dict[str, Any] | None:
    """운영자 화면용 — customer_key로 이름 등 메타 조회."""
    key = str(customer_key or "").strip()
    if not key:
        return None
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT customer_key, name, email, created_at
            FROM customers
            WHERE customer_key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "customer_key": str(row[0]),
        "customer_id": str(row[0]),
        "name": str(row[1] or "").strip() or "—",
        "email": str(row[2] or "").strip(),
        "created_at": str(row[3] or ""),
    }


def list_operator_customers(*, limit: int = 100) -> list[dict[str, Any]]:
    """운영자 콘솔 고객 선택 목록."""
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    rows: list[tuple[Any, ...]] = []
    try:
        rows = conn.execute(
            """
            SELECT
                c.customer_key,
                c.name,
                c.created_at,
                c.email,
                (SELECT COUNT(*) FROM medical_records m WHERE m.customer_key = c.customer_key),
                (SELECT COUNT(*) FROM insurance_records i WHERE i.customer_key = c.customer_key),
                (
                    SELECT flow_id FROM customer_flows f
                    WHERE f.customer_key = c.customer_key
                    ORDER BY f.updated_at DESC
                    LIMIT 1
                )
            FROM customers c
            ORDER BY c.created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    finally:
        conn.close()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "customer_key": str(row[0]),
                "customer_id": str(row[0]),
                "name": str(row[1] or "").strip() or "—",
                "created_at": str(row[2] or ""),
                "email": str(row[3] or "").strip(),
                "medical_record_count": int(row[4] or 0),
                "insurance_record_count": int(row[5] or 0),
                "latest_flow_id": str(row[6] or "").strip() or None,
                "has_medical": int(row[4] or 0) > 0,
                "has_insurance": int(row[5] or 0) > 0,
            }
        )
    return items


def list_operator_received_documents(
    *,
    customer_key: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """병원 출력서류 수신함 목록."""
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        if customer_key:
            rows = conn.execute(
                """
                SELECT id, customer_key, document_title, document_type_candidate,
                       ocr_status, file_path, file_url, linked_customer_name, created_at,
                       received_at, metadata_json
                FROM operator_received_documents
                WHERE customer_key = ?
                ORDER BY COALESCE(received_at, created_at) DESC, id DESC
                LIMIT ?
                """,
                (str(customer_key).strip(), max(1, min(int(limit), 500))),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, customer_key, document_title, document_type_candidate,
                       ocr_status, file_path, file_url, linked_customer_name, created_at,
                       received_at, metadata_json
                FROM operator_received_documents
                ORDER BY COALESCE(received_at, created_at) DESC, id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
    finally:
        conn.close()
    return [
        enrich_operator_received_document_for_display(_received_document_row_to_dict(row))
        for row in rows
    ]


def _received_document_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    doc_id = int(row[0])
    file_path = str(row[5] or "").strip()
    file_url = str(row[6] or "").strip()
    if not file_url and file_path:
        file_url = f"/operator/received-documents/{doc_id}/file"
    created_at = str(row[8] or "").strip()
    received_at = str(row[9] or "").strip() if len(row) > 9 else ""
    metadata_json = str(row[10] or "") if len(row) > 10 else ""
    if not received_at:
        received_at = created_at
    return {
        "id": doc_id,
        "customer_key": str(row[1] or "").strip() or None,
        "document_title": str(row[2] or "").strip() or "—",
        "document_type_candidate": str(row[3] or "").strip() or "—",
        "ocr_status": str(row[4] or "").strip() or "pending",
        "file_path": file_path,
        "file_url": file_url,
        "linked_customer_name": str(row[7] or "").strip() or "—",
        "created_at": created_at,
        "received_at": received_at,
        "metadata_json": metadata_json,
    }


def find_received_document_by_sha256(file_sha256: str) -> dict[str, Any] | None:
    digest = str(file_sha256 or "").strip().lower()
    if not digest:
        return None
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT id, customer_key, document_title, document_type_candidate,
                   ocr_status, file_path, file_url, linked_customer_name, created_at,
                   received_at, metadata_json
            FROM operator_received_documents
            WHERE file_sha256 = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (digest,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return enrich_operator_received_document_for_display(_received_document_row_to_dict(row))


def register_print_receiver_upload(
    *,
    stored_path: str,
    original_filename: str,
    file_sha256: str,
    received_at: str,
    hospital_name: str = "",
    printer_name: str = "",
    customer_key: str | None = None,
    linked_customer_name: str = "",
    document_type_candidate: str = "",
) -> dict[str, Any]:
    """Print Receiver 업로드 등록(sha256 중복 시 기존 문서 반환)."""
    received_at = str(received_at or "").strip() or print_receiver_received_at_utc()
    digest = str(file_sha256 or "").strip().lower()
    existing = find_received_document_by_sha256(digest) if digest else None
    if existing:
        return {
            "duplicate": True,
            "document_id": int(existing["id"]),
            "document": existing,
        }
    title = str(original_filename or "수신문서.pdf").strip() or "수신문서.pdf"
    if not document_type_candidate:
        lowered = title.lower()
        if "영수증" in title:
            document_type_candidate = "진료비영수증"
        elif "처방" in title:
            document_type_candidate = "처방전"
        elif "세부" in title or "내역" in title:
            document_type_candidate = "진료비세부내역서"
        elif "진단" in title:
            document_type_candidate = "진단서"
        else:
            document_type_candidate = "병원출력물"
    metadata = {
        "source": "print_receiver",
        "hospital_name": hospital_name,
        "printer_name": printer_name,
        "original_filename": original_filename,
        "sha256": digest,
    }
    doc_id = upsert_operator_received_document(
        customer_key=customer_key,
        document_title=title,
        document_type_candidate=document_type_candidate,
        ocr_status="pending",
        file_path=stored_path,
        file_url="",
        linked_customer_name=str(linked_customer_name or "").strip(),
        file_sha256=digest,
        metadata_json=metadata,
        received_at=received_at,
    )
    doc = get_received_document_by_id(doc_id)
    return {
        "duplicate": False,
        "document_id": doc_id,
        "document": doc or {"id": doc_id},
    }


def upsert_operator_received_document(
    *,
    customer_key: str | None,
    document_title: str,
    document_type_candidate: str = "",
    ocr_status: str = "pending",
    file_path: str = "",
    file_url: str = "",
    linked_customer_name: str = "",
    file_sha256: str = "",
    metadata_json: dict[str, Any] | str | None = None,
    received_at: str | None = None,
) -> int:
    _ensure_db_schema()
    received_ts = str(received_at or "").strip() or print_receiver_received_at_utc()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """
            INSERT INTO operator_received_documents (
                customer_key, document_title, document_type_candidate, ocr_status,
                file_path, file_url, linked_customer_name, file_sha256, metadata_json,
                created_at, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(customer_key or "").strip() or None,
                str(document_title or "").strip() or "문서",
                str(document_type_candidate or "").strip(),
                str(ocr_status or "pending").strip(),
                str(file_path or "").strip(),
                str(file_url or "").strip(),
                str(linked_customer_name or "").strip(),
                str(file_sha256 or "").strip().lower() or None,
                json.dumps(metadata_json or {}, ensure_ascii=False)
                if isinstance(metadata_json, dict)
                else str(metadata_json or "{}"),
                received_ts,
                received_ts,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def list_customer_match_targets(*, limit: int = 500) -> list[dict[str, Any]]:
    """OCR 고객 매칭용(이름·해시; 주민·전화 원문 미포함)."""
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT customer_key, name, identity_hash, phone_hash
            FROM customers
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "customer_key": str(row[0]),
            "name": str(row[1] or "").strip(),
            "identity_hash": str(row[2] or ""),
            "phone_hash": str(row[3] or ""),
            "identity_digits": "",
            "phone_digits": "",
        }
        for row in rows
    ]


def record_received_document_duplicate_upload(
    document_id: int,
    *,
    file_sha256: str,
    received_at: str,
) -> None:
    """중복 업로드 시 기존 문서 metadata에 기록(신규 행 생성 없음)."""
    doc_id = int(document_id or 0)
    if doc_id <= 0:
        return
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT metadata_json FROM operator_received_documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return
    meta = _parse_received_document_metadata(str(row[0] or ""))
    history = meta.get("duplicate_uploads")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "at": str(received_at or "").strip() or print_receiver_received_at_utc(),
            "sha256": str(file_sha256 or "").strip().lower(),
            "note": "중복 문서",
        }
    )
    meta["duplicate_uploads"] = history[-20:]
    meta["last_upload_duplicate"] = True
    meta["last_upload_duplicate_at"] = history[-1]["at"]
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            UPDATE operator_received_documents
            SET metadata_json = ?
            WHERE id = ?
            """,
            (json.dumps(meta, ensure_ascii=False), doc_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_operator_received_document_ocr(
    document_id: int,
    *,
    metadata_json: dict[str, Any],
    ocr_status: str = "completed",
    customer_key: str | None = None,
    linked_customer_name: str = "",
    document_type_candidate: str | None = None,
) -> None:
    """OCR·매칭 결과 반영."""
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        if document_type_candidate is not None:
            conn.execute(
                """
                UPDATE operator_received_documents
                SET metadata_json = ?,
                    ocr_status = ?,
                    customer_key = ?,
                    linked_customer_name = ?,
                    document_type_candidate = ?
                WHERE id = ?
                """,
                (
                    json.dumps(metadata_json, ensure_ascii=False),
                    str(ocr_status or "completed").strip(),
                    str(customer_key or "").strip() or None,
                    str(linked_customer_name or "").strip(),
                    str(document_type_candidate or "").strip(),
                    int(document_id),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE operator_received_documents
                SET metadata_json = ?,
                    ocr_status = ?,
                    customer_key = ?,
                    linked_customer_name = ?
                WHERE id = ?
                """,
                (
                    json.dumps(metadata_json, ensure_ascii=False),
                    str(ocr_status or "completed").strip(),
                    str(customer_key or "").strip() or None,
                    str(linked_customer_name or "").strip(),
                    int(document_id),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def seed_operator_received_documents_if_empty() -> int:
    """데모용 수신문서가 없으면 샘플 1건 삽입."""
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM operator_received_documents"
        ).fetchone()
        count = int(row[0] or 0) if row else 0
    finally:
        conn.close()
    if count > 0:
        return 0
    customers = list_operator_customers(limit=1)
    ck = customers[0]["customer_key"] if customers else None
    name = customers[0]["name"] if customers else "—"
    upsert_operator_received_document(
        customer_key=ck,
        document_title="진료비 영수증(샘플)",
        document_type_candidate="진료비영수증",
        ocr_status="completed",
        linked_customer_name=name,
        metadata_json={
            "source": "demo_seed",
            "hospital_name": "시연용 종합병원 A",
            "ocr": {
                "patient_name": name,
                "hospital_name": "시연용 종합병원 A",
                "visit_dates": ["2025-11-02"],
                "amounts": {
                    "total_amount": 15600,
                    "self_pay_amount": 15600,
                    "paid_amount": 15600,
                },
                "phone_number_masked": "010****5678",
                "rrn_masked": "900101-*******",
            },
            "match": {
                "match_score": 2,
                "matched_fields": ["이름", "주민번호(생년월일)"],
                "match_status": "auto_matched",
                "matched_customer_key": ck,
            },
        },
    )
    return 1


def get_received_document_by_id(document_id: int) -> dict[str, Any] | None:
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT id, customer_key, document_title, document_type_candidate,
                   ocr_status, file_path, file_url, linked_customer_name, created_at,
                   received_at, metadata_json
            FROM operator_received_documents
            WHERE id = ?
            """,
            (int(document_id),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return enrich_operator_received_document_for_display(_received_document_row_to_dict(row))


def load_actual_loss_claim_demo_state(customer_key: str) -> dict[str, Any] | None:
    key = str(customer_key or "").strip()
    if not key:
        return None
    _ensure_db_schema()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT customer_key, demo_status, note, payload_json, updated_at
            FROM actual_loss_claim_demo_transmissions
            WHERE customer_key = ?
            """,
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        payload = json.loads(str(row[3] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    return {
        "customer_key": str(row[0]),
        "demo_status": str(row[1] or ""),
        "note": str(row[2] or ""),
        "payload": payload if isinstance(payload, dict) else {},
        "updated_at": str(row[4] or ""),
    }


def save_actual_loss_claim_demo_state(
    customer_key: str,
    *,
    demo_status: str,
    note: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    key = str(customer_key or "").strip()
    if not key:
        raise ValueError("customer_key required")
    _ensure_db_schema()
    now = _utc_now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO actual_loss_claim_demo_transmissions (
                customer_key, demo_status, note, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(customer_key) DO UPDATE SET
                demo_status = excluded.demo_status,
                note = excluded.note,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                key,
                str(demo_status or "demo_saved").strip(),
                str(note or "").strip(),
                json.dumps(payload or {}, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


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
