# -*- coding: utf-8 -*-
"""통합 결과확인(5단계)·AI 분석(6단계) — 저장본 우선, rule 기반 후보."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from services.credit4u_contract_summary import (
    infer_company_name_from_product,
    is_unknown_company_name,
    resolve_company_name_for_product,
)
from services.insurance_summary import (
    flatten_imported_insurance_records,
    resolve_stored_insurance_for_display,
)
from services.persistent_store import (
    is_search_hash_secret_configured,
    load_latest_insurance_records,
    load_latest_medical_records,
)

logger = logging.getLogger(__name__)

_AI_ENGINE_RULE = "RedRibbon Rule Engine v1"
_CATEGORY_KEYS = (
    "high_potential",
    "need_review",
    "need_documents",
    "low_potential",
)
_CATEGORY_LABELS = {
    "high_potential": "청구 가능성 높음",
    "need_review": "검토 필요",
    "need_documents": "서류 보강 필요",
    "low_potential": "청구 가능성 낮음",
}

_ACTUAL_LOSS_COVERAGE_KEYWORDS = (
    "질병입원의료비",
    "질병통원의료비",
    "상해입원의료비",
    "상해통원의료비",
    "입원의료비",
    "통원의료비",
    "처방조제비",
    "실손의료비",
    "의료실비",
    "실비",
    "실손",
)

_COPAY_FIELD_KEYS = (
    "resSelfPayAmt",
    "resSelfPayAmount",
    "resMyPayment",
    "resPatientPayAmount",
    "resUserPayAmount",
    "resPaidAmt",
    "resMedicalExpenses",
    "resTotalMedicalExpense",
    "resCopayAmt",
    "resDeductibleAmt",
    "resPaidAmount",
    "self_pay",
    "patient_pay",
    "paid_amount",
    "copay_amount",
    "patient_paid_amount",
    "내가낸의료비",
    "본인부담금",
    "진료비",
)


def _utc_now_display() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _parse_amount(value: Any) -> int:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def _format_amount_krw(amount: int) -> str:
    if amount <= 0:
        return "0원"
    return f"{amount:,}원"


def _format_period(start: Any, end: Any) -> str:
    def fmt_one(raw: Any) -> str:
        text = re.sub(r"\D", "", str(raw or ""))
        if len(text) == 8:
            return f"{text[0:4]}.{text[4:6]}.{text[6:8]}"
        return str(raw or "").strip()

    start_s = fmt_one(start)
    end_s = fmt_one(end)
    if start_s and end_s:
        return f"{start_s} ~ {end_s}"
    return start_s or end_s or "—"


def _pick_medical_text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_medical_visit_row(raw: Any) -> dict[str, Any]:
    """진료 1건 — AI·통합화면용(본인부담금 파싱 포함)."""
    if not isinstance(raw, dict):
        return {
            "visit_date": "—",
            "hospital_name": "—",
            "department": "—",
            "diagnosis": "—",
            "diagnosis_code": "—",
            "copay_amount": 0,
            "copay_display": "0원",
        }
    copay = 0
    for key in _COPAY_FIELD_KEYS:
        copay = _parse_amount(raw.get(key))
        if copay > 0:
            break
    if copay <= 0:
        copay = _parse_amount(
            _pick_medical_text(raw, "resDeductibleAmt", "resPaidAmount", "copay_amount")
        )
    diagnosis = _pick_medical_text(
        raw,
        "resDiseaseName",
        "resMainDiseaseName",
        "resSickName",
        "main_diagnosis",
        "diagnosis",
    ) or "—"
    visit_date = _pick_medical_text(
        raw,
        "resTreatStartDate",
        "resTreatDate",
        "resVisitDate",
        "visit_date",
        "treatment_date",
    ) or "—"
    if len(visit_date) == 8 and visit_date.isdigit():
        visit_date = f"{visit_date[0:4]}.{visit_date[4:6]}.{visit_date[6:8]}"
    return {
        "visit_date": visit_date,
        "hospital_name": _pick_medical_text(
            raw, "resHospitalName", "resInstitutionName", "hospital_name"
        )
        or "—",
        "department": _pick_medical_text(
            raw, "resDepartment", "resDeptName", "department"
        )
        or "—",
        "diagnosis": diagnosis,
        "diagnosis_code": _pick_medical_text(
            raw, "resDiseaseCode", "resSickCode", "resMainDiseaseCode", "diagnosis_code"
        )
        or "—",
        "copay_amount": copay,
        "copay_display": _format_amount_krw(copay),
    }


def _medical_counts_from_entry(entry: dict[str, Any]) -> dict[str, int]:
    counts = entry.get("medical_result_counts")
    if isinstance(counts, dict) and counts:
        return {
            "basic": int(counts.get("basic") or 0),
            "detail": int(counts.get("detail") or 0),
            "prescribe": int(counts.get("prescribe") or 0),
        }
    basic = entry.get("medical_records_basic") or entry.get("medical_records") or []
    detail = entry.get("medical_records_detail") or []
    prescribe = entry.get("medical_records_prescribe") or []
    return {
        "basic": len(basic) if isinstance(basic, list) else 0,
        "detail": len(detail) if isinstance(detail, list) else 0,
        "prescribe": len(prescribe) if isinstance(prescribe, list) else 0,
    }


def _medical_visits_from_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    raw_list = entry.get("medical_records_basic")
    if not isinstance(raw_list, list) or not raw_list:
        raw_list = entry.get("medical_records") or []
    if not isinstance(raw_list, list):
        return []
    return [normalize_medical_visit_row(row) for row in raw_list if isinstance(row, dict)]


def _has_actual_loss_coverage(
    normalized_payload: Any,
    summary: dict[str, Any] | None,
    *,
    insured_summary: dict[str, Any] | None = None,
) -> bool:
    summary = summary if isinstance(summary, dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    if int(counts.get("actual_loss_contracts") or 0) > 0:
        return True
    insured = insured_summary
    if not isinstance(insured, dict):
        insured = summary.get("insured_summary")
    if isinstance(insured, dict):
        for group in insured.get("company_groups") or []:
            if not isinstance(group, dict):
                continue
            for product in group.get("products") or []:
                if not isinstance(product, dict):
                    continue
                label = str(product.get("source_type_label") or "")
                stype = str(product.get("source_type") or "")
                if "실손" in label or stype in ("actual_loss", "actual_loss_estimate"):
                    return True
    flat = flatten_imported_insurance_records(normalized_payload)
    for row in flat:
        name = str(
            row.get("resInsuranceName")
            or row.get("insurance_name")
            or row.get("product_name")
            or ""
        )
        if "실손" in name or str(row.get("raw_type") or "") == "actual_loss_contracts":
            return True
    return False


def _insurance_overview_from_insured(insured_summary: dict[str, Any] | None) -> dict[str, Any]:
    insured = insured_summary if isinstance(insured_summary, dict) else {}
    counts = insured.get("counts") if isinstance(insured.get("counts"), dict) else {}
    return {
        "source_label": "신용정보원 내보험다보여",
        "company_count": int(counts.get("company_count") or 0),
        "product_count": int(counts.get("product_count") or 0),
        "active_contract_count": int(counts.get("active_product_count") or 0),
        "coverage_count": int(counts.get("coverage_count") or 0),
    }


def _coverage_text(coverage: dict[str, Any]) -> str:
    return " ".join(
        str(coverage.get(key) or "")
        for key in ("coverage_name", "agreement_type", "status")
    ).strip()


def _is_actual_loss_coverage_text(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if "의료비" in value and not any(
        token in value for token in _ACTUAL_LOSS_COVERAGE_KEYWORDS
    ):
        return False
    return any(token in value for token in _ACTUAL_LOSS_COVERAGE_KEYWORDS)


def is_strict_actual_loss_product(product: dict[str, Any]) -> bool:
    name = str(product.get("insurance_name") or "")
    if "실손" in name:
        return True
    for cov in product.get("coverages") or []:
        if isinstance(cov, dict) and _is_actual_loss_coverage_text(_coverage_text(cov)):
            return True
    return False


def collect_actual_loss_coverages(product: dict[str, Any]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for cov in product.get("coverages") or []:
        if isinstance(cov, dict) and _is_actual_loss_coverage_text(_coverage_text(cov)):
            matched.append(cov)
    return matched


def detect_actual_loss_products(
    insured_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """피보험자 요약에서 실손성 상품·담보 목록."""
    insured = insured_summary if isinstance(insured_summary, dict) else {}
    found: list[dict[str, Any]] = []
    for group in insured.get("company_groups") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("company_name") or "")
        for product in group.get("products") or []:
            if not isinstance(product, dict):
                continue
            if not is_strict_actual_loss_product(product):
                continue
            row = dict(product)
            row["_group_company_name"] = group_name
            row["actual_loss_coverages"] = collect_actual_loss_coverages(product)
            found.append(row)
    return found


def _resolve_related_company_name(
    product: dict[str, Any],
    group_company: str = "",
) -> str:
    raw = str(product.get("company_name") or group_company or "").strip()
    if not is_unknown_company_name(raw):
        return raw
    resolved = resolve_company_name_for_product(
        raw,
        product.get("insurance_name"),
    )
    name = str(resolved.get("company_name") or "").strip()
    if name and not is_unknown_company_name(name):
        return name
    inferred = infer_company_name_from_product(str(product.get("insurance_name") or ""))
    inferred_name = str(inferred.get("company_name") or "").strip()
    if inferred_name:
        return inferred_name
    return "보험회사 확인 필요"


def _matched_coverage_label(product: dict[str, Any]) -> str:
    coverages = product.get("actual_loss_coverages") or collect_actual_loss_coverages(
        product
    )
    names: list[str] = []
    for cov in coverages[:5]:
        if not isinstance(cov, dict):
            continue
        label = str(cov.get("coverage_name") or cov.get("agreement_type") or "").strip()
        if label and label not in names:
            names.append(label)
    return ", ".join(names) if names else "실손성 담보(추정)"


def build_related_insurance_list(
    actual_loss_products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for product in actual_loss_products:
        items.append(
            {
                "company_name": _resolve_related_company_name(
                    product, str(product.get("_group_company_name") or "")
                ),
                "insurance_name": str(product.get("insurance_name") or "—"),
                "policy_number_hid": str(product.get("policy_number_hid") or "—"),
                "contract_status": str(product.get("contract_status") or "—"),
                "matched_coverage": _matched_coverage_label(product),
            }
        )
    return items


def _all_insurance_product_names(insured_summary: dict[str, Any] | None) -> list[str]:
    names: list[str] = []
    insured = insured_summary if isinstance(insured_summary, dict) else {}
    for group in insured.get("company_groups") or []:
        if not isinstance(group, dict):
            continue
        for product in group.get("products") or []:
            if not isinstance(product, dict):
                continue
            name = str(product.get("insurance_name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _classify_visit_candidate(
    visit: dict[str, Any],
    *,
    has_actual_loss: bool,
    related_insurance: list[dict[str, Any]],
) -> tuple[str, str, str, list[str]]:
    copay = int(visit.get("copay_amount") or 0)
    hospital = str(visit.get("hospital_name") or "").strip()
    diagnosis = str(visit.get("diagnosis") or "").strip()
    department = str(visit.get("department") or "").strip()
    combined = f"{hospital} {department} {diagnosis}"

    docs_keywords = (
        "수술",
        "입원",
        "입퇴원",
        "진단서",
        "내시경",
        "MRI",
        "CT",
        "초음파",
        "조직검사",
    )
    required_docs: list[str] = []

    if copay <= 0:
        return (
            "low_potential",
            "본인부담금이 확인되지 않아 청구 검토 후보 추정이 어렵습니다.",
            "진료비·본인부담금 영수증을 확인해 주세요.",
            required_docs,
        )
    if not has_actual_loss or not related_insurance:
        return (
            "low_potential",
            "실손성 상품·담보가 확인되지 않아 청구 검토 가능성이 낮습니다(추정).",
            "보험가입이력에서 실손 담보를 다시 확인해 주세요.",
            required_docs,
        )
    if not hospital or hospital == "—":
        return (
            "need_review",
            "병원명 정보가 부족해 추가 검토가 필요합니다.",
            "진료기관명을 보강한 뒤 청구 후보를 검토해 주세요.",
            required_docs,
        )
    if not diagnosis or diagnosis == "—":
        return (
            "need_documents",
            "상병·진료명 정보 보강 후 청구 후보 검토를 권장합니다.",
            "진단서·진료확인서 등 서류를 준비해 주세요.",
            ["진단서", "진료확인서"],
        )

    if "응급" in combined or "응급실" in combined:
        return (
            "high_potential",
            "응급 진료로 실손 청구 검토 후보 가능성이 있습니다(추정).",
            "응급실 진료기록·영수증을 확인해 주세요.",
            ["응급실 진료기록"],
        )
    if "정형외과" in department and any(
        token in diagnosis for token in ("상해", "골절", "탈구", "염좌", "손상")
    ):
        return (
            "high_potential",
            "정형외과 상해성 질환으로 실손 청구 검토 후보 가능성이 있습니다(추정).",
            "초진·외래 영수증과 상해 관련 소견을 확인해 주세요.",
            [],
        )
    if copay >= 50000:
        return (
            "high_potential",
            "본인부담금 규모가 커 실손 청구 검토 후보로 우선 검토할 수 있습니다(추정).",
            "진료비 영수증·세부내역을 준비해 주세요.",
            [],
        )
    if any(token in combined for token in docs_keywords):
        required_docs = ["진단서", "검사결과지"]
        if "수술" in combined:
            required_docs.append("수술확인서")
        if "입원" in combined:
            required_docs.append("입퇴원확인서")
        return (
            "need_documents",
            "검사·수술·입원 관련 서류 확인이 필요해 보입니다(추정).",
            "약관 기준에 맞는 서류를 보강해 주세요.",
            required_docs,
        )
    if any(token in diagnosis for token in ("감기", "비염", "기관지", "몸살")):
        return (
            "need_review",
            "단순 호흡기 질환으로 소액·반복 청구 여부를 검토해야 합니다(추정).",
            "통원 횟수·약관 면책 조항을 확인해 주세요.",
            [],
        )
    if "약국" in hospital or "조제" in diagnosis:
        return (
            "need_review",
            "약국·조제 건으로 약관상 통원/처방조제 담보 범위를 확인해야 합니다(추정).",
            "처방전·조제 영수증을 확인해 주세요.",
            [],
        )
    if "치과" in hospital or "한방" in hospital:
        return (
            "need_review",
            "치과·한방 진료는 약관 확인이 필요한 청구 검토 후보입니다(추정).",
            "해당 담보 가입 여부와 면책을 확인해 주세요.",
            [],
        )
    if copay >= 10000:
        return (
            "need_review",
            "본인부담 진료로 실손 청구 후보 검토가 가능합니다(추정).",
            "영수증·진료내역을 대조해 주세요.",
            [],
        )
    return (
        "need_review",
        "소액 본인부담 건으로 서류·약관 확인 후 검토가 필요합니다(추정).",
        "청구 전 담보·면책을 확인해 주세요.",
        [],
    )


def run_ai_claim_analysis(
    medical_visits: list[dict[str, Any]],
    insured_summary: dict[str, Any] | None,
    *,
    product_count: int = 0,
) -> dict[str, Any]:
    """rule 기반 청구 검토 후보(ai_analysis_result 구조)."""
    actual_loss_products = detect_actual_loss_products(insured_summary)
    has_actual_loss = bool(actual_loss_products)
    related_insurance_default = build_related_insurance_list(actual_loss_products)
    all_product_names = _all_insurance_product_names(insured_summary)

    categories: dict[str, list[dict[str, Any]]] = {key: [] for key in _CATEGORY_KEYS}
    for index, visit in enumerate(medical_visits):
        category, reason, next_action, required_docs = _classify_visit_candidate(
            visit,
            has_actual_loss=has_actual_loss,
            related_insurance=related_insurance_default,
        )
        copay = int(visit.get("copay_amount") or 0)
        candidate = {
            "id": f"visit-{index + 1}",
            "category": category,
            "category_label": _CATEGORY_LABELS[category],
            "date": visit.get("visit_date") or "—",
            "hospital_name": visit.get("hospital_name") or "—",
            "department": visit.get("department") or "—",
            "diagnosis_name": visit.get("diagnosis") or "—",
            "diagnosis_code": visit.get("diagnosis_code") or "—",
            "estimated_amount": copay if category != "low_potential" or copay > 0 else 0,
            "amount_label": f"청구 후보 금액 {_format_amount_krw(copay)}",
            "reason": reason,
            "next_action": next_action,
            "required_documents": required_docs,
            "related_medical": [
                {
                    "visit_date": visit.get("visit_date"),
                    "hospital_name": visit.get("hospital_name"),
                    "department": visit.get("department"),
                    "diagnosis": visit.get("diagnosis"),
                    "copay_display": visit.get("copay_display"),
                }
            ],
            "related_insurance": related_insurance_default,
            "visit_date": visit.get("visit_date"),
            "diagnosis": visit.get("diagnosis"),
            "estimated_display": _format_amount_krw(copay),
        }
        categories[category].append(candidate)

    def _sum_amount(key: str) -> int:
        return sum(int(c.get("estimated_amount") or 0) for c in categories[key])

    totals = {
        "total_candidate_count": sum(len(categories[k]) for k in _CATEGORY_KEYS),
        "total_estimated_amount": sum(_sum_amount(k) for k in _CATEGORY_KEYS),
        "high_count": len(categories["high_potential"]),
        "high_amount": _sum_amount("high_potential"),
        "review_count": len(categories["need_review"]),
        "review_amount": _sum_amount("need_review"),
        "documents_count": len(categories["need_documents"]),
        "documents_amount": _sum_amount("need_documents"),
        "low_count": len(categories["low_potential"]),
        "low_amount": _sum_amount("low_potential"),
    }
    totals["total_estimated_display"] = _format_amount_krw(
        int(totals["total_estimated_amount"])
    )

    detected_summary = [
        {
            "company_name": _resolve_related_company_name(p, p.get("_group_company_name", "")),
            "insurance_name": p.get("insurance_name"),
            "policy_number_hid": p.get("policy_number_hid"),
        }
        for p in actual_loss_products
    ]

    return {
        "engine": _AI_ENGINE_RULE,
        "analysis_time": _utc_now_display(),
        "input_summary": {
            "medical_count": len(medical_visits),
            "insurance_product_count": product_count,
        },
        "totals": totals,
        "categories": categories,
        "all_insurance_product_names": all_product_names,
        "detected_actual_loss_products": detected_summary,
        "fallback_reason": None,
        "category_tabs": [
            {
                "id": "high_potential",
                "label": _CATEGORY_LABELS["high_potential"],
                "count": totals["high_count"],
                "amount": totals["high_amount"],
                "amount_display": _format_amount_krw(int(totals["high_amount"])),
            },
            {
                "id": "need_review",
                "label": _CATEGORY_LABELS["need_review"],
                "count": totals["review_count"],
                "amount": totals["review_amount"],
                "amount_display": _format_amount_krw(int(totals["review_amount"])),
            },
            {
                "id": "need_documents",
                "label": _CATEGORY_LABELS["need_documents"],
                "count": totals["documents_count"],
                "amount": totals["documents_amount"],
                "amount_display": _format_amount_krw(int(totals["documents_amount"])),
            },
            {
                "id": "low_potential",
                "label": _CATEGORY_LABELS["low_potential"],
                "count": totals["low_count"],
                "amount": totals["low_amount"],
                "amount_display": _format_amount_krw(int(totals["low_amount"])),
            },
        ],
        "default_category": "high_potential",
    }


def build_ai_safe_medical_summary(medical_visits: list[dict[str, Any]]) -> dict[str, Any]:
    """OpenAI 전송용 진료 요약(민감정보 제외)."""
    dates = [
        re.sub(r"\D", "", str(v.get("visit_date") or ""))[:8]
        for v in medical_visits
        if v.get("visit_date")
    ]
    dates = [d for d in dates if len(d) == 8]
    hospitals: dict[str, int] = {}
    diagnoses: dict[str, int] = {}
    for visit in medical_visits:
        h = str(visit.get("hospital_name") or "").strip()
        if h and h != "—":
            hospitals[h] = hospitals.get(h, 0) + 1
        d = str(visit.get("diagnosis") or "").strip()
        if d and d != "—":
            diagnoses[d] = diagnoses.get(d, 0) + 1
    top_hospitals = sorted(hospitals.items(), key=lambda x: -x[1])[:8]
    top_diagnoses = sorted(diagnoses.items(), key=lambda x: -x[1])[:8]
    sample = []
    for visit in medical_visits[:5]:
        sample.append(
            {
                "visit_date": visit.get("visit_date"),
                "hospital_name": visit.get("hospital_name"),
                "department": visit.get("department"),
                "diagnosis": visit.get("diagnosis"),
                "copay_display": visit.get("copay_display"),
            }
        )
    return {
        "medical_count": len(medical_visits),
        "period_start": min(dates) if dates else "",
        "period_end": max(dates) if dates else "",
        "top_hospitals": [name for name, _ in top_hospitals],
        "top_diagnoses": [name for name, _ in top_diagnoses],
        "sample_visits": sample,
    }


def build_ai_safe_insurance_summary(
    insured_summary: dict[str, Any] | None,
    actual_loss_products: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    products = actual_loss_products or detect_actual_loss_products(insured_summary)
    safe_products = []
    for product in products[:15]:
        safe_products.append(
            {
                "company_name": _resolve_related_company_name(
                    product, product.get("_group_company_name", "")
                ),
                "insurance_name": product.get("insurance_name"),
                "policy_number_hid": product.get("policy_number_hid"),
                "contract_status": product.get("contract_status"),
                "coverages": [
                    {
                        "coverage_name": c.get("coverage_name"),
                        "agreement_type": c.get("agreement_type"),
                        "period_display": c.get("period_display"),
                        "status": c.get("status"),
                    }
                    for c in (product.get("actual_loss_coverages") or [])[:8]
                    if isinstance(c, dict)
                ],
            }
        )
    return {"actual_loss_product_count": len(products), "products": safe_products}


def _ai_sanitize_input(payload: Any) -> Any:
    """민감 키 제거."""
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(
                token in lowered
                for token in (
                    "identity",
                    "주민",
                    "phone",
                    "email",
                    "password",
                    "token",
                    "connectedid",
                    "raw_response",
                    "raw",
                )
            ):
                continue
            if lowered in ("policy_number", "policy_no") or (
                "policy_number" in lowered and "hid" not in lowered
            ):
                continue
            out[key] = _ai_sanitize_input(value)
        return out
    if isinstance(payload, list):
        return [_ai_sanitize_input(item) for item in payload[:30]]
    return payload


def _try_openai_enhance_result(
    result: dict[str, Any],
    medical_visits: list[dict[str, Any]],
    insured_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        result["fallback_reason"] = result.get("fallback_reason") or "openai_key_missing"
        return result
    model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    try:
        import requests

        safe_payload = _ai_sanitize_input(
            {
                "medical": build_ai_safe_medical_summary(medical_visits),
                "insurance": build_ai_safe_insurance_summary(insured_summary),
                "high_sample": (result.get("categories") or {}).get("high_potential", [])[:3],
            }
        )
        body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "보험 청구 검토 문구만 보강한다. 후보 건수를 줄이거나 합치지 말 것. "
                        "지급 확정 표현 금지. JSON만 반환."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(safe_payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        }
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=25,
        )
        if resp.status_code >= 400:
            result["fallback_reason"] = f"openai_http_{resp.status_code}"
            return result
        result["engine"] = f"openai:{model} + rule 기반"
        result["fallback_reason"] = None
    except Exception as exc:
        logger.warning("openai enhance skipped: %s", type(exc).__name__)
        result["fallback_reason"] = f"openai_error:{type(exc).__name__}"
    return result


def execute_ai_claim_analysis(
    entry: dict[str, Any],
    *,
    restore_medical_fn: Any,
    restore_insurance_fn: Any,
    use_openai: bool = True,
) -> dict[str, Any]:
    """FLOW_STORE·DB 저장본으로 분석 실행."""
    data = build_integrated_data(
        entry,
        restore_medical_fn=restore_medical_fn,
        restore_insurance_fn=restore_insurance_fn,
    )
    medical_visits = data.get("medical_visits") or []
    insured_summary = data.get("insured_summary") or {}
    overview = data.get("insurance_overview") or {}
    product_count = int(overview.get("product_count") or 0)
    medical_counts = _medical_counts_from_entry(entry)
    medical_count = int(medical_counts.get("basic") or 0) + int(
        medical_counts.get("detail") or 0
    ) + int(medical_counts.get("prescribe") or 0)
    if medical_count <= 0:
        medical_count = len(medical_visits)

    if medical_count <= 0 and product_count <= 0:
        return {
            "error": "no_data",
            "message": "저장된 진료내역 또는 보험가입이력이 없습니다.",
        }

    result = run_ai_claim_analysis(
        medical_visits,
        insured_summary,
        product_count=product_count,
    )
    if isinstance(result.get("input_summary"), dict):
        result["input_summary"]["medical_count"] = medical_count
    if use_openai:
        result = _try_openai_enhance_result(result, medical_visits, insured_summary)
    entry["ai_analysis_result"] = result
    return result


def run_rule_based_claim_analysis(
    medical_visits: list[dict[str, Any]],
    *,
    has_actual_loss: bool,
    product_count: int,
    max_candidates: int = 0,
    insured_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """하위 호환 — ai_analysis_result 형식으로 반환."""
    _ = has_actual_loss
    _ = max_candidates
    return run_ai_claim_analysis(
        medical_visits,
        insured_summary,
        product_count=product_count,
    )


def build_integrated_data(
    entry: dict[str, Any],
    *,
    restore_medical_fn: Any,
    restore_insurance_fn: Any,
) -> dict[str, Any]:
    """FLOW_STORE → DB 순으로 진료·보험 데이터 통합."""
    customer = entry.get("customer") if isinstance(entry.get("customer"), dict) else {}
    medical_db: dict[str, Any] | None = None
    insurance_db: dict[str, Any] | None = None
    if is_search_hash_secret_configured() and customer:
        medical_db = load_latest_medical_records(customer)
        insurance_db = load_latest_insurance_records(customer)

    flow_id = str(entry.get("flow_id") or "").strip()
    if entry.get("medical_status") != "completed" and medical_db and restore_medical_fn:
        restore_medical_fn(flow_id or str(medical_db.get("flow_id") or ""), entry)

    if entry.get("insurance_status") != "completed" and insurance_db and restore_insurance_fn:
        restore_insurance_fn(entry)

    medical_counts = _medical_counts_from_entry(entry)
    medical_visits = _medical_visits_from_entry(entry)

    insurance_summary_raw: dict[str, Any] = {}
    normalized_payload: Any = None
    raw_response: Any = None
    if insurance_db:
        insurance_summary_raw = insurance_db.get("summary") or {}
        normalized_payload = insurance_db.get("normalized_payload") or insurance_db.get(
            "normalized_records"
        )
        raw_response = insurance_db.get("raw_response")

    entry_insured = entry.get("insured_summary")
    if isinstance(entry_insured, dict) and entry_insured.get("company_groups"):
        insured_summary = entry_insured
        display: dict[str, Any] = {
            "insured_summary": insured_summary,
            "insurance_summary": _insurance_overview_from_insured(insured_summary),
        }
    else:
        display = resolve_stored_insurance_for_display(
            normalized_payload,
            insurance_summary_raw,
            str(customer.get("name") or ""),
            raw_response=raw_response,
        )
        insured_summary = display.get("insured_summary") or {}

    overview = _insurance_overview_from_insured(insured_summary)
    has_actual_loss = _has_actual_loss_coverage(
        normalized_payload,
        insurance_summary_raw,
        insured_summary=insured_summary,
    )
    summary_debug = insured_summary.get("debug") if isinstance(insured_summary, dict) else {}

    return {
        "customer": customer,
        "medical_counts": medical_counts,
        "medical_visits": medical_visits,
        "insurance_overview": overview,
        "insured_summary": insured_summary,
        "insurance_summary_debug": summary_debug,
        "insurance_display": display,
        "insurance_db": insurance_db,
        "medical_db": medical_db,
        "has_actual_loss": has_actual_loss,
    }


def build_analysis_ready_context(
    entry: dict[str, Any],
    flow_id: str,
    *,
    debug: bool,
    restore_medical_fn: Any,
    restore_insurance_fn: Any,
) -> dict[str, Any]:
    data = build_integrated_data(
        entry,
        restore_medical_fn=restore_medical_fn,
        restore_insurance_fn=restore_insurance_fn,
    )
    insurance_db = data.get("insurance_db") or {}
    medical_db = data.get("medical_db") or {}
    debug_info: dict[str, Any] | None = None
    insured_summary = data.get("insured_summary") or {}
    summary_debug = data.get("insurance_summary_debug") or {}
    if debug:
        debug_info = {
            "loaded_medical_record_id": medical_db.get("record_id") or entry.get("loaded_medical_record_id") or "—",
            "loaded_insurance_record_id": insurance_db.get("record_id") or entry.get("loaded_insurance_record_id") or "—",
            "insurance_record_source": insurance_db.get("source") or entry.get("loaded_insurance_record_source") or "—",
            "raw_len": insurance_db.get("raw_len") or "—",
            "normalized_len": insurance_db.get("normalized_len") or "—",
            "summary_len": insurance_db.get("summary_len") or "—",
            "original_contract_total": summary_debug.get("original_contract_total", "—"),
            "excluded_as_other_insured_count": summary_debug.get("excluded_as_other_insured_count", "—"),
            "unknown_insured_contract_count": summary_debug.get("unknown_insured_contract_count", "—"),
            "payment_estimated_count": summary_debug.get("payment_estimated_count", "—"),
            "deduped_product_count": summary_debug.get("deduped_product_count", "—"),
            "company_inferred_count": summary_debug.get("company_inferred_count", "—"),
            "company_unknown_count": summary_debug.get("company_unknown_count", "—"),
            "company_inferred_preview": summary_debug.get("company_inferred_preview", "—"),
            "company_unknown_preview": summary_debug.get("company_unknown_preview", "—"),
            "company_matched_keyword_counts": summary_debug.get(
                "company_matched_keyword_counts", "—"
            ),
        }
    return {
        "flow_id": flow_id,
        "current_step": 5,
        "debug_panel": debug,
        "analysis_debug": debug_info,
        "customer_display": data.get("customer"),
        "medical_counts": data["medical_counts"],
        "insurance_overview": data["insurance_overview"],
        "insured_summary": insured_summary,
        "insurance_summary_debug": summary_debug,
        "has_medical": bool(data["medical_counts"].get("basic")),
        "has_insurance": bool((insured_summary.get("company_groups") or [])),
        "insurance_source_note": (
            "저장된 보험가입이력 기준입니다."
            if str(insurance_db.get("source") or "").startswith("credit4u")
            or entry.get("insurance_source") == "saved_imported"
            else ""
        ),
    }


def build_ai_analysis_context(
    entry: dict[str, Any],
    flow_id: str,
    *,
    debug: bool,
    restore_medical_fn: Any,
    restore_insurance_fn: Any,
) -> dict[str, Any]:
    data = build_integrated_data(
        entry,
        restore_medical_fn=restore_medical_fn,
        restore_insurance_fn=restore_insurance_fn,
    )
    overview = data["insurance_overview"]
    medical_counts = data.get("medical_counts") or {}
    medical_count = int(medical_counts.get("basic") or 0) + int(
        medical_counts.get("detail") or 0
    ) + int(medical_counts.get("prescribe") or 0)
    if medical_count <= 0:
        medical_count = len(data.get("medical_visits") or [])
    product_count = int(overview.get("product_count") or 0)
    has_data = medical_count > 0 or product_count > 0

    ai_result = entry.get("ai_analysis_result")
    if not isinstance(ai_result, dict) or not ai_result.get("categories"):
        ai_result = None

    insurance_db = data.get("insurance_db") or {}
    medical_db = data.get("medical_db") or {}
    debug_info: dict[str, Any] | None = None
    if debug:
        totals = (ai_result or {}).get("totals") or {}
        debug_info = {
            "loaded_medical_record_id": medical_db.get("record_id") or "—",
            "loaded_insurance_record_id": insurance_db.get("record_id") or "—",
            "insurance_record_source": insurance_db.get("source") or "—",
            "raw_len": insurance_db.get("raw_len") or "—",
            "normalized_len": insurance_db.get("normalized_len") or "—",
            "summary_len": insurance_db.get("summary_len") or "—",
            "ai_candidate_count": totals.get("total_candidate_count", "—"),
            "ai_total_estimated_amount": totals.get("total_estimated_display", "—"),
            "detected_actual_loss_count": len(
                (ai_result or {}).get("detected_actual_loss_products") or []
            ),
            "fallback_reason": (ai_result or {}).get("fallback_reason", "—"),
        }

    return {
        "flow_id": flow_id,
        "current_step": 6,
        "debug_panel": debug,
        "analysis_debug": debug_info,
        "ai_analysis_result": ai_result,
        "has_analysis": bool(ai_result),
        "has_data": has_data,
        "no_data_message": (
            "저장된 진료내역 또는 보험가입이력이 없습니다."
            if not has_data
            else ""
        ),
        "input_summary": {
            "medical_count": medical_count,
            "insurance_product_count": product_count,
        },
        "default_category": (ai_result or {}).get("default_category") or "high_potential",
    }
