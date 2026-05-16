# -*- coding: utf-8 -*-
"""RedRibbon MVP — 루트 인트로 및 시연/운영자 진입 스텁.

기존 대형 앱과 병합할 때: GET `/` 는 인트로 템플릿을 렌더링하도록 유지하고,
아래 스텁 라우트(`/hospital/hira-consent` 등)는
프로젝트에 이미 동일 경로가 있으면 이 블록을 제거하고 기존 구현만 두면 됩니다.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.codef_client import (
    CodefClientError,
    build_hira_medical_payload,
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

app = FastAPI(title="RedRibbon MVP")

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
    entry["medical_status"] = "completed"
    entry["medical_result_counts"] = {"basic": 113, "detail": 703, "prescribe": 382}
    entry["medical_records"] = list(_MEDICAL_SAMPLE_RECORDS)
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
            raw, "resPaidAmount", "resSelfPayAmount", "patient_paid_amount", "copay_amount"
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


def _build_customer_display(customer: dict[str, Any]) -> dict[str, str]:
    auth = (customer.get("auth_method") or "").strip().lower()
    label = "카카오톡 간편인증" if auth == "kakao" else "본인인증"
    return {
        "name": (customer.get("name") or "").strip() or "—",
        "identity_masked": _mask_identity(str(customer.get("identity") or "")),
        "phone_masked": _mask_phone(str(customer.get("phone") or "")),
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
        "category": "기타",
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
        "product_name": "시연용 운전자보험",
        "policy_no": "DEMO-005",
        "status": "유지",
        "role": "피보험자",
        "category": "운전자",
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


def _request_insurance_history_demo(flow_id: str) -> None:
    """향후 신용정보원·제휴 API 연동 시 이 함수에 외부 호출을 연결합니다."""
    del flow_id  # noqa: ARG001


def _apply_insurance_demo_complete(entry: dict[str, Any]) -> None:
    records = [dict(r) for r in _INSURANCE_SAMPLE_RECORDS]
    entry["insurance_status"] = "completed"
    entry["insurance_summary"] = {
        "total": 11,
        "insured_valid": 10,
        "company_count": 5,
    }
    entry["insurance_records"] = records
    entry.pop("insurance_message", None)


def _clear_insurance_temp_fields(entry: dict[str, Any]) -> None:
    for key in ("insurance_message", "insurance_summary", "insurance_records"):
        entry.pop(key, None)


def _insurance_request_context(entry: dict[str, Any], fid: str) -> dict[str, Any]:
    status = entry.get("insurance_status") or "pending"
    customer = entry.get("customer") or {}
    summary = entry.get("insurance_summary") or {"total": 0, "insured_valid": 0, "company_count": 0}
    records = list(entry.get("insurance_records") or []) if status == "completed" else []
    return {
        "current_step": 4,
        "flow_id": fid,
        "debug_panel": _debug_panel(),
        "demo_complete_allowed": _demo_complete_allowed(),
        "customer_display": _build_customer_display(customer),
        "insurance_status": status,
        "insurance_message": entry.get("insurance_message"),
        "insurance_summary": summary,
        "insurance_records": records,
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
            "printer_link_url": None,
            "debug_panel": False,
        },
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

    FLOW_STORE[new_id] = {
        "customer": {
            "name": (name or "").strip(),
            "identity": (identity or "").strip(),
            "phone": (phone or "").strip(),
            "telecom": (telecom or "").strip(),
            "email": (email or "").strip(),
            "auth_method": "kakao",
        },
        "created_in_final": True,
        "medical_status": "pending",
        "insurance_status": "pending",
        "second_status": "idle",
    }

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
    if status == "completed":
        basic_all = _medical_records_basic_all(entry)
        detail_all = _medical_records_detail_all(entry)
        prescribe_all = _medical_records_prescribe_all(entry)
    show_hira_modal, hira_modal_step = _hira_modal_context(entry)

    return templates.TemplateResponse(
        request,
        "hira_start.html",
        {
            "current_step": 3,
            "flow_id": fid,
            "debug_panel": _debug_panel(),
            "customer_display": customer_display,
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
    return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)


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
def hospital_insurance_request_get(request: Request, flow_id: str | None = None):
    """병원용 4단계 보험가입이력 가져오기."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
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
    if entry.get("insurance_status") != "pending":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    _request_insurance_history_demo(fid)
    entry["insurance_status"] = "in_progress"
    entry["insurance_message"] = "보험가입이력 조회를 시작했습니다."
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
        entry["insurance_message"] = "조회 순서가 올바르지 않아 완료 처리할 수 없습니다."
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    _apply_insurance_demo_complete(entry)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.post("/hospital/insurance-request/retry")
def hospital_insurance_request_retry(flow_id: str | None = None):
    """보험가입이력 조회 실패 후 다시 시도."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("insurance_status") == "failed":
        entry["insurance_status"] = "pending"
        _clear_insurance_temp_fields(entry)
    return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)


@app.get("/hospital/analysis-ready", response_class=HTMLResponse)
def hospital_analysis_ready_get(request: Request, flow_id: str | None = None):
    """통합 결과확인 5단계 최소 스텁(추후 확장)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    if entry.get("insurance_status") != "completed":
        return RedirectResponse(f"/hospital/insurance-request?flow_id={fid}", status_code=303)
    return templates.TemplateResponse(
        request,
        "analysis_ready_stub.html",
        {
            "current_step": 5,
            "flow_id": fid,
            "debug_panel": _debug_panel(),
        },
    )


@app.get("/operator", response_class=HTMLResponse)
def operator_stub(request: Request):
    """운영자 콘솔 스텁."""
    return templates.TemplateResponse(
        request,
        "flow_stub.html",
        {
            "title": "운영자 콘솔",
            "lead": "운영자 화면 진입 지점입니다. 통합 앱에서는 기존 운영자 대시보드로 연결하세요.",
            "back_url": "/",
        },
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
