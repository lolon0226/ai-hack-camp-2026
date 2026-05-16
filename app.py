# -*- coding: utf-8 -*-
"""RedRibbon MVP — 루트 인트로 및 시연/운영자 진입 스텁.

기존 대형 앱과 병합할 때: GET `/` 는 인트로 템플릿을 렌더링하도록 유지하고,
아래 스텁 라우트(`/hospital/hira-consent` 등)는
프로젝트에 이미 동일 경로가 있으면 이 블록을 제거하고 기존 구현만 두면 됩니다.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="RedRibbon MVP")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 병원 흐름: 메모리 저장(재시작 시 초기화). POST 고객등록 시 항상 신규 flow_id만 발급.
FLOW_STORE: dict[str, dict[str, Any]] = {}


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
    """향후 CODEF 연동 시 이 함수에 외부 호출을 연결합니다. 데모 단계에서는 호출하지 않습니다."""
    del flow_id  # noqa: ARG001


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
    records = list(entry.get("medical_records") or []) if status == "completed" else []

    return templates.TemplateResponse(
        request,
        "hira_start.html",
        {
            "current_step": 3,
            "flow_id": fid,
            "debug_panel": False,
            "customer_display": customer_display,
            "medical_status": status,
            "medical_message": entry.get("medical_message"),
            "medical_result_counts": counts,
            "medical_records": records,
        },
    )


@app.post("/hospital/hira-auth-request")
def hospital_hira_auth_request(flow_id: str | None = None):
    """진료내역 조회 카카오 인증 요청(데모: CODEF 미연동)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "pending":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    _request_hira_auth_demo(fid)
    entry["medical_status"] = "waiting_auth"
    entry["medical_auth_requested"] = True
    entry["medical_message"] = "카카오 인증 요청이 발송되었습니다."
    return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)


@app.post("/hospital/hira-complete-auth")
def hospital_hira_complete_auth(flow_id: str | None = None):
    """데모: 인증 완료 후 진료내역 수신 완료 처리."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "waiting_auth":
        entry["medical_status"] = "failed"
        entry["medical_message"] = "인증 및 조회 순서가 올바르지 않아 처리할 수 없습니다."
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    entry["medical_status"] = "completed"
    entry["medical_result_counts"] = {"basic": 113, "detail": 703, "prescribe": 382}
    entry["medical_records"] = list(_MEDICAL_SAMPLE_RECORDS)
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
        entry["medical_auth_requested"] = False
        entry.pop("medical_message", None)
        entry.pop("medical_result_counts", None)
        entry.pop("medical_records", None)
    return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)


@app.get("/hospital/insurance-request", response_class=HTMLResponse)
def hospital_insurance_request_get(request: Request, flow_id: str | None = None):
    """보험가입이력 4단계 최소 스텁(추후 확장)."""
    fid = _canonical_flow_id(flow_id)
    if not fid:
        return RedirectResponse("/hospital/customer", status_code=303)
    entry = FLOW_STORE[fid]
    if entry.get("medical_status") != "completed":
        return RedirectResponse(f"/hospital/hira-start?flow_id={fid}", status_code=303)
    return templates.TemplateResponse(
        request,
        "insurance_request_stub.html",
        {
            "current_step": 4,
            "flow_id": fid,
            "debug_panel": False,
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
