# -*- coding: utf-8 -*-
"""RedRibbon MVP — 루트 인트로 및 시연/운영자 진입 스텁.

기존 대형 앱과 병합할 때: GET `/` 는 인트로 템플릿을 렌더링하도록 유지하고,
아래 스텁 라우트(`/hospital/hira-consent` 등)는
프로젝트에 이미 동일 경로가 있으면 이 블록을 제거하고 기존 구현만 두면 됩니다.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.credit4u_client import (
    extract_credit4u_insurance_records,
    is_credit4u_already_registered,
    is_credit4u_existing_account_required,
    is_credit4u_register_required,
    post_credit4u_contract_info_first,
    post_credit4u_contract_info_second,
    is_credit4u_register_completed,
    is_register_email_retry_code,
    is_register_signup_retry_code,
    post_credit4u_register_first,
    build_credit4u_register_first_payload,
    credit4u_register_payload_timeout_value,
    ensure_credit4u_check_param_uuid,
    allowed_credit4u_email_domains_display,
    extra_info_requests_signup_info,
    extract_register_extra_info,
    is_register_signup_auto_retry_code,
    is_register_signup_email_manual_code,
    register_extra_reason_for_display,
    register_signup_retry_message,
    resolve_signup_required_fields,
    signup_auto_retry_reason_label,
    store_register_extra_info_on_entry,
    is_credit4u_email_domain_allowed,
    is_credit4u_register_timeout_retryable,
    validate_credit4u_email_for_register,
    new_register_signup_timing_debug,
    post_credit4u_register_second,
    extra_info_has_request_key,
    extra_info_requests_email_auth,
    extra_info_requests_sms,
    register_followup_stage_message,
    register_req_secure_no,
    resolve_register_stage,
    resolve_register_stage_from_followup,
    sanitize_credit4u_two_way_info,
    user_message_for_credit4u_failure,
)
from services.credit4u_identity import (
    CREDIT4U_CREDENTIAL_VERSION,
    Credit4uConfigError,
    credit4u_credentials_debug,
    generate_credit4u_credentials,
    get_credit4u_secret,
    mask_credit4u_id,
    password_contains_user_id,
    mask_email_for_debug,
    persist_credit4u_credentials,
    regenerate_credit4u_credentials,
    regenerate_credit4u_credentials_for_signup,
    restore_credit4u_credentials,
    validate_credit4u_id,
    verify_persisted_credit4u_credentials,
)
from services.integrated_analysis import (
    build_ai_analysis_context,
    execute_ai_claim_analysis,
    build_analysis_ready_context,
)
from services.insurance_summary import (
    build_insurance_company_groups,
    flatten_imported_insurance_records,
    insurance_summary_from_records,
    prepare_insurance_company_groups_for_template,
    resolve_stored_insurance_for_display,
)
from services.codef_client import (
    CodefClientError,
    build_hira_medical_payload,
    codef_password_encryption_debug,
    extract_hira_medical_lists,
    extract_two_way_info,
    is_hira_auth_waiting,
    post_hira_medical_first,
    post_hira_medical_second,
    user_message_for_codef_failure,
    user_message_for_second_failure,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PRINT_SETUP_ZIP_URL = "/static/downloads/RedRibbon_Demo_Print_Setup.zip"
PRINT_SETUP_EXE_URL = "/static/downloads/RedRibbon_Demo_Print_Setup.exe"
PRINT_UPLOAD_ROOT = BASE_DIR / "data" / "print_receiver_uploads"


def _printer_download_template_context() -> dict[str, str]:
  """병원·운영자 화면 — ZIP/EXE 설치파일 링크."""
  exe_path = BASE_DIR / "static" / "downloads" / "RedRibbon_Demo_Print_Setup.exe"
  return {
      "printer_download_url": PRINT_SETUP_ZIP_URL,
      "printer_download_zip_url": PRINT_SETUP_ZIP_URL,
      "printer_download_exe_url": (
          PRINT_SETUP_EXE_URL if exe_path.is_file() else ""
      ),
  }

from services.actual_loss_claim_package_builder import (  # noqa: E402
    build_actual_loss_claim_package,
    build_operator_customer_picker,
)
from services.persistent_store import (  # noqa: E402
    DB_PATH,
    PersistentStoreConfigError,
    can_make_customer_key,
    ensure_storage,
    get_received_document_by_id,
    get_storage_health,
    has_medical_records,
    is_search_hash_secret_configured,
    has_stored_credit4u_credentials,
    list_operator_received_documents,
    load_latest_insurance_records,
    load_latest_medical_records,
    make_customer_key,
    rebuild_insurance_summary_for_customer,
    normalize_customer_fields,
    print_receiver_received_at_utc,
    register_print_receiver_upload,
    save_actual_loss_claim_demo_state,
    save_customer,
    save_insurance_records,
    save_medical_records,
    seed_operator_received_documents_if_empty,
    upsert_customer_flow,
)

app = FastAPI(title="RedRibbon MVP")


@app.on_event("startup")
def _app_startup() -> None:
    ensure_storage()
    PRINT_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 병원 흐름: 메모리 저장(재시작 시 초기화). POST 고객등록 시 항상 신규 flow_id만 발급.
FLOW_STORE: dict[str, dict[str, Any]] = {}


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes")


DEMO_MODE = _env_truthy("DEMO_MODE")
DEBUG_PANEL_ENABLED = _env_truthy("DEBUG_PANEL_ENABLED")


def _demo_complete_allowed() -> bool:
    return DEMO_MODE or DEBUG_PANEL_ENABLED


def _debug_panel() -> bool:
    return DEBUG_PANEL_ENABLED


def _canonical_flow_id(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    try:
        key = str(uuid.UUID(s))
    except ValueError:
        return None
    if key not in FLOW_STORE:
        return None
    return key


_ALLOWED_TELECOM = frozenset(
    {
        "SKT",
        "KT",
        "LGU+",
        "알뜰폰 SKT",
        "알뜰폰 KT",
        "알뜰폰 LGU+",
    }
)


def _customer_form_error(
    name: str,
    identity: str,
    phone: str,
    telecom: str,
    email: str,
    auth_method: str,
) -> str | None:
    name = (name or "").strip()
    identity = (identity or "").strip()
    phone = (phone or "").strip()
    telecom = (telecom or "").strip()
    email = (email or "").strip()
    auth_method = (auth_method or "").strip()
    if not all((name, identity, phone, telecom, email)):
        return "필수 항목을 모두 입력해 주세요."
    if telecom not in _ALLOWED_TELECOM:
        return "통신사 선택이 올바르지 않습니다."
    if auth_method != "kakao":
        return "인증수단이 올바르지 않습니다."
    return None


def _mask_identity(identity: str) -> str:
    digits = "".join(c for c in (identity or "") if c.isdigit())
    if len(digits) >= 6:
        return f"{digits[:6]}-*******"
    return "*******"


def _mask_phone(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) <= 3:
        return "***"
    return digits[:3] + ("*" * (len(digits) - 3))


def _request_hira_auth_demo(flow_id: str) -> None:
    """DEMO_MODE: CODEF 호출 없이 인증 대기 상태만 시뮬레이션."""
    del flow_id  # noqa: ARG001


def _apply_hira_waiting_auth(
    entry: dict[str, Any],
    *,
    result: dict[str, Any],
    extracted: dict[str, Any],
) -> None:
    entry["medical_status"] = "waiting_auth"
    entry["medical_auth_requested"] = True
    entry["codef_result_code"] = str(result.get("code") or "")
    entry["codef_result_message"] = str(result.get("message") or "")
    entry["codef_continue2_way"] = bool(extracted.get("continue2Way"))
    entry["codef_method"] = str(extracted.get("method") or "")
    entry["two_way_info_found"] = bool(extracted.get("twoWayInfo_found"))
    if extracted.get("twoWayInfo_found") and extracted.get("twoWayInfo") is not None:
        entry["two_way_info"] = extracted["twoWayInfo"]
    entry["codef_root_keys"] = list(extracted.get("root_keys") or [])
    entry["codef_data_keys"] = list(extracted.get("data_keys") or [])
    entry["medical_message"] = "카카오 인증 요청이 발송되었습니다."
    entry["second_status"] = entry.get("second_status") or "idle"


def _apply_hira_waiting_auth_debug_needed(
    entry: dict[str, Any],
    *,
    result: dict[str, Any],
    extracted: dict[str, Any],
) -> None:
    """CF-03002이나 twoWayInfo 저장 실패 — DEBUG로 구조 확인."""
    entry["medical_status"] = "waiting_auth_debug_needed"
    entry["medical_auth_requested"] = True
    entry["codef_result_code"] = str(result.get("code") or "")
    entry["codef_result_message"] = str(result.get("message") or "")
    entry["codef_continue2_way"] = bool(extracted.get("continue2Way"))
    entry["codef_method"] = str(extracted.get("method") or "")
    entry["two_way_info_found"] = False
    entry.pop("two_way_info", None)
    entry["codef_root_keys"] = list(extracted.get("root_keys") or [])
    entry["codef_data_keys"] = list(extracted.get("data_keys") or [])
    entry["medical_message"] = (
        "카카오 인증 요청은 성공했으나, 2차 인증 정보 저장에 실패했습니다."
    )
    entry["second_status"] = entry.get("second_status") or "idle"


def _apply_hira_auth_failed(
    entry: dict[str, Any],
    *,
    result_code: str,
    result_message: str,
    user_message: str,
) -> None:
    entry["medical_status"] = "failed"
    entry["codef_result_code"] = result_code
    entry["codef_result_message"] = result_message
    entry["medical_message"] = user_message
    entry["second_status"] = "failed"


def _apply_hira_second_completed(
    entry: dict[str, Any],
    *,
    lists: dict[str, Any],
    result_code: str,
    result_message: str,
) -> None:
    counts = lists.get("counts") or {"basic": 0, "detail": 0, "prescribe": 0}
    entry["medical_status"] = "completed"
    entry["medical_result_counts"] = counts
    entry["medical_records_basic"] = lists.get("basic") or []
    entry["medical_records_detail"] = lists.get("detail") or []
    entry["medical_records_prescribe"] = lists.get("prescribe") or []
    entry["medical_message"] = "진료내역 수신이 완료되었습니다."
    entry["second_status"] = "completed"
    entry["codef_second_result_code"] = result_code
    entry["codef_second_result_message"] = result_message


def _normalize_stored_customer(customer: dict[str, Any]) -> dict[str, Any]:
    """FLOW_STORE 고객 필드 정규화(저장·조회 customer_key 일치)."""
    fields = normalize_customer_fields(customer)
    normalized = dict(customer)
    normalized["name"] = fields["name"]
    normalized["identity"] = fields["identity"]
    normalized["phone"] = fields["phone"]
    return normalized


def _persist_medical_records_to_sqlite(flow_id: str, entry: dict[str, Any]) -> None:
    """진료내역 2차 성공 후 SQLite 저장(secret 없으면 중단)."""
    customer = entry.get("customer")
    if not isinstance(customer, dict) or entry.get("medical_status") != "completed":
        entry["medical_records_persisted"] = False
        return
    if not is_search_hash_secret_configured():
        entry["medical_records_persisted"] = False
        entry["medical_storage_error"] = "missing_secret"
        logger.warning(
            "medical_records not saved flow_id=%s reason=missing_search_hash_secret",
            flow_id,
        )
        return

    customer = _normalize_stored_customer(customer)
    entry["customer"] = customer
    counts = entry.get("medical_result_counts") or {"basic": 0, "detail": 0, "prescribe": 0}
    basic = entry.get("medical_records_basic")
    if basic is None:
        basic = entry.get("medical_records") or []
    detail = entry.get("medical_records_detail") or []
    prescribe = entry.get("medical_records_prescribe") or []
    try:
        save_customer(customer)
        customer_key = save_medical_records(
            customer,
            flow_id,
            basic,
            detail,
            prescribe,
            counts,
        )
        entry["customer_key"] = customer_key
        entry["medical_records_persisted"] = True
        entry.pop("medical_storage_error", None)
        entry["has_saved_medical_records"] = True
    except PersistentStoreConfigError:
        entry["medical_records_persisted"] = False
        entry["medical_storage_error"] = "missing_secret"
        logger.warning(
            "medical_records not saved flow_id=%s reason=config_error",
            flow_id,
        )
    except Exception as exc:
        entry["medical_records_persisted"] = False
        entry["medical_storage_error"] = "save_failed"
        logger.warning(
            "medical_records not saved flow_id=%s err=%s",
            flow_id,
            type(exc).__name__,
        )


def _register_customer_persistence(flow_id: str, entry: dict[str, Any]) -> None:
    """고객등록 직후 SQLite 고객·flow 연결 및 저장 진료내역 여부."""
    customer = entry.get("customer")
    if not isinstance(customer, dict):
        return
    customer = _normalize_stored_customer(customer)
    entry["customer"] = customer
    if not is_search_hash_secret_configured():
        entry["has_saved_medical_records"] = False
        return
    try:
        customer_key = save_customer(customer)
        entry["customer_key"] = customer_key
        upsert_customer_flow(flow_id, customer_key, current_step=3)
        entry["has_saved_medical_records"] = has_medical_records(customer)
    except PersistentStoreConfigError:
        entry["has_saved_medical_records"] = False


def _apply_saved_medical_records(flow_id: str, entry: dict[str, Any]) -> bool:
    """저장된 진료내역을 FLOW_STORE에 복원."""
    customer = entry.get("customer")
    if not isinstance(customer, dict):
        return False
    customer = _normalize_stored_customer(customer)
    entry["customer"] = customer
    bundle = load_latest_medical_records(customer)
    if not bundle:
        return False
    _apply_hira_second_completed(
        entry,
        lists={
            "basic": bundle.get("basic") or [],
            "detail": bundle.get("detail") or [],
            "prescribe": bundle.get("prescribe") or [],
            "counts": bundle.get("counts")
            or {"basic": 0, "detail": 0, "prescribe": 0},
        },
        result_code="SAVED",
        result_message="저장된 진료내역",
    )
    entry["medical_message"] = "저장된 진료내역을 불러왔습니다."
    entry["medical_source"] = "saved"
    entry["medical_records_source"] = bundle.get("source") or "saved"
    entry["medical_records_saved_at"] = bundle.get("created_at")
    entry["medical_records_from_flow_id"] = bundle.get("flow_id")
    return True


def _hira_storage_debug_context(
    customer: dict[str, Any],
    saved_medical: dict[str, Any] | None,
) -> dict[str, Any]:
    counts = (saved_medical or {}).get("counts") if isinstance(saved_medical, dict) else {}
    if not isinstance(counts, dict):
        counts = {}
    secret_ok = is_search_hash_secret_configured()
    ctx: dict[str, Any] = {
        "storage_db_path": str(DB_PATH),
        "saved_medical_exists": bool(saved_medical),
        "saved_medical_counts": (
            f"basic={counts.get('basic', 0)}, "
            f"detail={counts.get('detail', 0)}, "
            f"prescribe={counts.get('prescribe', 0)}"
        ),
        "customer_key_created": can_make_customer_key(customer) if secret_ok else False,
        "search_hash_secret_configured": secret_ok,
    }
    if not secret_ok:
        ctx["storage_secret_message"] = "진료내역 저장 설정값이 없습니다."
    return ctx


def _apply_hira_second_failed(
    entry: dict[str, Any],
    *,
    result_code: str,
    result_message: str,
    user_message: str,
) -> None:
    entry["medical_status"] = "failed"
    entry["second_status"] = "failed"
    entry["medical_message"] = user_message
    entry["codef_second_result_code"] = result_code
    entry["codef_second_result_message"] = result_message


def _complete_hira_demo(entry: dict[str, Any]) -> None:
    sample_basic = list(_MEDICAL_SAMPLE_RECORDS)
    entry["medical_status"] = "completed"
    entry["medical_result_counts"] = {"basic": 113, "detail": 703, "prescribe": 382}
    entry["medical_records"] = sample_basic
    entry["medical_records_basic"] = sample_basic
    entry["medical_records_detail"] = []
    entry["medical_records_prescribe"] = []
    entry["medical_message"] = "진료내역 수신이 완료되었습니다."
    entry["second_status"] = "completed"
    entry["codef_second_result_code"] = "DEMO"
    entry["codef_second_result_message"] = "데모 모드 샘플 완료"


def _get_hira_first_payload(flow_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    stored = entry.get("hira_first_payload")
    if isinstance(stored, dict) and stored:
        return dict(stored)
    customer = entry.get("customer") or {}
    payload = build_hira_medical_payload(customer, flow_id)
    entry["hira_first_payload"] = payload
    return payload


def _clear_hira_codef_fields(entry: dict[str, Any]) -> None:
    for key in (
        "medical_message",
        "medical_auth_requested",
        "codef_result_code",
        "codef_result_message",
        "codef_continue2_way",
        "codef_method",
        "two_way_info",
        "two_way_info_found",
        "codef_root_keys",
        "codef_data_keys",
        "hira_first_payload",
        "medical_result_counts",
        "medical_records",
        "medical_records_basic",
        "medical_records_detail",
        "medical_records_prescribe",
        "codef_second_result_code",
        "codef_second_result_message",
        "second_status",
    ):
        entry.pop(key, None)
    entry["second_status"] = "idle"


def _hira_modal_context(entry: dict[str, Any]) -> tuple[bool, str]:
    """진료내역 카카오 인증 모달 단계: intro | kakao_sent | kakao_error | fetching | done | error."""
    status = entry.get("medical_status") or "pending"
    second = entry.get("second_status") or "idle"
    if second == "in_progress" and status in ("waiting_auth", "waiting_auth_debug_needed"):
        return True, "fetching"
    if status == "completed":
        return True, "done"
    if status == "failed":
        return True, "error"
    if status == "waiting_auth":
        return True, "kakao_sent"
    if status == "waiting_auth_debug_needed":
        return True, "kakao_error"
    return False, "intro"


def _hira_codef_debug_context(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not _debug_panel():
        return None
    counts = entry.get("medical_result_counts") or {}
    root_keys = entry.get("codef_root_keys") or []
    data_keys = entry.get("codef_data_keys") or []
    flow_keys = sorted(str(k) for k in entry.keys())
    return {
        "codef_result_code": entry.get("codef_result_code") or "—",
        "codef_result_message": entry.get("codef_result_message") or "—",
        "codef_second_result_code": entry.get("codef_second_result_code") or "—",
        "codef_second_result_message": entry.get("codef_second_result_message") or "—",
        "continue2_way": entry.get("codef_continue2_way"),
        "method": entry.get("codef_method") or "—",
        "two_way_info_saved": bool(entry.get("two_way_info")),
        "two_way_info_found": entry.get("two_way_info_found"),
        "second_status": entry.get("second_status") or "idle",
        "medical_result_counts": counts,
        "data_keys": ", ".join(data_keys) if data_keys else "—",
        "flow_keys": ", ".join(flow_keys) if flow_keys else "—",
        "flow_has_two_way_info": bool(entry.get("two_way_info")),
    }


def _request_hira_auth_codef(flow_id: str, entry: dict[str, Any]) -> None:
    """실제 CODEF 심평원 1차 인증 요청."""
    customer = entry.get("customer") or {}
    payload = build_hira_medical_payload(customer, flow_id)
    entry["hira_first_payload"] = dict(payload)
    entry["second_status"] = entry.get("second_status") or "idle"
    api_result = post_hira_medical_first(payload)
    parsed = api_result.get("parsed")
    result = api_result.get("result") or {}
    extracted = extract_two_way_info(parsed)

    if is_hira_auth_waiting(parsed, result, api_result.get("data") or {}):
        result_code = str(result.get("code") or "")
        if result_code == "CF-03002" and extracted["twoWayInfo_found"]:
            _apply_hira_waiting_auth(entry, result=result, extracted=extracted)
        elif result_code == "CF-03002" and not extracted["twoWayInfo_found"]:
            _apply_hira_waiting_auth_debug_needed(entry, result=result, extracted=extracted)
        else:
            _apply_hira_waiting_auth(entry, result=result, extracted=extracted)
        return

    result_code = str(result.get("code") or "")
    result_message = str(result.get("message") or "")
    _apply_hira_auth_failed(
        entry,
        result_code=result_code,
        result_message=result_message,
        user_message=user_message_for_codef_failure(result_code, result_message),
    )


def _pick_medical_field(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "-"


def _normalize_basic_medical_row(raw: Any) -> dict[str, str]:
    """기본진료내역 1건 정규화."""
    if not isinstance(raw, dict):
        return {
            "visit_date": "-",
            "hospital_name": "-",
            "department": "-",
            "treat_type": "-",
            "main_diagnosis": "-",
            "diagnosis": "-",
            "copay_amount": "-",
        }
    main_diagnosis = _pick_medical_field(
        raw,
        "resMainDiseaseName",
        "resDiseaseName",
        "resSickName",
        "main_disease",
        "disease_name",
        "diagnosis",
    )
    return {
        "visit_date": _pick_medical_field(
            raw, "resTreatDate", "resVisitDate", "date", "treatment_date", "visit_date"
        ),
        "hospital_name": _pick_medical_field(
            raw, "resHospitalName", "resInstitutionName", "hospital_name"
        ),
        "department": _pick_medical_field(raw, "resDepartment", "resDeptName", "department"),
        "treat_type": _pick_medical_field(
            raw, "resTreatType", "resTreatKind", "resCareType", "treat_type", "treatment_type"
        ),
        "main_diagnosis": main_diagnosis,
        "diagnosis": main_diagnosis,
        "copay_amount": _pick_medical_field(
            raw,
            "resDeductibleAmt",
            "resPaidAmount",
            "resSelfPayAmount",
            "patient_paid_amount",
            "copay_amount",
        ),
    }


def _normalize_detail_medical_row(raw: Any) -> dict[str, str]:
    """세부진료정보 1건 정규화."""
    if not isinstance(raw, dict):
        return {
            "visit_date": "-",
            "hospital_name": "-",
            "item_name": "-",
            "dose": "-",
            "frequency": "-",
            "days": "-",
        }
    return {
        "visit_date": _pick_medical_field(
            raw, "resTreatDate", "resVisitDate", "date", "treatment_date", "visit_date"
        ),
        "hospital_name": _pick_medical_field(
            raw, "resHospitalName", "resInstitutionName", "hospital_name"
        ),
        "item_name": _pick_medical_field(
            raw,
            "resItemName",
            "resCodeName",
            "resTreatName",
            "resDetailName",
            "resDetailItemName",
            "code_name",
            "item_name",
        ),
        "dose": _pick_medical_field(
            raw, "resDosage", "resDose", "resAmount", "resDrugAmount", "dosage", "dose"
        ),
        "frequency": _pick_medical_field(
            raw, "resFrequency", "resTimes", "resCount", "resDrugCount", "frequency", "times"
        ),
        "days": _pick_medical_field(
            raw,
            "resDays",
            "resTotalDays",
            "resMedicationDays",
            "resDrugDays",
            "total_days",
            "days",
        ),
    }


def _normalize_prescribe_medical_row(raw: Any) -> dict[str, str]:
    """처방조제내역 1건 정규화."""
    if not isinstance(raw, dict):
        return {
            "dispense_date": "-",
            "pharmacy_name": "-",
            "drug_name": "-",
            "ingredient": "-",
            "dose": "-",
            "frequency": "-",
            "days": "-",
        }
    return {
        "dispense_date": _pick_medical_field(
            raw,
            "resPrescribeDate",
            "resDispenseDate",
            "resTreatDate",
            "dispense_date",
            "prescribe_date",
            "visit_date",
        ),
        "pharmacy_name": _pick_medical_field(
            raw,
            "resPharmacyName",
            "resHospitalName",
            "resInstitutionName",
            "pharmacy_name",
            "hospital_name",
        ),
        "drug_name": _pick_medical_field(
            raw, "resDrugName", "resMedicineName", "drug_name", "medicine_name"
        ),
        "ingredient": _pick_medical_field(
            raw,
            "resIngredientName",
            "resComponentName",
            "resIngrName",
            "ingredient_name",
            "ingredient",
        ),
        "dose": _pick_medical_field(
            raw, "resDosage", "resDose", "resAmount", "resDrugAmount", "dosage", "dose"
        ),
        "frequency": _pick_medical_field(
            raw, "resFrequency", "resTimes", "resCount", "resDrugCount", "frequency", "times"
        ),
        "days": _pick_medical_field(
            raw,
            "resDays",
            "resTotalDays",
            "resMedicationDays",
            "resDrugDays",
            "total_days",
            "days",
        ),
    }


def _normalize_medical_record_list(
    raw_list: Any,
    normalizer: Any,
) -> list[dict[str, str]]:
    if not isinstance(raw_list, list):
        return []
    return [normalizer(row) for row in raw_list]


def _medical_records_basic_all(entry: dict[str, Any]) -> list[dict[str, str]]:
    raw_list = entry.get("medical_records_basic")
    if not isinstance(raw_list, list) or not raw_list:
        fallback = entry.get("medical_records")
        raw_list = fallback if isinstance(fallback, list) else []
    return _normalize_medical_record_list(raw_list, _normalize_basic_medical_row)


def _medical_records_detail_all(entry: dict[str, Any]) -> list[dict[str, str]]:
    return _normalize_medical_record_list(
        entry.get("medical_records_detail"),
        _normalize_detail_medical_row,
    )


def _medical_records_prescribe_all(entry: dict[str, Any]) -> list[dict[str, str]]:
    return _normalize_medical_record_list(
        entry.get("medical_records_prescribe"),
        _normalize_prescribe_medical_row,
    )


def _ensure_medical_records_loaded(flow_id: str, entry: dict[str, Any]) -> bool:
    """FLOW_STORE → DB 저장본 순으로 진료내역 로드(CODEF 미호출)."""
    if entry.get("medical_status") == "completed":
        basic = entry.get("medical_records_basic") or entry.get("medical_records")
        if isinstance(basic, list) and basic:
            return True
    _apply_saved_medical_records(flow_id, entry)
    if entry.get("medical_status") != "completed":
        return False
    basic = entry.get("medical_records_basic") or entry.get("medical_records")
    return isinstance(basic, list) and bool(basic)


def _medical_records_view_context(entry: dict[str, Any]) -> dict[str, Any]:
    counts = entry.get("medical_result_counts")
    if not isinstance(counts, dict):
        counts = {}
    basic_all = _medical_records_basic_all(entry)
    detail_all = _medical_records_detail_all(entry)
    prescribe_all = _medical_records_prescribe_all(entry)
    counts = {
        "basic": int(counts.get("basic") or len(basic_all)),
        "detail": int(counts.get("detail") or len(detail_all)),
        "prescribe": int(counts.get("prescribe") or len(prescribe_all)),
    }
    source = str(entry.get("medical_records_source") or "saved")
    note = (
        "저장된 진료내역 기준입니다."
        if source.startswith("saved") or entry.get("loaded_medical_record_id")
        else ""
    )
    return {
        "medical_result_counts": counts,
        "medical_records_basic_all": basic_all,
        "medical_records_detail_all": detail_all,
        "medical_records_prescribe_all": prescribe_all,
        "medical_source_note": note,
    }


def _build_customer_display(customer: dict[str, Any]) -> dict[str, str]:
    auth = (customer.get("auth_method") or "").strip().lower()
    label = "카카오톡 간편인증" if auth == "kakao" else "본인인증"
    return {
        "name": (customer.get("name") or "").strip() or "—",
        "identity_masked": _mask_identity(str(customer.get("identity") or "")),
        "phone_masked": _mask_phone(str(customer.get("phone") or "")),
        "email": (customer.get("email") or "").strip() or "—",
        "auth_label": label,
    }


_MEDICAL_SAMPLE_RECORDS: list[dict[str, str]] = [
    {
        "hospital_name": "시연용 종합병원 A",
        "visit_date": "2025-11-02",
        "department": "내과",
        "diagnosis": "급성 상기도염 (시연용)",
        "copay_amount": "15,600원",
    },
    {
        "hospital_name": "시연용 의원 B",
        "visit_date": "2025-09-18",
        "department": "이비인후과",
        "diagnosis": "알레르기 비염 (시연용)",
        "copay_amount": "8,200원",
    },
    {
        "hospital_name": "시연용 종합병원 A",
        "visit_date": "2025-08-05",
        "department": "정형외과",
        "diagnosis": "요추 추간판 탈출증 (시연용)",
        "copay_amount": "34,100원",
    },
    {
        "hospital_name": "시연용 약국 C",
        "visit_date": "2025-08-05",
        "department": "약제과",
        "diagnosis": "처방 조제 (시연용)",
        "copay_amount": "12,000원",
    },
    {
        "hospital_name": "시연용 의원 D",
        "visit_date": "2025-06-22",
        "department": "소아과",
        "diagnosis": "급성 기관지염 (시연용)",
        "copay_amount": "9,800원",
    },
    {
        "hospital_name": "시연용 종합병원 E",
        "visit_date": "2025-04-10",
        "department": "안과",
        "diagnosis": "안구건조증 (시연용)",
        "copay_amount": "6,500원",
    },
    {
        "hospital_name": "시연용 의원 F",
        "visit_date": "2024-12-03",
        "department": "피부과",
        "diagnosis": "접촉성 피부염 (시연용)",
        "copay_amount": "11,300원",
    },
]


_INSURANCE_SAMPLE_RECORDS: list[dict[str, Any]] = [
    {
        "company": "NH농협손해보험",
        "product_name": "무배당NH프리미어운전자보험1804",
        "policy_no": "DEMO-001",
        "status": "유지",
        "role": "피보험자",
        "category": "운전자",
        "include_for_claim_review": True,
    },
    {
        "company": "NH농협손해보험",
        "product_name": "무배당헤아림실손의료비보험2001",
        "policy_no": "DEMO-002",
        "status": "유지",
        "role": "피보험자",
        "category": "실손",
        "include_for_claim_review": True,
    },
    {
        "company": "NH농협손해보험",
        "product_name": "시연용 종신보험(계약자 전용)",
        "policy_no": "DEMO-003",
        "status": "유지",
        "role": "계약자",
        "category": "종합",
        "include_for_claim_review": False,
    },
    {
        "company": "삼성화재",
        "product_name": "시연용 실손의료비보험",
        "policy_no": "DEMO-004",
        "status": "유지",
        "role": "피보험자",
        "category": "실손",
        "include_for_claim_review": True,
    },
    {
        "company": "삼성화재",
        "product_name": "시연용 종합건강보험",
        "policy_no": "DEMO-005",
        "status": "유지",
        "role": "피보험자",
        "category": "종합",
        "include_for_claim_review": True,
    },
    {
        "company": "현대해상",
        "product_name": "시연용 상해보험",
        "policy_no": "DEMO-006",
        "status": "유지",
        "role": "피보험자",
        "category": "기타",
        "include_for_claim_review": True,
    },
    {
        "company": "현대해상",
        "product_name": "시연용 실손의료비보험",
        "policy_no": "DEMO-007",
        "status": "유지",
        "role": "피보험자",
        "category": "실손",
        "include_for_claim_review": True,
    },
    {
        "company": "KB손해보험",
        "product_name": "시연용 실손의료비보험",
        "policy_no": "DEMO-008",
        "status": "유지",
        "role": "피보험자",
        "category": "실손",
        "include_for_claim_review": True,
    },
    {
        "company": "KB손해보험",
        "product_name": "시연용 암보험",
        "policy_no": "DEMO-009",
        "status": "유지",
        "role": "피보험자",
        "category": "기타",
        "include_for_claim_review": True,
    },
    {
        "company": "DB손해보험",
        "product_name": "시연용 실손의료비보험",
        "policy_no": "DEMO-010",
        "status": "유지",
        "role": "피보험자",
        "category": "실손",
        "include_for_claim_review": True,
    },
    {
        "company": "DB손해보험",
        "product_name": "시연용 운전자보험",
        "policy_no": "DEMO-011",
        "status": "유지",
        "role": "피보험자",
        "category": "운전자",
        "include_for_claim_review": True,
    },
]


def _signup_auto_retry_max() -> int:
    raw = (os.getenv("CREDIT4U_SIGNUP_AUTO_RETRY_MAX") or "3").strip()
    try:
        return max(0, min(10, int(raw)))
    except ValueError:
        return 3


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _format_credential_save_error(exc: BaseException) -> str:
    """DEBUG용 저장 오류(비밀번호·주민·전화 원문 미포함)."""
    raw = f"{type(exc).__name__}: {str(exc)[:200]}"
    scrubbed = re.sub(
        r"(?i)(password|passwd|비밀번호)\s*[=:]\s*\S+",
        r"\1=***",
        raw,
    )
    scrubbed = re.sub(r"Aa![^\s]{4,}#?", "Aa!***", scrubbed)
    return scrubbed[:220]


def _is_credit4u_generation_blocked(entry: dict[str, Any]) -> bool:
    """이미 가입·기존 계정 입력 단계에서는 신규 ID/PW 생성 금지."""
    stage = str(entry.get("insurance_stage") or "")
    if stage in ("already_registered", "existing_account_required"):
        return True
    if entry.get("already_registered_handled"):
        return True
    if str(entry.get("credit4u_result_code") or "") == "CF-12069":
        return True
    return False


def _apply_restored_credit4u_credentials(
    entry: dict[str, Any],
    restored: dict[str, Any],
) -> None:
    stored_source = str(restored.get("source") or "generated").strip()
    entry["credit4u_credentials"] = {
        "id": restored["id"],
        "password": restored["password"],
        "generated": stored_source == "generated",
        "source": "stored",
        "credential_version": str(
            restored.get("credential_version") or CREDIT4U_CREDENTIAL_VERSION
        ),
    }
    entry["credential_source"] = stored_source
    entry["credential_version"] = entry["credit4u_credentials"]["credential_version"]
    entry["credit4u_id_attempt_no"] = int(restored.get("credit4u_id_attempt_no") or 0)
    entry["credit4u_credentials_restored"] = True
    entry["stored_credit4u_credentials_exists"] = True
    entry["credential_loaded_from_store"] = True
    entry["credential_generated_new"] = False


def _ensure_credit4u_credentials_for_entry(
    entry: dict[str, Any],
    *,
    allow_generate: bool = True,
) -> None:
    """신용정보원 계정: 1) DB 저장본 2) FLOW_STORE 3) 신규 생성(허용 시만)."""
    entry.pop("credit4u_credentials_error", None)
    customer = entry.get("customer") or {}
    if not all(
        (
            str(customer.get("name") or "").strip(),
            str(customer.get("identity") or "").strip(),
            str(customer.get("phone") or "").strip(),
        )
    ):
        return

    entry.setdefault("stored_credit4u_credentials_exists", False)

    if is_search_hash_secret_configured():
        try:
            restored = restore_credit4u_credentials(customer)
        except Exception:
            restored = None
        if isinstance(restored, dict) and str(restored.get("id") or "").strip():
            if str(restored.get("password") or "").strip():
                _apply_restored_credit4u_credentials(entry, restored)
                return

    existing = entry.get("credit4u_credentials")
    if isinstance(existing, dict) and str(existing.get("id") or "").strip():
        if str(existing.get("password") or "").strip():
            entry["credential_loaded_from_store"] = False
            entry["credential_generated_new"] = False
            if entry.get("credit4u_id_attempt_no") is None:
                entry["credit4u_id_attempt_no"] = int(
                    existing.get("credit4u_id_attempt_no") or 0
                )
            return

    if _is_credit4u_generation_blocked(entry) or not allow_generate:
        entry.pop("credit4u_credentials", None)
        entry["credit4u_credentials_error"] = (
            "저장된 신용정보원 계정 또는 기존 계정 입력이 필요합니다."
        )
        entry["credential_generated_new"] = False
        return

    if not get_credit4u_secret():
        entry["credit4u_credentials_error"] = "REDRIBBON_CREDIT4U_SECRET 설정 필요"
        entry.pop("credit4u_credentials", None)
        entry["credential_generated_new"] = False
        return
    try:
        attempt = int(entry.get("credit4u_id_attempt_no") or 0)
        if attempt > 0:
            creds = regenerate_credit4u_credentials(customer, attempt)
        else:
            creds = generate_credit4u_credentials(customer)
    except Credit4uConfigError:
        entry["credit4u_credentials_error"] = "REDRIBBON_CREDIT4U_SECRET 설정 필요"
        entry.pop("credit4u_credentials", None)
        entry["credential_generated_new"] = False
        return
    except ValueError:
        entry["credit4u_credentials_error"] = "신용정보원 계정을 생성할 수 없습니다."
        entry.pop("credit4u_credentials", None)
        entry["credential_generated_new"] = False
        return
    entry["credit4u_credentials"] = {
        "id": creds["id"],
        "password": creds["password"],
        "generated": True,
        "source": "generated",
        "credential_version": str(
            creds.get("credential_version") or CREDIT4U_CREDENTIAL_VERSION
        ),
    }
    entry["credential_source"] = "generated"
    entry["credential_version"] = entry["credit4u_credentials"]["credential_version"]
    entry["credential_loaded_from_store"] = False
    entry["credential_generated_new"] = True


def _provision_credit4u_credentials(entry: dict[str, Any]) -> None:
    """고객 flow에 신용정보원 계정 준비(저장본 우선)."""
    allow_generate = not _is_credit4u_generation_blocked(entry)
    _ensure_credit4u_credentials_for_entry(entry, allow_generate=allow_generate)


def _credit4u_credentials_view_context(entry: dict[str, Any]) -> dict[str, Any]:
    """템플릿용 신용정보원 계정 카드(비밀번호는 화면 토글로만 노출)."""
    creds = entry.get("credit4u_credentials")
    cred_error = entry.get("credit4u_credentials_error")
    cred_error_text = str(cred_error).strip() if cred_error else None
    if isinstance(creds, dict) and str(creds.get("id") or "").strip():
        password = str(creds.get("password") or "")
        return {
            "show_credit4u_credentials_card": True,
            "credit4u_credentials": creds,
            "credit4u_credentials_error": cred_error_text,
            "credit4u_id": str(creds.get("id") or ""),
            "credit4u_password_available": bool(password),
            "credit4u_password": password,
        }
    if cred_error_text:
        return {
            "show_credit4u_credentials_card": True,
            "credit4u_credentials": None,
            "credit4u_credentials_error": cred_error_text,
            "credit4u_id": "",
            "credit4u_password_available": False,
            "credit4u_password": "",
        }
    return {
        "show_credit4u_credentials_card": False,
        "credit4u_credentials": None,
        "credit4u_credentials_error": None,
        "credit4u_id": "",
        "credit4u_password_available": False,
        "credit4u_password": "",
    }


def _credit4u_secure_no_view(req_secure_no: Any) -> dict[str, Any]:
    """보안문자 이미지 표시용(원문은 captcha 데이터만)."""
    value = str(req_secure_no or "").strip()
    if not value:
        return {"has_image": False, "image_src": ""}
    if value.startswith("data:image"):
        return {"has_image": True, "image_src": value}
    return {"has_image": True, "image_src": f"data:image/png;base64,{value}"}


def _store_credit4u_payload_debug(entry: dict[str, Any], api_result: dict[str, Any]) -> None:
    payload_debug = api_result.get("payload_debug")
    if isinstance(payload_debug, dict):
        entry["credit4u_payload_debug"] = payload_debug


def _store_credit4u_register_debug(entry: dict[str, Any], api_result: dict[str, Any]) -> None:
    register_debug = api_result.get("register_debug")
    if isinstance(register_debug, dict):
        signup_timing = entry.get("credit4u_register_signup_timing")
        if isinstance(signup_timing, dict):
            register_debug = {**register_debug, **signup_timing}
        entry["credit4u_register_debug"] = register_debug


def _resolve_entry_flow_id(entry: dict[str, Any], flow_id: str | None = None) -> str:
    """FLOW_STORE 항목에 대응하는 flow_id."""
    if flow_id:
        entry["flow_id"] = flow_id
        return flow_id
    stored = str(entry.get("flow_id") or "").strip()
    if stored:
        return stored
    for fid, stored_entry in FLOW_STORE.items():
        if stored_entry is entry:
            entry["flow_id"] = fid
            return fid
    return ""


def _ensure_register_first_payload(
    entry: dict[str, Any],
    flow_id: str,
) -> dict[str, Any]:
    """저장된 register 1차 payload 반환(없으면 재생성, checkParamUUID 보강)."""
    check_uuid = ensure_credit4u_check_param_uuid(entry, flow_id)
    first_payload = entry.get("credit4u_register_first_payload")
    if isinstance(first_payload, dict) and first_payload:
        merged = dict(first_payload)
        if not str(merged.get("checkParamUUID") or "").strip():
            merged["checkParamUUID"] = check_uuid
        merged["timeout"] = credit4u_register_payload_timeout_value()
        entry["credit4u_register_first_payload"] = merged
        return merged
    customer = entry.get("customer") or {}
    credentials = entry.get("credit4u_credentials") or {}
    built = build_credit4u_register_first_payload(customer, credentials, check_uuid)
    entry["credit4u_register_first_payload"] = dict(built)
    return built


def _start_credit4u_register(
    entry: dict[str, Any],
    flow_id: str | None = None,
) -> None:
    """신용정보원 register 1차 자동 시작(CF-12832 등)."""
    customer = entry.get("customer") or {}
    if not customer:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "고객 정보가 없어 회원가입을 진행할 수 없습니다."
        return

    credentials = entry.get("credit4u_credentials")
    if not isinstance(credentials, dict) or not str(credentials.get("id") or "").strip():
        _provision_credit4u_credentials(entry)
        credentials = entry.get("credit4u_credentials")
    if not isinstance(credentials, dict) or not str(credentials.get("id") or "").strip():
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "신용정보원 조회 계정이 준비되지 않았습니다."
        return

    entry["insurance_status"] = "in_progress"
    entry["credit4u_current_flow"] = "register"
    entry["insurance_stage"] = "register_requesting"
    entry["credit4u_current_request_status"] = "register_requesting"
    entry["insurance_message"] = (
        "신용정보원 회원가입 절차를 시작하고 있습니다. 잠시만 기다려 주세요."
    )
    entry.pop("insurance_error_code", None)

    fid = _resolve_entry_flow_id(entry, flow_id)
    if not fid:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "흐름 ID를 확인할 수 없어 회원가입을 진행할 수 없습니다."
        return

    check_uuid = ensure_credit4u_check_param_uuid(entry, fid)
    api_result = post_credit4u_register_first(customer, credentials, check_uuid)
    _apply_credit4u_register_first_response(entry, api_result)


def _set_insurance_submitting(
    entry: dict[str, Any],
    stage: str,
    message: str,
) -> None:
    prior = str(entry.get("insurance_stage") or "")
    if prior and prior not in (
        "register_signup_info_submitting",
        "register_sms_submitting",
        "register_secure_no_submitting",
        "register_email_auth_submitting",
        "register_requesting",
    ):
        entry["credit4u_last_register_stage"] = prior
    entry["insurance_status"] = "in_progress"
    entry["insurance_stage"] = stage
    entry["credit4u_current_request_status"] = stage
    entry["insurance_message"] = message


def _apply_credit4u_register_retryable_timeout(
    entry: dict[str, Any],
    result_code: str,
    result_message: str,
) -> None:
    """CF-01004 — 회원가입 단계 유지(twoWayInfo·payload 삭제 금지)."""
    entry["insurance_status"] = "in_progress"
    entry["credit4u_current_flow"] = "register"
    entry["insurance_stage"] = "register_retryable_timeout"
    entry["credit4u_current_request_status"] = "register_retryable_timeout"
    entry["insurance_message"] = _register_stage_user_message("register_retryable_timeout")
    entry["credit4u_result_code"] = result_code
    entry["credit4u_result_message"] = result_message
    entry.pop("insurance_error_code", None)


def _resolve_register_retry_stage(entry: dict[str, Any]) -> str:
    """저장된 extraInfo 기준 재시도 입력 단계."""
    extra_info = entry.get("credit4u_register_extra_info")
    if not isinstance(extra_info, dict):
        extra_info = {}
    if extra_info_requests_signup_info(extra_info):
        return "register_signup_info_required"
    if extra_info_requests_email_auth(extra_info):
        return "register_email_auth_required"
    if extra_info_requests_sms(extra_info):
        return "register_sms_required"
    if extra_info_has_request_key(extra_info, "reqSecureNo"):
        return "register_secure_no_required"
    return "register_signup_info_required"


def _register_stage_user_message(stage: str) -> str:
    messages = {
        "register_requesting": (
            "신용정보원 회원가입 절차를 시작하고 있습니다. 잠시만 기다려 주세요."
        ),
        "register_secure_no_required": "회원가입을 위한 보안문자 입력이 필요합니다.",
        "register_secure_no_submitting": (
            "회원가입 보안문자를 확인하고 있습니다. 잠시만 기다려 주세요."
        ),
        "register_sms_submitting": "SMS 인증번호를 확인하고 있습니다. 잠시만 기다려 주세요.",
        "register_sms_required": "휴대폰 SMS 인증번호를 입력해 주세요.",
        "register_signup_info_submitting": (
            "회원가입 정보를 제출하고 있습니다. 신용정보원 응답을 기다리는 중입니다. 창을 닫지 마세요."
        ),
        "register_retryable_timeout": (
            "신용정보원 응답 대기시간이 초과되었습니다. 현재 단계에서 다시 시도해 주세요."
        ),
        "register_email_auth_submitting": (
            "이메일 인증번호를 확인하고 있습니다. 잠시만 기다려 주세요."
        ),
        "register_signup_info_required": (
            "회원가입에 사용할 아이디, 비밀번호, 이메일 정보가 필요합니다."
        ),
        "register_email_auth_required": "이메일 인증번호를 입력해 주세요.",
        "register_completed": "신용정보원 회원가입이 완료되었습니다.",
        "register_continue_pending": "회원가입 절차를 계속 진행합니다.",
    }
    return messages.get(stage, "회원가입 절차를 계속 진행합니다.")


def _update_credit4u_register_session(
    entry: dict[str, Any],
    api_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """register 응답에서 twoWayInfo·extraInfo 갱신."""
    data = api_result.get("data") if isinstance(api_result.get("data"), dict) else {}
    extracted = api_result.get("extracted") if isinstance(api_result.get("extracted"), dict) else {}
    extra_info = api_result.get("extra_info") if isinstance(api_result.get("extra_info"), dict) else {}
    if not extra_info:
        extra_info = extract_register_extra_info(data)

    two_way = extracted.get("twoWayInfo")
    if not two_way:
        two_way = {
            "jobIndex": data.get("jobIndex"),
            "threadIndex": data.get("threadIndex"),
            "jti": data.get("jti"),
            "twoWayTimestamp": data.get("twoWayTimestamp"),
        }
    if isinstance(two_way, dict):
        two_way = sanitize_credit4u_two_way_info(two_way)
        entry["credit4u_register_two_way_info"] = two_way
    entry["credit4u_register_extra_info"] = extra_info
    store_register_extra_info_on_entry(entry, extra_info)
    return data, extracted, extra_info


def _apply_register_signup_stage_from_extra(
    entry: dict[str, Any],
    extra_info: dict[str, Any],
    result_code: str,
) -> None:
    """register_signup_info_required 단계 — extraInfo 요청 필드별 안내."""
    required = resolve_signup_required_fields(extra_info)
    entry["insurance_status"] = "in_progress"
    entry["credit4u_current_flow"] = "register"
    entry["insurance_stage"] = "register_signup_info_required"
    entry["credit4u_current_request_status"] = "register_signup_info_required"
    if required:
        entry["credit4u_signup_required_fields"] = required
    else:
        entry.pop("credit4u_signup_required_fields", None)
    entry["credit4u_signup_id_retry"] = "id" in required
    entry["insurance_message"] = register_signup_retry_message(result_code, extra_info)
    entry.pop("insurance_error_code", None)


def _regenerate_credit4u_signup_credentials(entry: dict[str, Any]) -> None:
    """회원가입 ID·비밀번호 재생성(attempt 증가, checkParamUUID/twoWayInfo 유지)."""
    customer = entry.get("customer") or {}
    creds = dict(entry.get("credit4u_credentials") or {})
    prev_id = str(creds.get("id") or "").strip()
    attempt = int(entry.get("credit4u_id_attempt_no") or 0) + 1
    entry["credit4u_id_attempt_no"] = attempt

    renewed = regenerate_credit4u_credentials(
        customer, attempt, previous_id=prev_id or None
    )
    entry["credit4u_credentials"] = {
        **creds,
        "id": renewed["id"],
        "password": renewed["password"],
        "generated": True,
        "source": "generated",
        "credential_version": str(
            renewed.get("credential_version") or CREDIT4U_CREDENTIAL_VERSION
        ),
    }
    entry["credential_version"] = entry["credit4u_credentials"]["credential_version"]
    required = list(entry.get("credit4u_signup_required_fields") or [])
    if "id" not in required and entry.get("credit4u_signup_id_retry"):
        required = ["id", *required]
    entry["credit4u_signup_required_fields"] = required or ["id"]
    entry["credit4u_signup_id_retry"] = True


def _flow_has_usable_credit4u_credentials(entry: dict[str, Any]) -> bool:
    creds = entry.get("credit4u_credentials") or {}
    return bool(
        str(creds.get("id") or "").strip() and str(creds.get("password") or "").strip()
    )


def _load_stored_credit4u_credentials_into_entry(entry: dict[str, Any]) -> bool:
    """DB 저장본을 FLOW_STORE에 로드."""
    customer = entry.get("customer") or {}
    if not is_search_hash_secret_configured():
        return False
    try:
        restored = restore_credit4u_credentials(customer)
    except Exception:
        return False
    if not isinstance(restored, dict) or not str(restored.get("id") or "").strip():
        return False
    if not str(restored.get("password") or "").strip():
        return False
    _apply_restored_credit4u_credentials(entry, restored)
    entry["credential_source"] = str(restored.get("source") or "generated").strip()
    return True


def _save_credit4u_credentials_to_store(entry: dict[str, Any]) -> bool:
    """회원가입 완료 등 FLOW_STORE 최종 확정 계정을 고객별 저장·재조회 검증."""
    entry.pop("credential_save_error", None)
    customer = entry.get("customer") or {}
    creds = entry.get("credit4u_credentials") or {}
    user_id = str(creds.get("id") or "").strip()
    password = str(creds.get("password") or "").strip()
    if not user_id or not password:
        entry["credit4u_credentials_saved"] = False
        entry["credential_save_error"] = "missing_id_or_password"
        return False
    if not is_search_hash_secret_configured():
        entry["credit4u_credentials_saved"] = False
        entry["credential_save_error"] = "PersistentStoreConfigError: REDRIBBON_SEARCH_HASH_SECRET is missing"
        return False
    email = str(
        entry.get("credit4u_signup_email") or entry.get("credit4u_register_email") or ""
    ).strip()
    creds_source = str(
        creds.get("source") or entry.get("credential_source") or "generated"
    ).strip()
    attempt_no = int(entry.get("credit4u_id_attempt_no") or 0)
    metadata = {
        "credential_source": creds_source,
        "credential_version": str(
            creds.get("credential_version")
            or entry.get("credential_version")
            or CREDIT4U_CREDENTIAL_VERSION
        ),
        "credit4u_id_attempt_no": attempt_no,
        "email": email,
        "register_completed_at": _utc_now_iso(),
    }
    try:
        customer_key = persist_credit4u_credentials(customer, creds, metadata)
    except Exception as exc:
        entry["credit4u_credentials_saved"] = False
        entry["credential_save_error"] = _format_credential_save_error(exc)
        logger.warning(
            "credit4u_credentials save failed err=%s",
            type(exc).__name__,
        )
        return False
    if not customer_key:
        entry["credit4u_credentials_saved"] = False
        entry["credential_save_error"] = "persist_returned_empty"
        return False
    if not verify_persisted_credit4u_credentials(customer, expected_id=user_id):
        entry["credit4u_credentials_saved"] = False
        entry["credential_save_error"] = "verify_reload_failed"
        return False
    entry["credit4u_credentials_saved"] = True
    entry["stored_credit4u_credentials_exists"] = True
    entry["customer_key"] = customer_key
    entry["credential_version"] = metadata["credential_version"]
    entry["credential_source"] = creds_source
    entry["credit4u_id_attempt_no"] = attempt_no
    return True


def _start_contract_after_already_registered(flow_id: str, entry: dict[str, Any]) -> None:
    """CF-12069 처리 후 contract-info 자동 시작(무한 반복 방지)."""
    if entry.get("already_registered_auto_contract_started"):
        return
    if not flow_id:
        return
    entry["already_registered_auto_contract_started"] = True
    try:
        entry["insurance_contract_auto_started"] = "예"
        _start_credit4u_contract_info_first(flow_id, entry)
        entry["insurance_contract_result_code"] = str(entry.get("credit4u_result_code") or "")
    except (Credit4uConfigError, ValueError) as exc:
        entry["insurance_stage"] = "already_registered"
        entry["credit4u_current_request_status"] = "already_registered"
        entry["insurance_message"] = (
            "이미 신용정보원에 가입된 고객입니다. "
            f"보험가입이력 자동 조회를 시작하지 못했습니다. ({exc})"
        )


def _apply_credit4u_already_registered(
    flow_id: str,
    entry: dict[str, Any],
    result_message: str,
) -> None:
    """CF-12069 — 이미 가입된 고객: 회원가입 재시도 없이 contract-info로 전환."""
    entry["insurance_status"] = "in_progress"
    entry["insurance_stage"] = "already_registered"
    entry["credit4u_current_request_status"] = "already_registered"
    entry["credit4u_current_flow"] = "contract"
    entry["insurance_message"] = (
        "이미 신용정보원에 가입된 고객입니다. 저장된 계정정보로 보험가입이력을 조회합니다."
    )
    entry["credit4u_result_code"] = "CF-12069"
    entry["credit4u_result_message"] = result_message
    entry["already_registered_handled"] = True
    entry.pop("insurance_error_code", None)

    fid = _resolve_entry_flow_id(entry, flow_id)

    if entry.get("already_registered_auto_contract_started"):
        return

    if _load_stored_credit4u_credentials_into_entry(entry):
        _start_contract_after_already_registered(fid, entry)
        return

    if _flow_has_usable_credit4u_credentials(entry):
        _save_credit4u_credentials_to_store(entry)
        _start_contract_after_already_registered(fid, entry)
        return

    entry["insurance_stage"] = "existing_account_required"
    entry["credit4u_current_request_status"] = "existing_account_required"
    entry["insurance_message"] = (
        "이미 신용정보원에 가입된 고객입니다. "
        "기존 신용정보원 아이디와 비밀번호를 입력해 주세요."
    )


def _handle_credit4u_already_registered(
    entry: dict[str, Any],
    result_code: str,
    result_message: str,
    *,
    flow_id: str | None = None,
) -> bool:
    if not is_credit4u_already_registered(result_code):
        return False
    _apply_credit4u_already_registered(
        flow_id or _resolve_entry_flow_id(entry, flow_id),
        entry,
        result_message,
    )
    return True


def _persist_insurance_records_to_sqlite(
    flow_id: str,
    entry: dict[str, Any],
    raw_data: dict[str, Any],
) -> None:
    """contract-info 성공 원부 SQLite 저장."""
    customer = entry.get("customer") or {}
    if not is_search_hash_secret_configured():
        entry["insurance_records_saved"] = False
        return
    normalized = list(entry.get("insurance_records") or [])
    summary = entry.get("insurance_summary") or insurance_summary_from_records(normalized)
    try:
        save_insurance_records(customer, flow_id, raw_data, normalized, summary)
        entry["insurance_records_saved"] = True
    except PersistentStoreConfigError:
        entry["insurance_records_saved"] = False
    except Exception as exc:
        entry["insurance_records_saved"] = False
        logger.warning(
            "insurance_records not saved flow_id=%s err=%s",
            flow_id,
            type(exc).__name__,
        )


def _post_register_signup_info_sync(entry: dict[str, Any], flow_id: str) -> dict[str, Any]:
    """signup_info 2차 POST(서버 내부 자동 재시도용)."""
    credentials = entry.get("credit4u_credentials") or {}
    user_id = str(credentials.get("id") or "").strip()
    password = str(credentials.get("password") or "").strip()
    email_input = str(
        entry.get("credit4u_signup_email") or entry.get("credit4u_register_email") or ""
    ).strip()
    if not user_id or not password or not email_input:
        raise ValueError("signup_info fields incomplete")

    first_payload = _ensure_register_first_payload(entry, flow_id)
    two_way_info = entry.get("credit4u_register_two_way_info")
    if not isinstance(two_way_info, dict) or not two_way_info:
        raise ValueError("missing register twoWayInfo")

    signup_timing = new_register_signup_timing_debug()
    signup_timing["register_signup_info_post_entered"] = "예"
    entry["credit4u_register_signup_timing"] = signup_timing
    entry["credit4u_register_payload_purpose"] = "signup_info"
    return post_credit4u_register_second(
        first_payload,
        two_way_info,
        {"id": user_id, "password": password, "email": email_input},
        purpose="signup_info",
        signup_timing=signup_timing,
    )


def _attempt_signup_info_auto_retry(
    entry: dict[str, Any],
    flow_id: str,
    result_code: str,
    extra_info: dict[str, Any],
) -> bool:
    """ID·비밀번호 오류 시 자동 재생성 후 signup_info 재제출."""
    if not is_register_signup_auto_retry_code(result_code):
        return False
    if is_register_signup_email_manual_code(result_code):
        return False

    max_retries = _signup_auto_retry_max()
    retry_count = int(entry.get("credit4u_auto_retry_count") or 0)
    if retry_count >= max_retries:
        return False

    customer = entry.get("customer") or {}
    creds = dict(entry.get("credit4u_credentials") or {})
    prev_id = str(creds.get("id") or "").strip()
    attempt = int(entry.get("credit4u_id_attempt_no") or 0) + 1

    entry["credit4u_auto_retry_count"] = retry_count + 1
    entry["last_register_extra_code"] = str(
        extra_info.get("code") or result_code or ""
    ).strip()
    entry["auto_retry_reason"] = signup_auto_retry_reason_label(result_code)
    entry["insurance_status"] = "in_progress"
    entry["credit4u_current_flow"] = "register"
    entry["insurance_stage"] = "register_signup_auto_retrying"
    entry["credit4u_current_request_status"] = "register_signup_auto_retrying"
    entry["insurance_message"] = (
        "신용정보원 아이디를 다시 생성해 회원가입 정보를 재제출하고 있습니다."
    )

    try:
        renewed = regenerate_credit4u_credentials(
            customer, attempt, previous_id=prev_id or None
        )
    except (Credit4uConfigError, ValueError):
        return False

    entry["credit4u_id_attempt_no"] = attempt
    entry["credit4u_credentials"] = {
        **creds,
        "id": renewed["id"],
        "password": renewed["password"],
        "generated": True,
        "source": "generated",
    }

    try:
        api_result = _post_register_signup_info_sync(entry, flow_id)
    except (CodefClientError, ValueError):
        return False

    _apply_credit4u_register_followup_response(entry, api_result, flow_id=flow_id)
    return True


def _on_register_completed(flow_id: str, entry: dict[str, Any]) -> None:
    """회원가입 완료 → 계정 저장 → contract-info 자동 시작."""
    entry["credit4u_register_completed"] = True
    entry.pop("insurance_error_code", None)
    entry.pop("credit4u_signup_id_retry", None)
    entry.pop("credit4u_signup_required_fields", None)
    if not _flow_has_usable_credit4u_credentials(entry):
        entry["insurance_stage"] = "register_completed"
        entry["credit4u_current_request_status"] = "register_completed"
        entry["insurance_message"] = (
            "회원가입은 완료되었으나 조회 계정 정보가 없습니다. "
            "기존 신용정보원 아이디와 비밀번호를 입력해 주세요."
        )
        entry["insurance_stage"] = "existing_account_required"
        return
    _save_credit4u_credentials_to_store(entry)
    entry["register_completed_auto_contract_started"] = "예"
    entry["insurance_message"] = (
        "신용정보원 회원가입이 완료되었습니다. 보험가입이력을 자동 조회하고 있습니다."
    )
    entry.pop("credit4u_current_flow", None)

    if not flow_id:
        entry["insurance_stage"] = "register_completed"
        entry["credit4u_current_request_status"] = "register_completed"
        entry["insurance_message"] = (
            "회원가입은 완료되었으나 보험가입이력 자동 조회를 시작할 수 없습니다."
        )
        return

    try:
        entry["insurance_contract_auto_started"] = "예"
        _start_credit4u_contract_info_first(flow_id, entry)
        entry["insurance_contract_result_code"] = str(entry.get("credit4u_result_code") or "")
    except (Credit4uConfigError, ValueError) as exc:
        entry["insurance_stage"] = "register_completed"
        entry["credit4u_current_request_status"] = "register_completed"
        entry["insurance_message"] = (
            "신용정보원 회원가입이 완료되었습니다. "
            f"보험가입이력 자동 조회를 시작하지 못했습니다. ({exc})"
        )


def _apply_credit4u_register_followup_response(
    entry: dict[str, Any],
    api_result: dict[str, Any],
    *,
    flow_id: str | None = None,
) -> None:
    """register 2차 이상 응답 처리."""
    result_code = str(api_result.get("result_code") or "")
    result_message = str(api_result.get("result_message") or "")
    entry["credit4u_result_code"] = result_code
    entry["credit4u_result_message"] = result_message
    _store_credit4u_register_debug(entry, api_result)

    if result_code == "CODEF_PASSWORD_ENCRYPTION_ERROR":
        _apply_codef_password_encryption_failure(entry)
        return

    if _handle_credit4u_already_registered(
        entry, result_code, result_message, flow_id=flow_id
    ):
        return

    if api_result.get("status_code") == 0 and result_code == "CLIENT_ERROR":
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if api_result.get("parsed") is None and result_code in ("PARSE_ERROR", "CLIENT_ERROR"):
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    data, extracted, extra_info = _update_credit4u_register_session(entry, api_result)
    error_message = str(extra_info.get("errorMessage") or "").strip()
    if error_message and not result_message:
        result_message = error_message
        entry["credit4u_result_message"] = result_message

    if (
        is_credit4u_register_timeout_retryable(result_code)
        and entry.get("credit4u_current_flow") == "register"
    ):
        _apply_credit4u_register_retryable_timeout(entry, result_code, result_message)
        return

    fid = _resolve_entry_flow_id(entry, flow_id)

    if is_register_signup_auto_retry_code(result_code):
        if fid and _attempt_signup_info_auto_retry(entry, fid, result_code, extra_info):
            return
        retry_count = int(entry.get("credit4u_auto_retry_count") or 0)
        if retry_count >= _signup_auto_retry_max():
            entry["insurance_status"] = "in_progress"
            entry["credit4u_current_flow"] = "register"
            entry["insurance_stage"] = "register_signup_info_required"
            entry["credit4u_current_request_status"] = "register_signup_info_required"
            entry["insurance_message"] = (
                "자동 아이디 재생성으로 해결되지 않았습니다. "
                "다른 아이디를 직접 입력하거나 다시 생성해 주세요."
            )
            entry["insurance_error_code"] = result_code
            return

    if is_register_signup_email_manual_code(result_code) or (
        is_register_signup_retry_code(result_code)
        and not is_register_signup_auto_retry_code(result_code)
    ):
        _apply_register_signup_stage_from_extra(entry, extra_info, result_code)
        entry["insurance_error_code"] = result_code
        return

    if is_register_email_retry_code(result_code):
        entry["insurance_status"] = "in_progress"
        entry["credit4u_current_flow"] = "register"
        entry["insurance_stage"] = "email_required"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if api_result.get("completed") or is_credit4u_register_completed(result_code, data):
        entry["insurance_status"] = "in_progress"
        _on_register_completed(fid, entry)
        return

    if api_result.get("ok"):
        entry["insurance_status"] = "in_progress"
        entry["credit4u_current_flow"] = "register"
        prior_stage = str(entry.get("insurance_stage") or "")
        stage = resolve_register_stage_from_followup(
            result_code, data, extra_info, extracted
        )
        if stage == "register_continue_pending" and extra_info_requests_signup_info(
            extra_info
        ):
            stage = "register_signup_info_required"
        sms_retry = prior_stage in (
            "register_sms_submitting",
            "register_sms_required",
        ) and stage == "register_sms_required"
        entry["insurance_stage"] = stage
        entry["credit4u_current_request_status"] = stage
        if stage == "register_signup_info_required":
            extra_code = str(extra_info.get("code") or result_code or "").strip()
            if (
                fid
                and extra_code
                and is_register_signup_auto_retry_code(extra_code)
                and _attempt_signup_info_auto_retry(entry, fid, extra_code, extra_info)
            ):
                return
            _apply_register_signup_stage_from_extra(entry, extra_info, result_code)
        else:
            entry.pop("credit4u_signup_required_fields", None)
            entry.pop("credit4u_signup_id_retry", None)
            entry["insurance_message"] = register_followup_stage_message(
                stage, sms_retry=sms_retry
            )
        if stage == "register_secure_no_required":
            entry["credit4u_register_req_secure_no"] = register_req_secure_no(
                data, extra_info
            )
        if stage != "register_signup_info_required":
            entry.pop("insurance_error_code", None)
        return

    status_code = int(api_result.get("status_code") or 0)
    if status_code >= 400 or (result_code and result_code not in ("CF-00000",)):
        if (
            is_credit4u_register_timeout_retryable(result_code)
            and entry.get("credit4u_current_flow") == "register"
        ):
            _apply_credit4u_register_retryable_timeout(entry, result_code, result_message)
            return
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["credit4u_current_request_status"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if (
        is_credit4u_register_timeout_retryable(result_code)
        and entry.get("credit4u_current_flow") == "register"
    ):
        _apply_credit4u_register_retryable_timeout(entry, result_code, result_message)
        return

    entry["insurance_status"] = "failed"
    entry["insurance_stage"] = "failed"
    entry["credit4u_current_request_status"] = "failed"
    entry["insurance_message"] = user_message_for_credit4u_failure(
        result_code, result_message
    )
    entry["insurance_error_code"] = result_code or "CREDIT4U_REGISTER_FAILED"


def _apply_credit4u_register_first_response(
    entry: dict[str, Any],
    api_result: dict[str, Any],
) -> None:
    result_code = str(api_result.get("result_code") or "")
    result_message = str(api_result.get("result_message") or "")
    entry["credit4u_result_code"] = result_code
    entry["credit4u_result_message"] = result_message
    _store_credit4u_register_debug(entry, api_result)
    first_payload = api_result.get("first_payload")
    if isinstance(first_payload, dict) and first_payload:
        entry["credit4u_register_first_payload"] = dict(first_payload)

    if result_code == "CODEF_PASSWORD_ENCRYPTION_ERROR":
        _apply_codef_password_encryption_failure(entry)
        return

    if _handle_credit4u_already_registered(entry, result_code, result_message):
        return

    if api_result.get("status_code") == 0 and result_code == "CLIENT_ERROR":
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if api_result.get("parsed") is None and result_code in ("PARSE_ERROR", "CLIENT_ERROR"):
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if api_result.get("ok"):
        data = api_result.get("data") if isinstance(api_result.get("data"), dict) else {}
        extracted = (
            api_result.get("extracted") if isinstance(api_result.get("extracted"), dict) else {}
        )
        extra_info = (
            api_result.get("extra_info") if isinstance(api_result.get("extra_info"), dict) else {}
        )
        _update_credit4u_register_session(
            entry,
            {
                "data": data,
                "extracted": extracted,
                "extra_info": extra_info,
            },
        )
        stage = resolve_register_stage_from_followup(
            result_code, data, extra_info, extracted
        )
        if stage == "register_continue_pending" and extra_info_requests_signup_info(
            extra_info
        ):
            stage = "register_signup_info_required"
        entry["insurance_status"] = "in_progress"
        entry["credit4u_current_flow"] = "register"
        entry["insurance_stage"] = stage
        entry["credit4u_current_request_status"] = stage
        if stage == "register_signup_info_required":
            _apply_register_signup_stage_from_extra(entry, extra_info, result_code)
        else:
            entry.pop("credit4u_signup_required_fields", None)
            entry.pop("credit4u_signup_id_retry", None)
            entry["insurance_message"] = register_followup_stage_message(stage)
        if stage == "register_secure_no_required":
            entry["credit4u_register_req_secure_no"] = register_req_secure_no(
                data, extra_info
            )
        entry.pop("insurance_error_code", None)
        return

    status_code = int(api_result.get("status_code") or 0)
    if status_code >= 400 or (result_code and result_code not in ("CF-00000",)):
        if is_credit4u_register_timeout_retryable(result_code):
            _apply_credit4u_register_retryable_timeout(entry, result_code, result_message)
            return
        if _handle_credit4u_already_registered(entry, result_code, result_message):
            return
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if is_credit4u_register_timeout_retryable(result_code):
        _apply_credit4u_register_retryable_timeout(entry, result_code, result_message)
        return

    if _handle_credit4u_already_registered(entry, result_code, result_message):
        return

    entry["insurance_status"] = "failed"
    entry["insurance_stage"] = "failed"
    entry["insurance_message"] = user_message_for_credit4u_failure(
        result_code, result_message
    )
    entry["insurance_error_code"] = result_code or "CREDIT4U_REGISTER_FAILED"


def _apply_credit4u_register_required(
    entry: dict[str, Any],
    *,
    result_code: str,
    result_message: str,
) -> None:
    """CF-12832 — 저장 계정 우선, 없으면 회원가입 1차 자동 시작."""
    entry["credit4u_result_code"] = result_code
    entry["credit4u_result_message"] = result_message
    entry["credit4u_second_status"] = "idle"
    entry.pop("insurance_error_code", None)
    fid = _resolve_entry_flow_id(entry)
    if fid and _load_stored_credit4u_credentials_into_entry(entry):
        entry["insurance_status"] = "in_progress"
        entry["credit4u_current_flow"] = "contract"
        entry["insurance_message"] = (
            "저장된 신용정보원 계정으로 보험가입이력을 조회합니다."
        )
        try:
            _start_credit4u_contract_info_first(fid, entry)
        except (Credit4uConfigError, ValueError):
            _start_credit4u_register(entry, fid)
        return
    if fid and _flow_has_usable_credit4u_credentials(entry):
        _save_credit4u_credentials_to_store(entry)
        entry["insurance_status"] = "in_progress"
        entry["credit4u_current_flow"] = "contract"
        try:
            _start_credit4u_contract_info_first(fid, entry)
        except (Credit4uConfigError, ValueError):
            _start_credit4u_register(entry, fid)
        return
    _start_credit4u_register(entry, fid)


def _apply_credit4u_existing_account_required(
    entry: dict[str, Any],
    result_code: str,
) -> None:
    entry["insurance_status"] = "in_progress"
    entry["insurance_stage"] = "existing_account_required"
    entry["insurance_message"] = (
        "이미 신용정보원에 가입된 고객입니다. 기존 아이디와 비밀번호를 입력해 주세요."
    )
    entry["insurance_error_code"] = result_code
    entry["credit4u_second_status"] = "idle"


def _should_show_existing_account_section(entry: dict[str, Any]) -> bool:
    if entry.get("insurance_stage") == "existing_account_required":
        return True
    if _debug_panel() and entry.get("debug_show_existing_account"):
        return True
    return False


def _apply_codef_password_encryption_failure(entry: dict[str, Any]) -> None:
    entry["insurance_status"] = "failed"
    entry["insurance_stage"] = "failed"
    entry["insurance_message"] = "CODEF 비밀번호 암호화 설정이 필요합니다."
    entry["insurance_error_code"] = "CODEF_PASSWORD_ENCRYPTION_ERROR"
    entry["credit4u_second_status"] = "failed"


def _apply_credit4u_contract_info_first_response(
    entry: dict[str, Any],
    api_result: dict[str, Any],
) -> None:
    result_code = str(api_result.get("result_code") or "")
    result_message = str(api_result.get("result_message") or "")
    entry["credit4u_result_code"] = result_code
    entry["credit4u_result_message"] = result_message
    _store_credit4u_payload_debug(entry, api_result)

    if result_code == "CODEF_PASSWORD_ENCRYPTION_ERROR":
        _apply_codef_password_encryption_failure(entry)
        return

    if _handle_credit4u_already_registered(entry, result_code, result_message):
        return

    if api_result.get("status_code") == 0 and result_code == "CLIENT_ERROR":
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if api_result.get("parsed") is None and result_code in ("PARSE_ERROR", "CLIENT_ERROR"):
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if api_result.get("secure_no_required"):
        data = api_result.get("data") or {}
        extracted = api_result.get("extracted") or {}
        two_way = extracted.get("twoWayInfo")
        if not two_way:
            two_way = {
                "jobIndex": data.get("jobIndex"),
                "threadIndex": data.get("threadIndex"),
                "jti": data.get("jti"),
                "twoWayTimestamp": data.get("twoWayTimestamp"),
            }
        if isinstance(two_way, dict):
            two_way = sanitize_credit4u_two_way_info(two_way)
        entry["insurance_status"] = "in_progress"
        entry["insurance_stage"] = "secure_no_required"
        entry["insurance_message"] = "보안문자 입력이 필요합니다."
        entry["credit4u_req_secure_no"] = data.get("reqSecureNo")
        entry["credit4u_two_way_info"] = two_way
        entry["credit4u_continue2_way"] = bool(extracted.get("continue2Way"))
        entry["credit4u_method"] = str(extracted.get("method") or data.get("method") or "")
        entry.pop("insurance_error_code", None)
        return

    if is_credit4u_register_required(result_code):
        if entry.get("credit4u_register_completed"):
            fid = _resolve_entry_flow_id(entry)
            post_retry = int(entry.get("credit4u_contract_post_register_retry_count") or 0)
            if fid and post_retry < 2:
                entry["credit4u_contract_post_register_retry_count"] = post_retry + 1
                entry["insurance_status"] = "in_progress"
                entry["insurance_stage"] = "register_completed_but_contract_retry_needed"
                entry["credit4u_current_request_status"] = (
                    "register_completed_but_contract_retry_needed"
                )
                entry["insurance_message"] = (
                    "회원가입 직후 조회가 지연되고 있습니다. 잠시 후 자동 재조회합니다."
                )
                time.sleep(2)
                try:
                    _start_credit4u_contract_info_first(fid, entry)
                    entry["insurance_contract_result_code"] = str(
                        entry.get("credit4u_result_code") or ""
                    )
                except (Credit4uConfigError, ValueError):
                    entry["insurance_stage"] = "register_completed"
                    entry["insurance_message"] = (
                        "회원가입은 완료되었으나 보험가입이력 재조회에 실패했습니다."
                    )
                return
        _apply_credit4u_register_required(
            entry,
            result_code=result_code,
            result_message=result_message,
        )
        return

    if result_code == "CF-12822":
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_error_code"] = "CF-12822"
        entry["insurance_message"] = (
            "보험가입이력 조회 요청값이 부족합니다. 개발자 확인이 필요합니다."
        )
        return

    if result_code == "CF-13342":
        entry["insurance_status"] = "in_progress"
        entry["insurance_stage"] = "email_required"
        entry["insurance_message"] = "이메일 확인이 필요합니다."
        return

    if is_credit4u_existing_account_required(result_code):
        _apply_credit4u_existing_account_required(entry, result_code)
        return

    status_code = int(api_result.get("status_code") or 0)
    data = api_result.get("data") if isinstance(api_result.get("data"), dict) else {}
    if result_code == "CF-00000" or extract_credit4u_insurance_records(data):
        _apply_credit4u_insurance_completed(entry, data, flow_id=_resolve_entry_flow_id(entry))
        return

    if status_code >= 400 or (result_code and result_code not in ("CF-00000",)):
        if _handle_credit4u_already_registered(entry, result_code, result_message):
            return
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        return

    if _handle_credit4u_already_registered(entry, result_code, result_message):
        return

    entry["insurance_status"] = "in_progress"
    entry["insurance_stage"] = "credentials_ready"
    entry["insurance_message"] = result_message or "신용정보원 조회 응답을 확인했습니다."


def _start_credit4u_contract_info_first(flow_id: str, entry: dict[str, Any]) -> None:
    """준비된 credit4u_credentials로 contract-info 1차 요청."""
    customer = entry.get("customer") or {}
    preserve_flow = _flow_has_usable_credit4u_credentials(entry)
    block_generate = _is_credit4u_generation_blocked(entry)
    _ensure_credit4u_credentials_for_entry(
        entry,
        allow_generate=not (preserve_flow or block_generate),
    )
    credentials = entry.get("credit4u_credentials")
    if not isinstance(credentials, dict):
        raise ValueError("credit4u credentials missing")
    if not str(credentials.get("id") or "").strip() or not str(credentials.get("password") or "").strip():
        raise ValueError("credit4u credentials incomplete")

    entry["insurance_status"] = "in_progress"
    entry["insurance_stage"] = "requesting_contract_info"
    entry["insurance_message"] = (
        "신용정보원 회원가입이 완료되었습니다. 보험가입이력을 자동 조회하고 있습니다."
        if entry.get("credit4u_register_completed")
        else "신용정보원 보험가입이력을 조회하는 중입니다. 잠시만 기다려 주세요."
    )
    entry.pop("insurance_error", None)
    entry.pop("insurance_error_code", None)

    api_result = post_credit4u_contract_info_first(flow_id, customer, credentials)
    _apply_credit4u_contract_info_first_response(entry, api_result)


def _request_insurance_history_start(flow_id: str, entry: dict[str, Any]) -> None:
    """신용정보원 contract-info 1차: 고객등록 시 생성된 계정으로 CODEF 요청."""
    _ensure_credit4u_credentials_for_entry(entry)
    creds = entry.get("credit4u_credentials")
    if not isinstance(creds, dict) or not str(creds.get("id") or "").strip():
        _provision_credit4u_credentials(entry)
        creds = entry.get("credit4u_credentials")
    if not isinstance(creds, dict) or not str(creds.get("id") or "").strip():
        if entry.get("credit4u_credentials_error"):
            raise Credit4uConfigError("REDRIBBON_CREDIT4U_SECRET is not configured")
        raise ValueError("credit4u credentials missing")
    _start_credit4u_contract_info_first(flow_id, entry)


def _apply_credit4u_insurance_completed(
    entry: dict[str, Any],
    data: dict[str, Any],
    *,
    flow_id: str | None = None,
) -> None:
    customer = entry.get("customer") or {}
    customer_name = str(customer.get("name") or "")
    raw_records = extract_credit4u_insurance_records(data)
    normalized, company_groups = build_insurance_company_groups(raw_records, customer_name)
    entry["insurance_status"] = "completed"
    entry["insurance_stage"] = "completed"
    entry["insurance_summary"] = insurance_summary_from_records(normalized)
    entry["insurance_records"] = normalized
    entry["insurance_company_groups"] = company_groups
    entry["insurance_message"] = "보험가입이력 수신 완료"
    entry["credit4u_second_status"] = "completed"
    entry.pop("insurance_error_code", None)
    fid = _resolve_entry_flow_id(entry, flow_id)
    if fid:
        _persist_insurance_records_to_sqlite(fid, entry, data)
    if _flow_has_usable_credit4u_credentials(entry):
        _save_credit4u_credentials_to_store(entry)


def _apply_credit4u_contract_info_second_response(
    entry: dict[str, Any],
    api_result: dict[str, Any],
) -> None:
    result_code = str(api_result.get("result_code") or "")
    result_message = str(api_result.get("result_message") or "")
    data = api_result.get("data") if isinstance(api_result.get("data"), dict) else {}
    entry["credit4u_result_code"] = result_code
    entry["credit4u_result_message"] = result_message
    entry["credit4u_data_keys"] = sorted(str(k) for k in data.keys())
    _store_credit4u_payload_debug(entry, api_result)

    if result_code == "CODEF_PASSWORD_ENCRYPTION_ERROR":
        _apply_codef_password_encryption_failure(entry)
        return

    if _handle_credit4u_already_registered(entry, result_code, result_message):
        return

    if api_result.get("status_code") == 0 and result_code == "CLIENT_ERROR":
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = result_message or "보험가입이력 조회에 실패했습니다."
        entry["insurance_error_code"] = result_code
        entry["credit4u_second_status"] = "failed"
        return

    if api_result.get("parsed") is None and result_code in ("PARSE_ERROR", "CLIENT_ERROR"):
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            result_code, result_message
        )
        entry["insurance_error_code"] = result_code
        entry["credit4u_second_status"] = "failed"
        return

    if is_credit4u_register_required(result_code):
        _apply_credit4u_register_required(
            entry,
            result_code=result_code,
            result_message=result_message,
        )
        return

    if result_code == "CF-12822":
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_error_code"] = "CF-12822"
        entry["insurance_message"] = (
            "보험가입이력 조회 요청값이 부족합니다. 개발자 확인이 필요합니다."
        )
        entry["credit4u_second_status"] = "failed"
        return

    if result_code == "CF-13342":
        entry["insurance_status"] = "in_progress"
        entry["insurance_stage"] = "email_required"
        entry["insurance_message"] = "이메일 확인이 필요합니다."
        entry["credit4u_second_status"] = "idle"
        return

    if is_credit4u_existing_account_required(result_code):
        _apply_credit4u_existing_account_required(entry, result_code)
        return

    if api_result.get("ok"):
        _apply_credit4u_insurance_completed(
            entry, data, flow_id=_resolve_entry_flow_id(entry)
        )
        return

    if _handle_credit4u_already_registered(entry, result_code, result_message):
        return

    entry["insurance_status"] = "failed"
    entry["insurance_stage"] = "failed"
    entry["insurance_message"] = user_message_for_credit4u_failure(
        result_code, result_message
    )
    entry["insurance_error_code"] = result_code or "CREDIT4U_FAILED"
    entry["credit4u_second_status"] = "failed"


def _apply_insurance_sample_complete(entry: dict[str, Any]) -> None:
    customer = entry.get("customer") or {}
    customer_name = str(customer.get("name") or "")
    raw_records = [dict(r) for r in _INSURANCE_SAMPLE_RECORDS]
    normalized, company_groups = build_insurance_company_groups(raw_records, customer_name)
    entry["insurance_status"] = "completed"
    entry["insurance_summary"] = insurance_summary_from_records(normalized)
    entry["insurance_records"] = normalized
    entry["insurance_company_groups"] = company_groups
    entry.pop("insurance_message", None)
    entry.pop("insurance_error", None)


def _clear_insurance_temp_fields(entry: dict[str, Any]) -> None:
    for key in (
        "insurance_message",
        "insurance_error",
        "insurance_error_code",
        "insurance_stage",
        "insurance_summary",
        "insurance_records",
        "insurance_company_groups",
        "credit4u_req_secure_no",
        "credit4u_two_way_info",
        "credit4u_secure_no_input",
        "credit4u_result_code",
        "credit4u_result_message",
        "credit4u_continue2_way",
        "credit4u_method",
        "credit4u_second_status",
        "credit4u_data_keys",
        "credit4u_current_flow",
        "credit4u_register_debug",
        "credit4u_register_two_way_info",
        "credit4u_register_extra_info",
        "credit4u_register_req_secure_no",
        "credit4u_register_secure_no_input",
        "credit4u_register_first_payload",
        "credit4u_register_completed",
        "credit4u_current_request_status",
        "credit4u_register_sms_auth_no_input",
        "credit4u_register_email",
        "credit4u_register_email_auth_no_input",
    ):
        entry.pop(key, None)
    entry["credit4u_second_status"] = "idle"


def _customer_has_stored_insurance_records(customer: dict[str, Any]) -> bool:
    if not is_search_hash_secret_configured():
        return False
    saved = load_latest_insurance_records(customer)
    if not saved:
        return False
    payload = saved.get("normalized_payload") or saved.get("normalized_records")
    return bool(flatten_imported_insurance_records(payload))


def _apply_saved_insurance_records_to_entry(
    entry: dict[str, Any],
    saved: dict[str, Any],
) -> bool:
    """저장된 insurance_records를 FLOW_STORE completed 상태로 반영."""
    customer = entry.get("customer") or {}
    customer_name = str(customer.get("name") or "")
    normalized_payload = saved.get("normalized_payload") or saved.get("normalized_records")
    display = resolve_stored_insurance_for_display(
        normalized_payload,
        saved.get("summary"),
        customer_name,
        raw_response=saved.get("raw_response"),
    )
    insured_summary = display.get("insured_summary")
    if not isinstance(insured_summary, dict) or not insured_summary.get("company_groups"):
        if not display.get("flat_record_count"):
            return False

    entry["insurance_status"] = "completed"
    entry["insurance_stage"] = "completed"
    entry["insurance_source"] = "saved_imported"
    entry["insurance_message"] = "저장된 보험가입이력을 불러왔습니다."
    entry["insurance_records_saved"] = True
    entry["stored_insurance_records_exists"] = True
    entry["loaded_insurance_record_id"] = saved.get("record_id")
    entry["loaded_insurance_record_source"] = str(saved.get("source") or "")
    entry["loaded_insurance_record_flow_id"] = str(saved.get("flow_id") or "")
    entry["insurance_result_raw"] = saved.get("raw_response")
    entry["insurance_result"] = normalized_payload
    entry["insurance_records"] = display.get("insurance_records") or []
    entry["insured_summary"] = insured_summary
    entry["insurance_company_groups"] = display.get("insurance_company_groups") or []
    entry["insurance_summary"] = display.get("insurance_summary") or {}
    entry["insurance_summary_debug"] = (insured_summary or {}).get("debug") or {}
    entry["credit4u_second_status"] = "completed"
    entry.pop("insurance_error_code", None)
    return True


def _restore_saved_insurance_records_if_needed(entry: dict[str, Any]) -> bool:
    """SQLite에 저장된 보험 원부를 FLOW_STORE에 복원(CODEF 미호출)."""
    if entry.get("insurance_source") == "saved_imported" and entry.get("insurance_records"):
        return True
    if entry.get("insurance_status") == "completed" and entry.get("insurance_records"):
        return True
    active_register_stages = {
        "register_requesting",
        "register_secure_no_required",
        "register_sms_required",
        "register_signup_info_required",
        "register_signup_info_submitting",
        "register_signup_auto_retrying",
        "register_email_auth_required",
    }
    if str(entry.get("insurance_stage") or "") in active_register_stages:
        return False
    customer = entry.get("customer") or {}
    if not is_search_hash_secret_configured():
        return False
    saved = load_latest_insurance_records(customer)
    if not saved:
        return False
    return _apply_saved_insurance_records_to_entry(entry, saved)


def _insurance_request_context(entry: dict[str, Any], fid: str) -> dict[str, Any]:
    restored_saved = _restore_saved_insurance_records_if_needed(entry)
    if not restored_saved:
        _ensure_credit4u_credentials_for_entry(
            entry,
            allow_generate=not _is_credit4u_generation_blocked(entry),
        )
    status = entry.get("insurance_status") or "pending"
    customer = entry.get("customer") or {}
    summary = entry.get("insurance_summary") or {
        "total": 0,
        "insured_valid": 0,
        "company_count": 0,
        "coverage_count": 0,
    }
    records: list[dict[str, Any]] = []
    company_groups: list[dict[str, Any]] = []
    insured_summary: dict[str, Any] = {}
    insurance_summary_debug: dict[str, Any] = {}
    if status == "completed":
        customer_name = str((customer.get("name") or ""))
        insured_summary = entry.get("insured_summary")
        if not isinstance(insured_summary, dict) or not insured_summary.get("company_groups"):
            saved = (
                load_latest_insurance_records(customer)
                if is_search_hash_secret_configured()
                else None
            )
            if saved:
                display = resolve_stored_insurance_for_display(
                    saved.get("normalized_payload") or saved.get("normalized_records"),
                    saved.get("summary"),
                    customer_name,
                    raw_response=saved.get("raw_response"),
                )
                insured_summary = display.get("insured_summary") or {}
                summary = display.get("insurance_summary") or summary
                insurance_summary_debug = (insured_summary or {}).get("debug") or {}
        else:
            insurance_summary_debug = entry.get("insurance_summary_debug") or (
                insured_summary.get("debug") or {}
            )
            inner = insured_summary.get("counts")
            if isinstance(inner, dict):
                summary = {
                    "total": int(inner.get("product_count") or summary.get("total") or 0),
                    "insured_valid": int(
                        inner.get("active_product_count") or summary.get("insured_valid") or 0
                    ),
                    "company_count": int(
                        inner.get("company_count") or summary.get("company_count") or 0
                    ),
                    "coverage_count": int(
                        inner.get("coverage_count") or summary.get("coverage_count") or 0
                    ),
                }
        company_groups = insured_summary.get("company_groups") or []
    user_message = entry.get("insurance_message") or entry.get("insurance_error")
    credit4u_debug: dict[str, Any] | None = None
    insurance_stage = str(entry.get("insurance_stage") or "")
    if insurance_stage.startswith("register_") and entry.get("credit4u_register_req_secure_no"):
        secure_no_view = _credit4u_secure_no_view(entry.get("credit4u_register_req_secure_no"))
    else:
        secure_no_view = _credit4u_secure_no_view(entry.get("credit4u_req_secure_no"))
    creds = entry.get("credit4u_credentials") or {}
    credential_source = str(
        entry.get("credential_source")
        or creds.get("source")
        or ("generated" if creds.get("generated") else "—")
    )
    credential_version = str(
        entry.get("credential_version")
        or creds.get("credential_version")
        or CREDIT4U_CREDENTIAL_VERSION
    )
    if _debug_panel():
        payload_debug = entry.get("credit4u_payload_debug") or {}
        register_debug = entry.get("credit4u_register_debug") or {}
        credit4u_debug = {
            "credit4u_endpoint": payload_debug.get("credit4u_endpoint") or "—",
            "credit4u_payload_keys": payload_debug.get("credit4u_payload_keys") or "—",
            "organization_in_payload": payload_debug.get("organization_in_payload"),
            "organization_source": payload_debug.get("organization_source") or "—",
            "credit4u_result_code": entry.get("credit4u_result_code") or "—",
            "credit4u_result_message": entry.get("credit4u_result_message") or "—",
            "credential_source": str(
                entry.get("credential_source") or credential_source
            ),
            "credit4u_current_flow": entry.get("credit4u_current_flow") or "—",
            "insurance_stage": entry.get("insurance_stage") or "—",
            "current_request_status": entry.get("insurance_stage") or "—",
            "continue2_way": entry.get("credit4u_continue2_way"),
            "method": entry.get("credit4u_method") or "—",
            "req_secure_no_present": bool(entry.get("credit4u_req_secure_no")),
            "two_way_info_saved": bool(entry.get("credit4u_two_way_info")),
            "secure_no_input": bool(entry.get("credit4u_secure_no_input")),
            "data_keys": ", ".join(entry.get("credit4u_data_keys") or []) or "—",
            "register_endpoint": register_debug.get("register_endpoint") or "—",
            "register_payload_keys": register_debug.get("register_payload_keys") or "—",
            "register_extra_info_keys": register_debug.get("register_extra_info_keys") or "—",
            "register_extra_code": (
                register_debug.get("register_extra_code")
                or entry.get("credit4u_register_extra_code")
                or "—"
            ),
            "register_extra_message": (
                register_debug.get("register_extra_message")
                or entry.get("credit4u_register_extra_message")
                or "—"
            ),
            "register_error_message": (
                register_debug.get("register_error_message")
                or entry.get("credit4u_register_error_message")
                or "—"
            ),
            "credit4u_id_attempt_no": entry.get("credit4u_id_attempt_no", 0),
            "register_two_way_info_saved": bool(entry.get("credit4u_register_two_way_info")),
            "checkParamUUID_present": register_debug.get("checkParamUUID_present")
            or (
                "예"
                if len(str(entry.get("credit4u_check_param_uuid") or "")) == 20
                else "아니오"
            ),
            "checkParamUUID_length": register_debug.get("checkParamUUID_length")
            or len(str(entry.get("credit4u_check_param_uuid") or "")),
            "register_payload_purpose": (
                register_debug.get("register_payload_purpose")
                or entry.get("credit4u_register_payload_purpose")
                or "—"
            ),
            "payload_has_checkParamUUID": register_debug.get("payload_has_checkParamUUID")
            or "—",
            "payload_has_id": register_debug.get("payload_has_id") or "—",
            "payload_has_password": register_debug.get("payload_has_password") or "—",
            "payload_has_email": register_debug.get("payload_has_email") or "—",
            "payload_has_secureNo": register_debug.get("payload_has_secureNo") or "—",
            "payload_has_secureNoRefresh": register_debug.get("payload_has_secureNoRefresh")
            or "—",
            "payload_has_smsAuthNo": register_debug.get("payload_has_smsAuthNo") or "—",
            "payload_has_emailAuthNo": register_debug.get("payload_has_emailAuthNo") or "—",
            "password_encrypted": register_debug.get("password_encrypted") or "—",
            "current_request_status": (
                entry.get("credit4u_current_request_status")
                or entry.get("insurance_stage")
                or "—"
            ),
            "last_register_stage": entry.get("credit4u_last_register_stage") or "—",
            "email_domain_allowed": (
                "예"
                if is_credit4u_email_domain_allowed(
                    str(entry.get("credit4u_register_email") or entry.get("credit4u_signup_email") or "")
                )
                else "아니오"
            ),
            **credit4u_credentials_debug(
                str(creds.get("id") or ""),
                password=str(creds.get("password") or ""),
                credential_source=credential_source,
            ),
            "credit4u_signup_required_fields": ", ".join(
                entry.get("credit4u_signup_required_fields") or []
            )
            or "—",
            "signup_email_domain": mask_email_for_debug(
                str(
                    entry.get("credit4u_signup_email")
                    or entry.get("credit4u_register_email")
                    or ""
                )
            ),
            **codef_password_encryption_debug(),
            **(entry.get("credit4u_register_signup_timing") or {}),
            "register_completed_auto_contract_started": (
                "예" if entry.get("register_completed_auto_contract_started") else "아니오"
            ),
            "credit4u_credentials_saved": (
                "예" if entry.get("credit4u_credentials_saved") else "아니오"
            ),
            "stored_credit4u_credentials_exists": (
                "예"
                if entry.get("stored_credit4u_credentials_exists")
                or (
                    is_search_hash_secret_configured()
                    and has_stored_credit4u_credentials(customer)
                )
                else "아니오"
            ),
            "credit4u_auto_retry_count": entry.get("credit4u_auto_retry_count", 0),
            "last_register_extra_code": entry.get("last_register_extra_code") or "—",
            "auto_retry_reason": entry.get("auto_retry_reason") or "—",
            "insurance_contract_auto_started": (
                "예" if entry.get("insurance_contract_auto_started") else "아니오"
            ),
            "insurance_contract_result_code": (
                entry.get("insurance_contract_result_code")
                or entry.get("credit4u_result_code")
                or "—"
            ),
            "insurance_records_saved": (
                "예" if entry.get("insurance_records_saved") else "아니오"
            ),
            "credential_version": credential_version,
            "already_registered_handled": (
                "예" if entry.get("already_registered_handled") else "아니오"
            ),
            "already_registered_auto_contract_started": (
                "예" if entry.get("already_registered_auto_contract_started") else "아니오"
            ),
            "credential_save_error": entry.get("credential_save_error") or "—",
            "credential_loaded_from_store": (
                "예" if entry.get("credential_loaded_from_store") else "아니오"
            ),
            "credential_generated_new": (
                "예" if entry.get("credential_generated_new") else "아니오"
            ),
            "stored_insurance_records_exists": (
                "예" if entry.get("stored_insurance_records_exists") else "아니오"
            ),
            "loaded_insurance_record_id": entry.get("loaded_insurance_record_id") or "—",
            "loaded_insurance_record_source": (
                entry.get("loaded_insurance_record_source") or "—"
            ),
            "insurance_source": str(entry.get("insurance_source") or "—"),
            "loaded_insurance_raw_len": entry.get("loaded_insurance_raw_len") or "—",
            "loaded_insurance_normalized_len": (
                entry.get("loaded_insurance_normalized_len") or "—"
            ),
            "loaded_insurance_summary_len": (
                entry.get("loaded_insurance_summary_len") or "—"
            ),
        }
    if _debug_panel() and entry.get("stored_insurance_records_exists"):
        saved_dbg = load_latest_insurance_records(customer) if customer else None
        if saved_dbg:
            credit4u_debug = credit4u_debug or {}
            credit4u_debug.update(
                {
                    "loaded_insurance_raw_len": saved_dbg.get("raw_len", "—"),
                    "loaded_insurance_normalized_len": saved_dbg.get("normalized_len", "—"),
                    "loaded_insurance_summary_len": saved_dbg.get("summary_len", "—"),
                }
            )
            entry["loaded_insurance_raw_len"] = saved_dbg.get("raw_len")
            entry["loaded_insurance_normalized_len"] = saved_dbg.get("normalized_len")
            entry["loaded_insurance_summary_len"] = saved_dbg.get("summary_len")
    show_existing_account_section = _should_show_existing_account_section(entry)
    insurance_stage_value = str(entry.get("insurance_stage") or "")
    show_credentials_card = bool(
        _credit4u_credentials_view_context(entry).get("show_credit4u_credentials_card")
        and insurance_stage_value != "existing_account_required"
    )
    creds_view = _credit4u_credentials_view_context(entry)
    if not show_credentials_card:
        creds_view = {
            **creds_view,
            "show_credit4u_credentials_card": False,
            "credit4u_id": "",
            "credit4u_password": "",
            "credit4u_password_available": False,
        }
    return {
        "current_step": 4,
        "flow_id": fid,
        "debug_panel": _debug_panel(),
        "demo_complete_allowed": _demo_complete_allowed(),
        "customer_display": _build_customer_display(customer),
        "insurance_status": status,
        "insurance_stage": entry.get("insurance_stage"),
        "insurance_message": user_message,
        "insurance_error_code": entry.get("insurance_error_code"),
        "insurance_summary": summary,
        "insured_summary": insured_summary,
        "insurance_summary_debug": insurance_summary_debug,
        "insurance_records": records,
        "insurance_company_groups": company_groups,
        "show_existing_account_section": show_existing_account_section,
        "show_credit4u_credentials_card": show_credentials_card,
        "insurance_using_saved_import": entry.get("insurance_source") == "saved_imported",
        "insurance_source_label": (
            "저장된 보험가입이력 기준입니다."
            if entry.get("insurance_source") == "saved_imported"
            else ""
        ),
        "hide_insurance_codf_start": entry.get("insurance_source") == "saved_imported",
        "credit4u_debug": credit4u_debug,
        "credit4u_secure_no": secure_no_view,
        **creds_view,
        "credit4u_register_email": str(
            entry.get("credit4u_signup_email")
            or entry.get("credit4u_register_email")
            or ""
        ),
        "credit4u_allowed_email_domains": allowed_credit4u_email_domains_display(),
        "credit4u_signup_id_retry": bool(entry.get("credit4u_signup_id_retry")),
        "credit4u_signup_required_fields": list(
            entry.get("credit4u_signup_required_fields") or []
        ),
        "credit4u_register_extra_reason": register_extra_reason_for_display(
            entry.get("credit4u_register_extra_info")
            if isinstance(entry.get("credit4u_register_extra_info"), dict)
            else {}
        )
        or str(entry.get("credit4u_register_extra_message") or "")
        or str(entry.get("credit4u_register_error_message") or ""),
    }


@app.get("/", response_class=HTMLResponse)
def intro(request: Request):
    """래드리본 인트로 + 서비스 선택."""
    return templates.TemplateResponse(
        request,
        "intro.html",
        {
            "customer_url": "/customer/start",
            "hospital_url": "/hospital/start",
            "operator_url": "/operator",
            **_printer_download_template_context(),
        },
    )


@app.get("/customer/start", response_class=HTMLResponse)
def customer_start_stub(request: Request):
    """고객용 진입 스텁(준비 중)."""
    return templates.TemplateResponse(
        request,
        "flow_stub.html",
        {
            "title": "고객용 서비스",
            "lead": "고객용 서비스는 준비 중입니다. 통합 앱에서는 고객 흐름 화면으로 연결됩니다.",
            "back_url": "/",
        },
    )


@app.get("/hospital/start", response_class=HTMLResponse)
def hospital_flow_start(request: Request):
    """병원용 1단계 시작 화면."""
    return templates.TemplateResponse(
        request,
        "hospital_start.html",
        {
            "current_step": 1,
            "flow_id": None,
            "hospital_flow_first_url": "/hospital/customer",
            "debug_panel": False,
            **_printer_download_template_context(),
        },
    )


@app.post("/api/print-receiver/upload")
async def api_print_receiver_upload(
    file: UploadFile = File(...),
    hospital_name: str = Form(""),
    printer_name: str = Form(""),
    customer_key: str = Form(""),
    linked_customer_name: str = Form(""),
):
    """Print Receiver PDF 업로드 — operator_received_documents 저장."""
    filename = str(file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        return JSONResponse(
            {"ok": False, "error": "pdf_required"},
            status_code=400,
        )
    content = await file.read()
    if not content:
        return JSONResponse(
            {"ok": False, "error": "empty_file"},
            status_code=400,
        )
    digest = hashlib.sha256(content).hexdigest()
    day_dir = PRINT_UPLOAD_ROOT / datetime.now(timezone.utc).strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.\-]+", "_", filename)[:120] or "document.pdf"
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    stored_path = day_dir / stored_name
    stored_path.write_bytes(content)

    received_at = print_receiver_received_at_utc()
    if not received_at.strip():
        received_at = print_receiver_received_at_utc()
    reg = register_print_receiver_upload(
        stored_path=str(stored_path.resolve()),
        original_filename=filename,
        file_sha256=digest,
        received_at=received_at,
        hospital_name=(hospital_name or "").strip(),
        printer_name=(printer_name or "").strip(),
        customer_key=(customer_key or "").strip() or None,
        linked_customer_name=(linked_customer_name or "").strip(),
    )
    if not reg.get("duplicate"):
        from services.received_pdf_match import apply_received_pdf_ocr_and_match

        apply_received_pdf_ocr_and_match(
            int(reg.get("document_id") or 0),
            pdf_path=stored_path,
            filename=filename,
            hospital_name=(hospital_name or "").strip(),
            document_type_candidate="",
            flow_store=FLOW_STORE,
            extra_metadata={
                "source": "print_receiver",
                "hospital_name": (hospital_name or "").strip(),
                "printer_name": (printer_name or "").strip(),
                "original_filename": filename,
                "sha256": digest,
            },
        )
    doc = reg.get("document") or {}
    if not reg.get("duplicate"):
        refreshed = get_received_document_by_id(int(reg.get("document_id") or 0))
        if refreshed:
            doc = refreshed
    return JSONResponse(
        {
            "ok": True,
            "duplicate": bool(reg.get("duplicate")),
            "document_id": reg.get("document_id"),
            "sha256": digest,
            "received_at": received_at,
            "file_url": doc.get("file_url"),
            "message": (
                "duplicate_skipped"
                if reg.get("duplicate")
                else "uploaded"
            ),
        }
    )


@app.get("/hospital/customer", response_class=HTMLResponse)
def hospital_customer_get(request: Request, flow_id: str | None = None):
    """병원용 2단계 고객등록 화면."""
    fid = _canonical_flow_id(flow_id)
    return templates.TemplateResponse(
        request,
        "customer.html",
        {
            "current_step": 2,
            "flow_id": fid,
            "debug_panel": False,
            "form_error": None,
        },
    )


@app.post("/hospital/customer")
def hospital_customer_post(
    request: Request,
    name: str = Form(""),
    identity: str = Form(""),
    phone: str = Form(""),
    telecom: str = Form(""),
    email: str = Form(""),
    auth_method: str = Form(""),
):
    """고객 등록 후 신규 flow만 생성(클라이언트 flow_id 재사용·기존 자동 연결 없음)."""
    err = _customer_form_error(name, identity, phone, telecom, email, auth_method)
    if err:
        return templates.TemplateResponse(
            request,
            "customer.html",
            {
                "current_step": 2,
                "flow_id": None,
                "debug_panel": False,
                "form_error": err,
            },
        )

    new_id = str(uuid.uuid4())
    while new_id in FLOW_STORE:
        new_id = str(uuid.uuid4())

    identity_digits = re.sub(r"\D", "", identity or "")[:13]
    phone_digits = re.sub(r"\D", "", phone or "")
    FLOW_STORE[new_id] = {
        "customer": {
            "name": (name or "").strip(),
            "identity": identity_digits,
            "phone": phone_digits,
            "telecom": (telecom or "").strip(),
            "email": (email or "").strip(),
            "auth_method": "kakao",
        },
        "created_in_final": True,
        "medical_status": "pending",
        "insurance_status": "pending",
        "second_status": "idle",
        "credit4u_second_status": "idle",
    }
    _register_customer_persistence(new_id, FLOW_STORE[new_id])
    _provision_credit4u_credentials(FLOW_STORE[new_id])

    return RedirectResponse(
        url=f"/hospital/hira-start?flow_id={new_id}",
        status_code=303,
    )


@app.get("/hospital/hira-start", response_class=HTMLResponse)
def hospital_hira_start(request: Request, flow_id: str | None = None):
    """병원용 3단계 진료내역 가져오기."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    status = entry.get("medical_status") or "pending"
    customer = entry.get("customer") or {}
    customer_display = _build_customer_display(customer)
    counts = entry.get("medical_result_counts") or {"basic": 0, "detail": 0, "prescribe": 0}
    basic_all: list[dict[str, str]] = []
    detail_all: list[dict[str, str]] = []
    prescribe_all: list[dict[str, str]] = []
    medical_view: dict[str, Any] = {}
    if status == "completed":
        _ensure_medical_records_loaded(fid, entry)
        medical_view = _medical_records_view_context(entry)
        basic_all = medical_view["medical_records_basic_all"]
        detail_all = medical_view["medical_records_detail_all"]
        prescribe_all = medical_view["medical_records_prescribe_all"]
        counts = medical_view["medical_result_counts"]
    show_hira_modal, hira_modal_step = _hira_modal_context(entry)
    customer_norm = _normalize_stored_customer(customer) if customer else {}
    if customer_norm:
        entry["customer"] = customer_norm
    saved_medical: dict[str, Any] | None = None
    if is_search_hash_secret_configured() and customer_norm:
        saved_medical = load_latest_medical_records(customer_norm)
    has_saved_medical_records = bool(saved_medical)
    entry["has_saved_medical_records"] = has_saved_medical_records
    hira_storage_debug = None
    if _debug_panel():
        hira_storage_debug = _hira_storage_debug_context(customer_norm, saved_medical)

    return templates.TemplateResponse(
        request,
        "hira_start.html",
        {
            "current_step": 3,
            "flow_id": fid,
            "debug_panel": _debug_panel(),
            "customer_display": customer_display,
            "has_saved_medical_records": has_saved_medical_records,
            "hira_storage_debug": hira_storage_debug,
            "medical_status": status,
            "medical_message": entry.get("medical_message"),
            "medical_result_counts": counts,
            "medical_records": basic_all[:10],
            "medical_records_basic_all": basic_all,
            "medical_records_detail_all": detail_all,
            "medical_records_prescribe_all": prescribe_all,
            "codef_debug": _hira_codef_debug_context(entry),
            "show_hira_modal": show_hira_modal,
            "hira_modal_step": hira_modal_step,
            **_credit4u_credentials_view_context(entry),
        },
    )


@app.post("/hospital/hira-auth-request")
def hospital_hira_auth_request(flow_id: str | None = None):
    """진료내역 조회 카카오 인증 요청(DEMO_MODE: 시뮬레이션, 그 외: CODEF 1차)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    status = entry.get("medical_status") or "pending"
    if status not in ("pending", "waiting_auth", "waiting_auth_debug_needed", "failed"):
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    if status != "pending":
        _clear_hira_codef_fields(entry)
        entry["medical_status"] = "pending"

    if DEMO_MODE:
        _request_hira_auth_demo(fid)
        _apply_hira_waiting_auth(
            entry,
            result={"code": "DEMO", "message": "데모 모드"},
            extracted={
                "continue2Way": True,
                "method": "simpleAuth",
                "twoWayInfo": {"jobIndex": 0, "threadIndex": 0, "jti": "demo", "twoWayTimestamp": 0},
                "twoWayInfo_found": True,
                "root_keys": [],
                "data_keys": [],
            },
        )
    else:
        try:
            _request_hira_auth_codef(fid, entry)
        except CodefClientError as exc:
            _apply_hira_auth_failed(
                entry,
                result_code=exc.code or "CLIENT_ERROR",
                result_message=exc.message,
                user_message=exc.message,
            )

    return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)


@app.post("/hospital/hira-complete-auth")
def hospital_hira_complete_auth(flow_id: str | None = None):
    """인증 완료 후 진료내역 수신(DEMO_MODE: 샘플, 그 외: CODEF 2차)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]

    second_status = entry.get("second_status") or "idle"
    if entry.get("medical_status") == "completed" or second_status == "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    if second_status == "in_progress":
        entry["medical_message"] = "진료내역 조회가 이미 진행 중입니다. 잠시 후 다시 확인해 주세요."
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)

    if DEMO_MODE:
        if entry.get("medical_status") != "waiting_auth":
            entry["medical_status"] = "failed"
            entry["medical_message"] = "인증 및 조회 순서가 올바르지 않아 처리할 수 없습니다."
        else:
            _complete_hira_demo(entry)
            _persist_medical_records_to_sqlite(fid, entry)
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)

    medical_status = entry.get("medical_status")
    if medical_status not in ("waiting_auth", "waiting_auth_debug_needed"):
        _apply_hira_second_failed(
            entry,
            result_code="STATE_ERROR",
            result_message="medical_status is not waiting_auth",
            user_message=(
                f"현재 진료내역 조회 상태({medical_status})에서는 "
                "인증 완료 처리를 할 수 없습니다."
            ),
        )
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)

    two_way_info = entry.get("two_way_info")
    if not two_way_info:
        entry["codef_second_result_code"] = "NO_TWO_WAY"
        entry["codef_second_result_message"] = "twoWayInfo missing"
        entry["medical_message"] = (
            "2차 인증 정보(twoWayInfo)가 저장되지 않아 조회를 진행할 수 없습니다. "
            "1차 「카카오 인증 요청」을 다시 시도하거나, DEBUG 정보를 확인해 주세요."
        )
        if medical_status != "waiting_auth_debug_needed":
            entry["medical_status"] = "waiting_auth_debug_needed"
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)

    first_payload = _get_hira_first_payload(fid, entry)
    entry["second_status"] = "in_progress"

    api_result = post_hira_medical_second(first_payload, two_way_info)
    result_code = str(api_result.get("result_code") or "")
    result_message = str(api_result.get("result_message") or "")

    if api_result.get("status_code") == 0 and result_code == "CLIENT_ERROR":
        _apply_hira_second_failed(
            entry,
            result_code=result_code,
            result_message=result_message,
            user_message=result_message or "CODEF 통신 중 오류가 발생했습니다.",
        )
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)

    parsed = api_result.get("parsed")
    if parsed is None:
        _apply_hira_second_failed(
            entry,
            result_code=result_code or "PARSE_ERROR",
            result_message=result_message,
            user_message="CODEF 응답을 해석하지 못했습니다.",
        )
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)

    if not api_result.get("ok"):
        _apply_hira_second_failed(
            entry,
            result_code=result_code,
            result_message=result_message,
            user_message=user_message_for_second_failure(result_code, result_message),
        )
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)

    lists = extract_hira_medical_lists(parsed)
    _apply_hira_second_completed(
        entry,
        lists=lists,
        result_code=result_code,
        result_message=result_message,
    )
    _persist_medical_records_to_sqlite(fid, entry)
    return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)


@app.post("/hospital/hira-use-saved")
def hospital_hira_use_saved(flow_id: str | None = None):
    """저장된 진료내역을 FLOW_STORE에 불러와 CODEF 재인증 없이 완료 처리."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") == "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    if not _apply_saved_medical_records(fid, entry):
        entry["medical_status"] = "pending"
        entry["medical_message"] = "저장된 진료내역이 없습니다. 새로 조회해 주세요."
        entry["has_saved_medical_records"] = False
    return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)


@app.get("/debug/storage-health", response_class=HTMLResponse)
def debug_storage_health(request: Request):
    """DEBUG: SQLite 진료내역 저장 상태 확인."""
    if not _debug_panel():
        return RedirectResponse("/", status_code=303)
    health = get_storage_health()
    rows = "".join(
        f"<tr><th>{label}</th><td>{value}</td></tr>"
        for label, value in (
            ("db_path", health["db_path"]),
            ("db_exists", "예" if health["db_exists"] else "아니오"),
            ("customers_count", health["customers_count"]),
            ("medical_records_count", health["medical_records_count"]),
            ("latest_medical_created_at", health["latest_medical_created_at"]),
            (
                "REDRIBBON_SEARCH_HASH_SECRET configured",
                "예" if health["search_hash_secret_configured"] else "아니오",
            ),
        )
    )
    body = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"/><title>Storage health</title></head>
<body><h1>진료내역 저장 DB 상태</h1><table border="1" cellpadding="8">{rows}</table>
<p><a href="/hospital/start">병원용 시작</a></p></body></html>"""
    return HTMLResponse(body)


@app.post("/hospital/hira-retry")
def hospital_hira_retry(flow_id: str | None = None):
    """진료내역 조회 실패 후 다시 시도."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") == "failed":
        entry["medical_status"] = "pending"
        _clear_hira_codef_fields(entry)
    return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)


@app.get("/hospital/insurance-request", response_class=HTMLResponse)
def hospital_insurance_request_get(
    request: Request,
    flow_id: str | None = None,
    debug_existing: str | None = None,
):
    """병원용 4단계 보험가입이력 가져오기."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    if _debug_panel() and (debug_existing or "").strip() == "1":
        entry["debug_show_existing_account"] = True
    _restore_saved_insurance_records_if_needed(entry)
    if entry.get("insurance_source") != "saved_imported" and (
        entry.get("insurance_stage") == "register_completed"
        and entry.get("credit4u_register_completed")
        and not entry.get("insurance_contract_auto_started")
    ):
        _on_register_completed(fid, entry)
    return templates.TemplateResponse(
        request,
        "insurance_request.html",
        _insurance_request_context(entry, fid),
    )


@app.post("/hospital/insurance-request/start")
def hospital_insurance_request_start(flow_id: str | None = None):
    """보험가입이력 조회 시작(데모: 외부 API 미연동)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    customer = entry.get("customer") or {}
    if _customer_has_stored_insurance_records(customer):
        _restore_saved_insurance_records_if_needed(entry)
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    manual_restart = (
        entry.get("insurance_status") == "pending"
        or entry.get("insurance_stage") == "register_completed"
        or entry.get("credit4u_register_completed")
    )
    if not manual_restart:
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    if not get_credit4u_secret():
        _provision_credit4u_credentials(entry)
        entry["insurance_status"] = "failed"
        entry["insurance_message"] = "보험가입이력 조회 설정값이 필요합니다. (REDRIBBON_CREDIT4U_SECRET)"
        entry["insurance_error_code"] = "MISSING_CREDIT4U_SECRET"
        entry.pop("insurance_error", None)
        entry.pop("insurance_stage", None)
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    customer = entry.get("customer") or {}
    if not all(
        (
            str(customer.get("name") or "").strip(),
            str(customer.get("identity") or "").strip(),
            str(customer.get("phone") or "").strip(),
        )
    ):
        entry["insurance_status"] = "failed"
        entry["insurance_message"] = "고객 정보가 부족하여 보험가입이력 조회를 시작할 수 없습니다."
        entry["insurance_error_code"] = "MISSING_CUSTOMER_INFO"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    entry["insurance_status"] = "in_progress"
    entry["insurance_stage"] = "requesting_contract_info"
    entry["insurance_message"] = (
        "신용정보원 보험가입이력을 조회하는 중입니다. 잠시만 기다려 주세요."
    )
    entry.pop("insurance_error_code", None)

    try:
        _request_insurance_history_start(fid, entry)
    except Credit4uConfigError:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "보험가입이력 조회 설정값이 필요합니다. (REDRIBBON_CREDIT4U_SECRET)"
        entry["insurance_error_code"] = "MISSING_CREDIT4U_SECRET"
    except ValueError:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "고객 정보가 부족하여 보험가입이력 조회를 시작할 수 없습니다."
        entry["insurance_error_code"] = "MISSING_CUSTOMER_INFO"

    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/existing-account")
def hospital_insurance_request_existing_account(
    flow_id: str | None = None,
    existing_id: str = Form(""),
    existing_password: str = Form(""),
):
    """기존 신용정보원 계정으로 보험가입이력 contract-info 1차 조회."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    allowed = (
        entry.get("insurance_stage") == "existing_account_required"
        or entry.get("insurance_stage") == "already_registered"
        or is_credit4u_existing_account_required(str(entry.get("insurance_error_code") or ""))
        or str(entry.get("credit4u_result_code") or "") == "CF-12069"
        or (_debug_panel() and entry.get("debug_show_existing_account"))
    )
    if not allowed:
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    account_id = (existing_id or "").strip()
    account_password = (existing_password or "").strip()
    if not account_id or not account_password:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "기존 신용정보원 아이디와 비밀번호를 모두 입력해 주세요."
        entry["insurance_error_code"] = "MISSING_EXISTING_ACCOUNT"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    entry["credit4u_credentials"] = {
        "id": account_id,
        "password": account_password,
        "generated": False,
        "source": "existing_account",
        "credential_version": CREDIT4U_CREDENTIAL_VERSION,
    }
    entry["credential_source"] = "existing_account"
    entry["credential_version"] = CREDIT4U_CREDENTIAL_VERSION
    entry["credential_loaded_from_store"] = False
    entry["credential_generated_new"] = False

    if not _save_credit4u_credentials_to_store(entry):
        entry["insurance_status"] = "in_progress"
        entry["insurance_stage"] = "existing_account_required"
        entry["credit4u_current_request_status"] = "existing_account_required"
        entry["insurance_message"] = (
            "기존 신용정보원 계정 저장에 실패했습니다. "
            "아이디·비밀번호를 확인한 뒤 다시 입력해 주세요."
        )
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    entry["insurance_status"] = "in_progress"
    entry["credit4u_current_flow"] = "contract"
    entry["insurance_stage"] = "requesting_contract_info"
    entry["credit4u_current_request_status"] = "requesting_contract_info"
    entry["insurance_message"] = (
        "기존 신용정보원 계정으로 보험가입이력을 조회하는 중입니다. 잠시만 기다려 주세요."
    )
    entry.pop("insurance_error_code", None)

    try:
        _start_credit4u_contract_info_first(fid, entry)
    except CodefClientError as exc:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = user_message_for_credit4u_failure(
            exc.code or "CLIENT_ERROR",
            exc.message,
        )
        entry["insurance_error_code"] = exc.code or "CLIENT_ERROR"
    except ValueError:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "보험가입이력 조회를 시작할 수 없습니다."
        entry["insurance_error_code"] = "MISSING_CREDENTIALS"

    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/secure-no")
def hospital_insurance_request_secure_no(
    flow_id: str | None = None,
    secureNo: str = Form(""),
):
    """보안문자 입력 후 CODEF contract-info 2차 요청."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("insurance_status") == "completed":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    if entry.get("insurance_status") != "in_progress":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    if entry.get("insurance_stage") != "secure_no_required":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    second_status = entry.get("credit4u_second_status") or "idle"
    if second_status == "in_progress":
        entry["insurance_message"] = "보안문자 확인을 이미 진행 중입니다. 잠시만 기다려 주세요."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    if second_status == "completed":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    secure_input = (secureNo or "").strip()
    if not secure_input:
        entry["insurance_message"] = "보안문자를 입력해 주세요."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    two_way_info = entry.get("credit4u_two_way_info")
    if not isinstance(two_way_info, dict) or not two_way_info:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = (
            "2차 인증 정보가 없어 조회를 진행할 수 없습니다. 처음부터 다시 시도해 주세요."
        )
        entry["insurance_error_code"] = "NO_TWO_WAY"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    credentials = entry.get("credit4u_credentials")
    if not isinstance(credentials, dict) or not credentials.get("id"):
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "신용정보원 조회용 계정정보가 없습니다."
        entry["insurance_error_code"] = "NO_CREDENTIALS"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    customer = entry.get("customer") or {}
    entry["credit4u_secure_no_input"] = secure_input
    entry["credit4u_second_status"] = "in_progress"
    _set_insurance_submitting(
        entry,
        "submitting_secure_no",
        "보안문자를 확인하고 있습니다. 잠시만 기다려 주세요.",
    )

    api_result = post_credit4u_contract_info_second(
        customer,
        credentials,
        secure_input,
        two_way_info,
    )
    _apply_credit4u_contract_info_second_response(entry, api_result)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/register-start")
def hospital_insurance_request_register_start(flow_id: str | None = None):
    """신용정보원 register 1차 수동 재시도."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    _start_credit4u_register(entry, fid)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/register-secure-no")
def hospital_insurance_request_register_secure_no(
    flow_id: str | None = None,
    secureNo: str = Form(""),
):
    """회원가입 보안문자 제출 → register 2차 요청."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("insurance_stage") != "register_secure_no_required":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    secure_input = str(secureNo or "").strip()
    if not secure_input:
        entry["insurance_message"] = "보안문자를 입력해 주세요."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    try:
        first_payload = _ensure_register_first_payload(entry, fid)
    except CodefClientError as exc:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = exc.message
        entry["insurance_error_code"] = exc.code or "CLIENT_ERROR"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    two_way_info = entry.get("credit4u_register_two_way_info")
    if not isinstance(first_payload, dict) or not first_payload:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "회원가입 요청 정보가 없습니다. 회원가입을 처음부터 다시 시도해 주세요."
        entry["insurance_error_code"] = "MISSING_REGISTER_FIRST_PAYLOAD"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    if not isinstance(two_way_info, dict) or not two_way_info:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "회원가입 인증 정보가 없습니다. 회원가입을 처음부터 다시 시도해 주세요."
        entry["insurance_error_code"] = "MISSING_REGISTER_TWO_WAY_INFO"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    entry["credit4u_register_secure_no_input"] = True
    _set_insurance_submitting(
        entry,
        "register_secure_no_submitting",
        _register_stage_user_message("register_secure_no_submitting"),
    )
    entry.pop("insurance_error_code", None)

    entry["credit4u_register_payload_purpose"] = "secure_no"
    api_result = post_credit4u_register_second(
        first_payload,
        two_way_info,
        {"secureNo": secure_input, "secureNoRefresh": "0"},
        purpose="secure_no",
    )
    _apply_credit4u_register_followup_response(entry, api_result, flow_id=fid)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/register-sms")
def hospital_insurance_request_register_sms(
    flow_id: str | None = None,
    smsAuthNo: str = Form(""),
):
    """SMS 인증번호 제출 → register 추가요청."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("insurance_stage") != "register_sms_required":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    sms_input = str(smsAuthNo or "").strip()
    if not sms_input:
        entry["insurance_message"] = "SMS 인증번호를 입력해 주세요."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    try:
        first_payload = _ensure_register_first_payload(entry, fid)
    except CodefClientError as exc:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = exc.message
        entry["insurance_error_code"] = exc.code or "CLIENT_ERROR"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    two_way_info = entry.get("credit4u_register_two_way_info")
    if not isinstance(two_way_info, dict) or not two_way_info:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "회원가입 인증 정보가 없습니다. 회원가입을 처음부터 다시 시도해 주세요."
        entry["insurance_error_code"] = "MISSING_REGISTER_TWO_WAY_INFO"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    entry["credit4u_register_sms_auth_no_input"] = True
    _set_insurance_submitting(
        entry,
        "register_sms_submitting",
        _register_stage_user_message("register_sms_submitting"),
    )
    entry.pop("insurance_error_code", None)

    entry["credit4u_register_payload_purpose"] = "sms"
    api_result = post_credit4u_register_second(
        first_payload,
        two_way_info,
        {"smsAuthNo": sms_input},
        purpose="sms",
    )
    _apply_credit4u_register_followup_response(entry, api_result, flow_id=fid)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/register-regenerate-id")
def hospital_insurance_request_register_regenerate_id(flow_id: str | None = None):
    """회원가입 아이디 재생성."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("credit4u_current_flow") != "register":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    if not get_credit4u_secret():
        entry["insurance_message"] = "신용정보원 계정을 재생성할 수 없습니다. 설정을 확인해 주세요."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    try:
        _regenerate_credit4u_signup_credentials(entry)
    except (Credit4uConfigError, ValueError) as exc:
        entry["insurance_message"] = str(exc) or "아이디를 재생성할 수 없습니다."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    entry["insurance_status"] = "in_progress"
    entry["insurance_stage"] = "register_signup_info_required"
    entry["credit4u_current_request_status"] = "register_signup_info_required"
    entry["insurance_message"] = (
        "새 아이디를 생성했습니다. 회원가입 정보를 다시 제출해 주세요."
    )
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/register-signup-info")
def hospital_insurance_request_register_signup_info(
    flow_id: str | None = None,
    email: str = Form(""),
    userId: str = Form(""),
):
    """회원가입 정보(아이디·비밀번호·이메일) 제출 → register 추가요청."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    signup_timing = new_register_signup_timing_debug()
    signup_timing["register_signup_info_post_entered"] = "예"
    entry["credit4u_register_signup_timing"] = signup_timing
    if entry.get("insurance_stage") != "register_signup_info_required":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    email_input = str(email or "").strip()
    if not email_input:
        email_input = str(
            entry.get("credit4u_signup_email") or entry.get("credit4u_register_email") or ""
        ).strip()
    if not email_input:
        entry["insurance_message"] = "이메일을 입력해 주세요."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    email_validation_error = validate_credit4u_email_for_register(email_input)
    if email_validation_error:
        entry["insurance_status"] = "in_progress"
        entry["credit4u_current_flow"] = "register"
        entry["insurance_stage"] = "register_signup_info_required"
        entry["credit4u_current_request_status"] = "register_signup_info_required"
        entry["insurance_message"] = email_validation_error
        entry["credit4u_register_email"] = email_input
        entry["credit4u_signup_email"] = email_input
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    credentials = entry.get("credit4u_credentials") or {}
    user_id = str(userId or credentials.get("id") or "").strip()
    if user_id and not validate_credit4u_id(user_id):
        entry["insurance_status"] = "in_progress"
        entry["credit4u_current_flow"] = "register"
        entry["insurance_stage"] = "register_signup_info_required"
        entry["credit4u_current_request_status"] = "register_signup_info_required"
        entry["insurance_message"] = (
            "아이디는 6~12자의 영문·숫자만 사용할 수 있습니다. "
            "다른 아이디를 자동 생성하거나 다시 입력해 주세요."
        )
        entry["credit4u_signup_id_retry"] = True
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    password = str(credentials.get("password") or "").strip()
    if password_contains_user_id(password, user_id):
        try:
            attempt = int(entry.get("credit4u_id_attempt_no") or 0) + 1
            renewed = regenerate_credit4u_credentials_for_signup(
                entry.get("customer") or {},
                attempt,
                previous_id=user_id,
            )
            entry["credit4u_id_attempt_no"] = attempt
            entry["credit4u_credentials"] = {
                **credentials,
                "id": user_id,
                "password": renewed["password"],
                "generated": True,
                "source": "generated",
            }
            password = renewed["password"]
            entry["insurance_message"] = (
                "비밀번호에 아이디가 포함되어 비밀번호를 다시 생성했습니다. 다시 제출해 주세요."
            )
        except (Credit4uConfigError, ValueError):
            entry["insurance_message"] = "비밀번호를 다시 생성할 수 없습니다."
            return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    if not user_id or not password:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "신용정보원 조회 계정이 준비되지 않았습니다."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    try:
        first_payload = _ensure_register_first_payload(entry, fid)
    except CodefClientError as exc:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = exc.message
        entry["insurance_error_code"] = exc.code or "CLIENT_ERROR"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    two_way_info = entry.get("credit4u_register_two_way_info")
    if not isinstance(two_way_info, dict) or not two_way_info:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "회원가입 인증 정보가 없습니다. 회원가입을 처음부터 다시 시도해 주세요."
        entry["insurance_error_code"] = "MISSING_REGISTER_TWO_WAY_INFO"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    entry["credit4u_register_email"] = email_input
    entry["credit4u_signup_email"] = email_input
    entry["credit4u_credentials"] = {
        **credentials,
        "id": user_id,
        "password": password,
        "generated": True,
        "source": str(credentials.get("source") or "generated"),
    }
    _set_insurance_submitting(
        entry,
        "register_signup_info_submitting",
        _register_stage_user_message("register_signup_info_submitting"),
    )
    entry.pop("insurance_error_code", None)
    try:
        api_result = _post_register_signup_info_sync(entry, fid)
    except (CodefClientError, ValueError) as exc:
        entry["insurance_status"] = "in_progress"
        entry["insurance_stage"] = "register_signup_info_required"
        entry["insurance_message"] = (
            str(exc) if isinstance(exc, ValueError) else getattr(exc, "message", str(exc))
        )
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    _apply_credit4u_register_followup_response(entry, api_result, flow_id=fid)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/register-email-auth")
def hospital_insurance_request_register_email_auth(
    flow_id: str | None = None,
    emailAuthNo: str = Form(""),
):
    """이메일 인증번호 제출 → register 추가요청."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("insurance_stage") != "register_email_auth_required":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    email_auth_input = str(emailAuthNo or "").strip()
    if not email_auth_input:
        entry["insurance_message"] = "이메일 인증번호를 입력해 주세요."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    try:
        first_payload = _ensure_register_first_payload(entry, fid)
    except CodefClientError as exc:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = exc.message
        entry["insurance_error_code"] = exc.code or "CLIENT_ERROR"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    two_way_info = entry.get("credit4u_register_two_way_info")
    if not isinstance(two_way_info, dict) or not two_way_info:
        entry["insurance_status"] = "failed"
        entry["insurance_stage"] = "failed"
        entry["insurance_message"] = "회원가입 인증 정보가 없습니다. 회원가입을 처음부터 다시 시도해 주세요."
        entry["insurance_error_code"] = "MISSING_REGISTER_TWO_WAY_INFO"
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    entry["credit4u_register_email_auth_no_input"] = True
    _set_insurance_submitting(
        entry,
        "register_email_auth_submitting",
        _register_stage_user_message("register_email_auth_submitting"),
    )
    entry.pop("insurance_error_code", None)
    entry["credit4u_register_payload_purpose"] = "email_auth"
    api_result = post_credit4u_register_second(
        first_payload,
        two_way_info,
        {"emailAuthNo": email_auth_input},
        purpose="email_auth",
    )
    _apply_credit4u_register_followup_response(entry, api_result, flow_id=fid)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/register-retry-current")
def hospital_insurance_request_register_retry_current(flow_id: str | None = None):
    """CF-01004 등 타임아웃 후 현재 회원가입 단계로 복귀."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("credit4u_current_flow") != "register":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)

    stage = _resolve_register_retry_stage(entry)
    entry["insurance_status"] = "in_progress"
    entry["credit4u_current_flow"] = "register"
    entry["insurance_stage"] = stage
    entry["credit4u_current_request_status"] = stage
    entry["insurance_message"] = register_followup_stage_message(stage)
    if stage == "register_signup_info_required":
        entry["insurance_message"] = (
            "회원가입에 사용할 아이디, 비밀번호, 이메일 정보가 필요합니다. "
            "다시 제출할 수 있습니다."
        )
    entry.pop("insurance_error_code", None)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/complete")
def hospital_insurance_request_complete(flow_id: str | None = None):
    """데모·디버그 모드에서만 샘플 완료 처리."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    if not _demo_complete_allowed():
        entry["insurance_message"] = (
            "상용 모드에서는 외부 API 조회 결과 없이 완료 처리할 수 없습니다."
        )
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    if entry.get("insurance_status") != "in_progress":
        entry["insurance_status"] = "failed"
        entry["insurance_error"] = "조회 순서가 올바르지 않아 완료 처리할 수 없습니다."
        entry.pop("insurance_message", None)
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    _apply_insurance_sample_complete(entry)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/retry")
def hospital_insurance_request_retry(flow_id: str | None = None):
    """보험가입이력 조회 실패 후 다시 시도."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("insurance_status") in ("failed", "in_progress"):
        entry["insurance_status"] = "pending"
        _clear_insurance_temp_fields(entry)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


def _prepare_integrated_flow_entry(flow_id: str, entry: dict[str, Any]) -> bool:
    """통합·AI 단계용 FLOW_STORE 동기화(저장본 우선, CODEF 미호출)."""
    entry["flow_id"] = flow_id
    if entry.get("medical_status") != "completed":
        _apply_saved_medical_records(flow_id, entry)
    _restore_saved_insurance_records_if_needed(entry)
    return (
        entry.get("medical_status") == "completed"
        and entry.get("insurance_status") == "completed"
    )


@app.post("/hospital/rebuild-insurance-summary")
def hospital_rebuild_insurance_summary_post(
    request: Request, flow_id: str | None = None
):
    """저장 원부로 insured_summary 재생성(CODEF 미호출)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    customer = entry.get("customer") or {}
    if not is_search_hash_secret_configured():
        return RedirectResponse(
            f"/hospital/analysis-ready?flow_id={fid}", status_code=303
        )
    package = rebuild_insurance_summary_for_customer(customer)
    if package:
        saved = load_latest_insurance_records(customer)
        if saved:
            _apply_saved_insurance_records_to_entry(entry, saved)
    return RedirectResponse(f"/hospital/analysis-ready?flow_id={fid}", status_code=303)


@app.get("/hospital/medical-records/full", response_class=HTMLResponse)
def hospital_medical_records_full(request: Request, flow_id: str | None = None):
    """진료내역 전체보기 — 저장본만 사용(CODEF 미호출)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if not _ensure_medical_records_loaded(fid, entry):
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    return templates.TemplateResponse(
        request,
        "medical_records_full.html",
        {
            "flow_id": fid,
            "current_step": 5,
            **_medical_records_view_context(entry),
        },
    )


@app.get("/hospital/analysis-ready", response_class=HTMLResponse)
def hospital_analysis_ready_get(request: Request, flow_id: str | None = None):
    """통합 결과확인 5단계 — 진료·보험 저장본 결합."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if not _prepare_integrated_flow_entry(fid, entry):
        if entry.get("medical_status") != "completed":
            return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    customer = entry.get("customer") or {}
    ctx = build_analysis_ready_context(
        entry,
        fid,
        debug=_debug_panel(),
        restore_medical_fn=_apply_saved_medical_records,
        restore_insurance_fn=_restore_saved_insurance_records_if_needed,
    )
    return templates.TemplateResponse(
        request,
        "analysis_ready.html",
        {
            **ctx,
            "customer_display": _build_customer_display(customer),
        },
    )


@app.get("/hospital/ai-analysis", response_class=HTMLResponse)
def hospital_ai_analysis_get(request: Request, flow_id: str | None = None):
    """AI 분석 6단계 — rule 기반 청구 후보(저장본 우선)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if not _prepare_integrated_flow_entry(fid, entry):
        if entry.get("medical_status") != "completed":
            return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    ctx = build_ai_analysis_context(
        entry,
        fid,
        debug=_debug_panel(),
        restore_medical_fn=_apply_saved_medical_records,
        restore_insurance_fn=_restore_saved_insurance_records_if_needed,
    )
    return templates.TemplateResponse(
        request,
        "ai_analysis.html",
        ctx,
    )


@app.post("/hospital/ai-analysis/start")
def hospital_ai_analysis_start_post(request: Request, flow_id: str | None = None):
    """rule 기반 AI 분석 실행(CODEF 미호출) → FLOW_STORE 저장."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if not _prepare_integrated_flow_entry(fid, entry):
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    try:
        result = execute_ai_claim_analysis(
            entry,
            restore_medical_fn=_apply_saved_medical_records,
            restore_insurance_fn=_restore_saved_insurance_records_if_needed,
            use_openai=True,
        )
        if result.get("error") == "no_data":
            entry["ai_analysis_message"] = result.get("message")
    except Exception:
        logger.exception("ai claim analysis failed flow_id=%s", fid)
        entry["ai_analysis_message"] = "분석 중 오류가 발생했습니다. rule 결과로 다시 시도해 주세요."
    return RedirectResponse(f"/hospital/ai-analysis?flow_id={fid}", status_code=303)


def _operator_console_cards() -> list[dict[str, Any]]:
    return [
        {
            "title": "병원 출력서류 수신함",
            "description": "가상프린터·병원 출력으로 수신된 문서를 고객·OCR·문서종류와 함께 확인합니다.",
            "href": "/operator/received-documents",
            "active": True,
        },
        {
            "title": "실손보험 청구 패키지",
            "description": "고객·실손 계약·진료·수신문서를 묶어 보험사 전송 직전 패키지를 검수합니다.",
            "href": "/operator/actual-loss-claim-package",
            "active": True,
        },
        {
            "title": "가상프린터 엔진 상태",
            "description": "RedRibbon 전용 가상프린터·Print Receiver 연결 상태를 확인합니다.",
            "href": None,
            "active": False,
        },
        {
            "title": "미분류 문서 관리",
            "description": "고객 미연결·문서종류 미확정 문서를 분류합니다.",
            "href": None,
            "active": False,
        },
        {
            "title": "고객/문서 매칭",
            "description": "수신 문서와 고객·진료·보험 데이터를 매칭합니다.",
            "href": None,
            "active": False,
        },
    ]


def _find_flow_entry_by_customer_key(customer_key: str) -> dict[str, Any] | None:
    key = str(customer_key or "").strip()
    if not key:
        return None
    for flow_id, entry in FLOW_STORE.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("customer_key") or "").strip() == key:
            merged = dict(entry)
            merged.setdefault("flow_id", flow_id)
            return merged
        customer = entry.get("customer")
        if not isinstance(customer, dict):
            continue
        if not is_search_hash_secret_configured():
            continue
        try:
            if make_customer_key(_normalize_stored_customer(customer)) == key:
                merged = dict(entry)
                merged.setdefault("flow_id", flow_id)
                return merged
        except PersistentStoreConfigError:
            continue
    return None


@app.get("/operator", response_class=HTMLResponse)
def operator_dashboard(request: Request):
    """운영자 콘솔 메인."""
    return templates.TemplateResponse(
        request,
        "operator_dashboard.html",
        {
            "operator_cards": _operator_console_cards(),
            **_printer_download_template_context(),
        },
    )


@app.get("/operator/received-documents", response_class=HTMLResponse)
def operator_received_documents(request: Request):
    """병원 출력서류 수신함."""
    seed_operator_received_documents_if_empty()
    documents = list_operator_received_documents(limit=200)
    return templates.TemplateResponse(
        request,
        "received_documents.html",
        {
            "documents": documents,
            **_printer_download_template_context(),
        },
    )


@app.get("/operator/received-documents/{document_id}/file")
def operator_received_document_file(document_id: int):
    """수신 문서 파일 보기(데모 — 실제 파일 없으면 안내)."""
    doc = get_received_document_by_id(document_id)
    if not doc:
        return PlainTextResponse("문서를 찾을 수 없습니다.", status_code=404)
    file_path = str(doc.get("file_path") or "").strip()
    if file_path:
        path = Path(file_path)
        if path.is_file():
            from fastapi.responses import FileResponse

            return FileResponse(path)
    return PlainTextResponse(
        f"[데모] 문서 #{document_id} · {doc.get('document_title')} — "
        "실제 파일 저장 경로가 연결되면 이 링크에서 열립니다.",
        media_type="text/plain; charset=utf-8",
    )


@app.get("/operator/actual-loss-claim-package", response_class=HTMLResponse)
def operator_actual_loss_claim_package_get(
    request: Request,
    customer_id: str | None = None,
):
    """실손보험 청구 패키지 — 고객 선택 또는 검수 화면."""
    customer_key = str(customer_id or request.query_params.get("customer_id") or "").strip()
    if not customer_key:
        return templates.TemplateResponse(
            request,
            "actual_loss_claim_package_preview.html",
            {
                "show_customer_picker": True,
                "customers": build_operator_customer_picker(FLOW_STORE),
                "package_error": None,
            },
        )
    entry = _find_flow_entry_by_customer_key(customer_key)
    package = build_actual_loss_claim_package(customer_key, entry=entry)
    if package.get("error"):
        return templates.TemplateResponse(
            request,
            "actual_loss_claim_package_preview.html",
            {
                "show_customer_picker": False,
                "package_error": package.get("message") or "패키지를 구성할 수 없습니다.",
                "customer_id": customer_key,
            },
        )
    return templates.TemplateResponse(
        request,
        "actual_loss_claim_package_preview.html",
        {
            "show_customer_picker": False,
            "package_error": None,
            "package": package,
            "customer_id": customer_key,
        },
    )


@app.post("/operator/actual-loss-claim-package/demo-transmit")
def operator_actual_loss_claim_package_demo_transmit_post(
    request: Request,
    customer_id: str | None = None,
    note: str = Form(""),
):
    """데모 전송 상태만 저장(보험사 API 미호출)."""
    customer_key = str(customer_id or request.query_params.get("customer_id") or "").strip()
    if not customer_key:
        return RedirectResponse("/operator/actual-loss-claim-package", status_code=303)
    entry = _find_flow_entry_by_customer_key(customer_key)
    package = build_actual_loss_claim_package(customer_key, entry=entry)
    payload = package.get("transmission_payload") if isinstance(package, dict) else {}
    save_actual_loss_claim_demo_state(
        customer_key,
        demo_status="demo_transmitted",
        note=(note or "").strip() or "데모 전송 처리(보험사 API 미호출)",
        payload=payload if isinstance(payload, dict) else {},
    )
    return RedirectResponse(
        f"/operator/actual-loss-claim-package?customer_id={customer_key}",
        status_code=303,
    )


@app.get("/hospital/hira-consent", response_class=HTMLResponse)
def hospital_hira_consent_stub(request: Request):
    """시연 시작 진입 스텁 — 실제 앱에서는 기존 병원 동의/고객 흐름으로 교체."""
    return templates.TemplateResponse(
        request,
        "flow_stub.html",
        {
            "title": "병원 동의 · 고객 흐름",
            "lead": "시연 시작 지점입니다. 실제 서비스에서는 이 경로에 기존 HIRA 동의/고객등록 화면이 연결됩니다.",
            "back_url": "/",
        },
    )


@app.get("/admin/dashboard", response_class=RedirectResponse)
def admin_dashboard_redirect() -> RedirectResponse:
    """구 URL 호환."""
    return RedirectResponse("/operator", status_code=303)
