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
_AI_ENGINE_DISPLAY = "RedRibbon Rule Engine"
_AI_ENGINE_DISPLAY_ENHANCED = "RedRibbon Rule Engine + AI 보강"
_DEFAULT_NEXT_ACTION = (
    "진료비 영수증, 진료비 세부내역서, 처방전 또는 약제비 영수증을 확인하세요."
)
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
_CATEGORY_TOTAL_PREFIX: tuple[tuple[str, str], ...] = (
    ("high_potential", "high"),
    ("need_review", "review"),
    ("need_documents", "documents"),
    ("low_potential", "low"),
)

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

_SELF_PAY_FIELD_KEYS = (
    "self_pay",
    "patient_pay",
    "paid_amount",
    "copay_amount",
    "patient_paid_amount",
    "resSelfPayAmt",
    "resSelfPayAmount",
    "resMyPayment",
    "resPatientPayAmount",
    "resUserPayAmount",
    "resPaidAmt",
    "resCopayAmt",
    "resDeductibleAmt",
    "resPaidAmount",
    "내가낸의료비",
    "본인부담금",
)

_INSURER_PAY_FIELD_KEYS = (
    "insurer_pay",
    "national_health_pay",
    "resInsuranceBenefitAmount",
    "resInsurerPayAmt",
    "resNationalHealthPay",
    "resPublicChargeAmt",
    "공단부담금",
    "건강보험",
)

_TOTAL_COST_FIELD_KEYS = (
    "total_amount",
    "resTotalMedicalExpense",
    "resTotalMedicalAmt",
    "resMedicalExpenses",
    "resTotalTreatAmt",
    "resTreatAmt",
    "resMedicalTotalAmt",
    "resClaimAmt",
    "total_cost",
    "총진료비",
)

# 하위 호환
_COPAY_FIELD_KEYS = _SELF_PAY_FIELD_KEYS

_HOSPITAL_TYPE_PHARMACY_KEYWORDS = (
    "약국",
    "pharmacy",
    "조제",
    "약제",
    "처방조제",
    "처방전",
)
_HOSPITAL_TYPE_HOSPITAL_KEYWORDS = (
    "병원",
    "의원",
    "의료원",
    "클리닉",
    "내과",
    "외과",
    "정형외과",
    "응급의학과",
    "소아과",
    "산부인과",
    "이비인후과",
    "안과",
    "피부과",
    "신경과",
    "정신건강",
    "재활의학과",
    "영상의학과",
    "치과",
    "한방",
)

_DEDUCTIBLE_THRESHOLD_HOSPITAL = 10_000
_DEDUCTIBLE_THRESHOLD_PHARMACY = 8_000
_DEDUCTIBLE_THRESHOLD_INPATIENT = 0

_AMOUNT_CALCULATION_MEMO = (
    "실손 세대·약관에 따라 실제 공제금액은 달라질 수 있습니다."
)
_RELATED_INSURANCE_PLACEHOLDER = "관련 실손 담보 확인 필요"

_OUTPATIENT_COVERAGE_KEYWORDS = (
    "질병통원의료비",
    "상해통원의료비",
    "통원의료비",
    "외래의료비",
)
_PHARMACY_COVERAGE_KEYWORDS = (
    "처방조제비",
    "약제비",
)
_INPATIENT_COVERAGE_KEYWORDS = (
    "입원의료비",
    "질병입원의료비",
    "상해입원의료비",
)
_INPATIENT_ONLY_COVERAGE_KEYWORDS = (
    "입원의료비",
    "질병입원의료비",
    "상해입원의료비",
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


def clean_display_text(value: Any) -> str:
    """화면 표시용 — '+'를 공백으로 치환."""
    text = str(value or "").strip()
    if not text or text == "—":
        return text or "—"
    text = text.replace("+", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or "—"


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


def _parse_first_positive_amount(raw: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        amount = _parse_amount(raw.get(key))
        if amount > 0:
            return amount
    return 0


def classify_hospital_type(visit: dict[str, Any]) -> str:
    """병원/약국/unknown 분류."""
    hospital = str(visit.get("hospital_name") or "")
    diagnosis = str(visit.get("diagnosis") or "")
    department = str(visit.get("department") or "")
    combined = f"{hospital} {diagnosis} {department}"
    if any(token in combined for token in _HOSPITAL_TYPE_PHARMACY_KEYWORDS):
        return "pharmacy"
    if any(token in combined for token in _HOSPITAL_TYPE_HOSPITAL_KEYWORDS):
        return "hospital"
    return "unknown"


def classify_visit_type(visit: dict[str, Any], hospital_type: str) -> str:
    """outpatient / inpatient / pharmacy / unknown."""
    if hospital_type == "pharmacy":
        return "pharmacy"
    combined = " ".join(
        str(visit.get(key) or "")
        for key in ("hospital_name", "department", "diagnosis")
    )
    if any(token in combined for token in ("입원", "입퇴원", "병동")):
        return "inpatient"
    if hospital_type == "hospital":
        return "outpatient"
    if any(token in combined for token in _HOSPITAL_TYPE_PHARMACY_KEYWORDS):
        return "pharmacy"
    return "unknown"


def build_visit_group_key(visit: dict[str, Any]) -> str:
    """동일 날짜 병원+약국 묶음용 키(현재는 묶지 않음)."""
    date_digits = re.sub(r"\D", "", str(visit.get("visit_date") or ""))[:8]
    code = re.sub(r"\W", "", str(visit.get("diagnosis_code") or ""))[:12]
    hospital_type = str(visit.get("hospital_type") or classify_hospital_type(visit))
    institution = re.sub(r"\s+", "", str(visit.get("hospital_name") or ""))[:40]
    return f"{date_digits}|{code}|{hospital_type}|{institution}"


def _visit_care_context(visit: dict[str, Any]) -> tuple[str, str, bool, bool, bool]:
    """hospital_type, visit_type, is_inpatient, is_pharmacy, is_outpatient."""
    hospital_type = str(visit.get("hospital_type") or classify_hospital_type(visit))
    visit_type = str(visit.get("visit_type") or classify_visit_type(visit, hospital_type))
    combined = " ".join(
        str(visit.get(key) or "")
        for key in ("hospital_name", "department", "diagnosis")
    )
    is_inpatient = visit_type == "inpatient" or any(
        token in combined for token in ("입원", "입퇴원", "병동")
    )
    is_pharmacy = hospital_type == "pharmacy" or visit_type == "pharmacy"
    is_outpatient = not is_inpatient and not is_pharmacy
    return hospital_type, visit_type, is_inpatient, is_pharmacy, is_outpatient


def _base_deductible_amount(visit: dict[str, Any]) -> tuple[int, str]:
    """기본 공제금액과 구분 라벨."""
    _, visit_type, is_inpatient, is_pharmacy, _ = _visit_care_context(visit)
    if is_inpatient:
        return _DEDUCTIBLE_THRESHOLD_INPATIENT, "입원(약관 확인 필요)"
    if is_pharmacy or visit_type == "pharmacy":
        return _DEDUCTIBLE_THRESHOLD_PHARMACY, "약국"
    return _DEDUCTIBLE_THRESHOLD_HOSPITAL, "병원·외래"


def estimate_actual_loss_payable_amount(
    record: dict[str, Any],
    product_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """본인부담금 − 기본 공제금액 = 청구가능금액(추정)."""
    _ = product_info
    self_pay = int(
        record.get("self_pay_amount") or record.get("copay_amount") or 0
    )
    deductible, place_label = _base_deductible_amount(record)
    _, _, is_inpatient, _, _ = _visit_care_context(record)

    if self_pay <= 0:
        return {
            "base_self_pay_amount": 0,
            "deductible_amount": deductible,
            "estimated_payable_amount": 0,
            "deductible_rule": "본인부담금 미확인",
            "calculation_note": "본인부담금 영수증 확인 필요",
            "deductible_applied": False,
            "below_deductible_threshold": False,
        }

    estimated = max(self_pay - deductible, 0)
    if is_inpatient and deductible == 0:
        deductible_rule = "입원 — 기본 공제 0원(약관 확인 필요)"
    else:
        deductible_rule = f"{place_label} 기본 공제 {_format_amount_krw(deductible)} 차감"

    return {
        "base_self_pay_amount": self_pay,
        "deductible_amount": deductible,
        "estimated_payable_amount": estimated,
        "deductible_rule": deductible_rule,
        "calculation_note": _AMOUNT_CALCULATION_MEMO,
        "deductible_applied": deductible > 0,
        "below_deductible_threshold": estimated <= 0 and self_pay > 0,
    }


def normalize_medical_visit_row(raw: Any) -> dict[str, Any]:
    """진료 1건 — 총진료비/공단부담금/본인부담금 분리 파싱."""
    if not isinstance(raw, dict):
        return {
            "visit_date": "—",
            "hospital_name": "—",
            "department": "—",
            "diagnosis": "—",
            "diagnosis_code": "—",
            "self_pay_amount": 0,
            "copay_amount": 0,
            "copay_display": "0원",
            "insurer_pay_amount": 0,
            "insurer_pay_display": "—",
            "total_amount": 0,
            "total_cost_amount": 0,
            "total_cost_display": "—",
            "hospital_type": "unknown",
            "visit_type": "unknown",
            "group_key": "",
        }
    self_pay = _parse_first_positive_amount(raw, _SELF_PAY_FIELD_KEYS)
    insurer_pay = _parse_first_positive_amount(raw, _INSURER_PAY_FIELD_KEYS)
    total_cost = _parse_first_positive_amount(raw, _TOTAL_COST_FIELD_KEYS)
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
    row = {
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
        "self_pay_amount": self_pay,
        "copay_amount": self_pay,
        "copay_display": _format_amount_krw(self_pay),
        "insurer_pay_amount": insurer_pay,
        "insurer_pay_display": (
            _format_amount_krw(insurer_pay) if insurer_pay > 0 else "—"
        ),
        "total_amount": total_cost,
        "total_cost_amount": total_cost,
        "total_cost_display": _format_amount_krw(total_cost) if total_cost > 0 else "—",
    }
    row["hospital_type"] = classify_hospital_type(row)
    row["visit_type"] = classify_visit_type(row, row["hospital_type"])
    row["group_key"] = build_visit_group_key(row)
    return row


def _visit_date_int(visit: dict[str, Any]) -> int:
    digits = re.sub(r"\D", "", str(visit.get("visit_date") or ""))[:8]
    if len(digits) == 8 and digits.isdigit():
        try:
            return int(digits)
        except ValueError:
            return 0
    return 0


def build_medical_preview(
    medical_visits: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """5단계 진료내역 펼침용 — 청구 후보(본인부담금) 우선, 최근 진료 순."""
    visits = [v for v in medical_visits if isinstance(v, dict)]
    total = len(visits)
    ordered = sorted(
        visits,
        key=lambda v: (
            0 if int(v.get("copay_amount") or 0) > 0 else 1,
            -_visit_date_int(v),
        ),
    )
    items = ordered[: max(0, limit)]
    return {
        "total_count": total,
        "display_count": len(items),
        "limit": limit,
        "truncated": total > len(items),
        "items": items,
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


def _is_excluded_insurance_product(product: dict[str, Any]) -> bool:
    """지급내역 기반 추정·actual_loss_estimate 상품 제외."""
    if str(product.get("source_type") or "").strip() == "actual_loss_estimate":
        return True
    status = str(product.get("contract_status") or "").strip()
    if "지급내역 기반 추정" in status:
        return True
    name = str(product.get("insurance_name") or "").strip()
    if "지급내역" in name and "추정" in name:
        return True
    return False


def _is_excluded_matched_coverage_label(label: str) -> bool:
    text = (label or "").strip()
    if not text:
        return True
    if text in ("—", "-", "실손성 담보(추정)"):
        return True
    if "지급 사유 미상" in text:
        return True
    return False


def _coverage_matches_visit(coverage: dict[str, Any], visit: dict[str, Any]) -> bool:
    text = _coverage_text(coverage)
    if not text or not _is_actual_loss_coverage_text(text):
        return False
    if "지급 사유 미상" in text:
        return False

    _, _, is_inpatient, is_pharmacy, is_outpatient = _visit_care_context(visit)

    if is_inpatient:
        return any(keyword in text for keyword in _INPATIENT_COVERAGE_KEYWORDS)

    if is_pharmacy:
        return any(keyword in text for keyword in _PHARMACY_COVERAGE_KEYWORDS)

    if is_outpatient:
        if any(keyword in text for keyword in _INPATIENT_ONLY_COVERAGE_KEYWORDS):
            if not any(keyword in text for keyword in _OUTPATIENT_COVERAGE_KEYWORDS):
                return False
        return any(keyword in text for keyword in _OUTPATIENT_COVERAGE_KEYWORDS) or (
            "통원" in text or "외래" in text
        )

    return any(keyword in text for keyword in _OUTPATIENT_COVERAGE_KEYWORDS)


def _matched_coverages_for_visit(
    product: dict[str, Any], visit: dict[str, Any]
) -> list[str]:
    labels: list[str] = []
    for cov in product.get("coverages") or []:
        if not isinstance(cov, dict):
            continue
        if not _coverage_matches_visit(cov, visit):
            continue
        label = str(cov.get("coverage_name") or cov.get("agreement_type") or "").strip()
        if label and label not in labels and not _is_excluded_matched_coverage_label(label):
            labels.append(label)
    return labels


def detect_actual_loss_products(
    insured_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """피보험자 요약에서 실손성 상품·담보 목록(지급내역 추정 상품 제외)."""
    insured = insured_summary if isinstance(insured_summary, dict) else {}
    found: list[dict[str, Any]] = []
    for group in insured.get("company_groups") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("company_name") or "")
        for product in group.get("products") or []:
            if not isinstance(product, dict):
                continue
            if _is_excluded_insurance_product(product):
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


def _actual_loss_detection_reason(product: dict[str, Any]) -> str:
    name = str(product.get("insurance_name") or "").strip()
    if "실손" in name:
        return "상품명 실손"
    coverages = product.get("actual_loss_coverages") or collect_actual_loss_coverages(
        product
    )
    for cov in coverages[:3]:
        if not isinstance(cov, dict):
            continue
        label = str(cov.get("coverage_name") or cov.get("agreement_type") or "").strip()
        if label:
            return f"보명명 {label}"
    return "실손성 담보(추정)"


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


def _should_exclude_related_insurance_row(row: dict[str, Any]) -> bool:
    if row.get("is_placeholder"):
        return False
    if str(row.get("source_type") or "").strip() == "actual_loss_estimate":
        return True
    status = str(row.get("contract_status") or "").strip()
    if "지급내역 기반 추정" in status:
        return True
    name = str(row.get("insurance_name") or "").strip()
    if "지급내역" in name and "추정" in name:
        return True
    coverage = str(row.get("matched_coverage") or "").strip()
    return _is_excluded_matched_coverage_label(coverage)


def _sanitize_related_insurance_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["company_name"] = clean_display_text(
        out.get("company_name") or "보험회사 확인 필요"
    )
    out["insurance_name"] = clean_display_text(out.get("insurance_name"))
    out["contract_status"] = clean_display_text(out.get("contract_status"))
    out["matched_coverage"] = clean_display_text(out.get("matched_coverage"))
    hid = str(out.get("policy_number_hid") or "").strip()
    out["policy_number_hid"] = hid if hid and hid != "—" else "—"
    out.pop("policy_number", None)
    return out


def _sanitize_related_medical_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "visit_date": clean_display_text(row.get("visit_date")),
        "hospital_name": clean_display_text(row.get("hospital_name")),
        "department": clean_display_text(row.get("department")),
        "diagnosis": clean_display_text(row.get("diagnosis")),
        "copay_display": clean_display_text(row.get("copay_display")),
    }


def _sanitize_candidate_for_display(candidate: dict[str, Any]) -> dict[str, Any]:
    out = dict(candidate)
    for key in (
        "date",
        "hospital_name",
        "department",
        "diagnosis_name",
        "diagnosis_code",
        "reason",
        "visit_date",
        "diagnosis",
    ):
        if key in out:
            out[key] = clean_display_text(out.get(key))
    next_action = clean_display_text(out.get("next_action"))
    out["next_action"] = (
        next_action if next_action and next_action != "—" else _DEFAULT_NEXT_ACTION
    )
    reason = clean_display_text(out.get("reason"))
    out["reason"] = reason if reason and reason != "—" else "청구 검토 후보로 분류되었습니다(추정)."
    amount_label = str(out.get("amount_label") or "")
    if any(
        token in amount_label
        for token in (
            "지급 가능",
            "지급 예상",
            "지급예상",
            "지급 확정",
            "청구 후보",
        )
    ):
        self_pay = int(out.get("self_pay_amount") or 0)
        estimated = int(out.get("estimated_amount") or 0)
        out["amount_label"] = _candidate_amount_label(self_pay, estimated)
    else:
        out["amount_label"] = clean_display_text(amount_label)
    for note_key in ("deductible_note", "amount_basis"):
        if note_key in out:
            out[note_key] = clean_display_text(out.get(note_key))
    related_rows = [
        _sanitize_related_insurance_row(ins)
        for ins in (out.get("related_insurance") or [])
        if isinstance(ins, dict) and not _should_exclude_related_insurance_row(ins)
    ]
    if not related_rows:
        related_rows = build_related_insurance_placeholder()
    out["related_insurance"] = related_rows
    breakdown = out.get("amount_breakdown")
    if not isinstance(breakdown, dict) or not breakdown.get("claimable_amount_display"):
        self_pay = int(out.get("self_pay_amount") or 0)
        deductible = int(out.get("deductible_amount") or 0)
        estimated = int(out.get("estimated_amount") or max(self_pay - deductible, 0))
        out["amount_breakdown"] = {
            "self_pay_display": _format_amount_krw(self_pay) if self_pay > 0 else None,
            "deductible_amount_display": _format_amount_krw(deductible),
            "claimable_amount_display": _format_amount_krw(estimated),
            "calculation_memo": _AMOUNT_CALCULATION_MEMO,
        }
    out["related_medical"] = [
        _sanitize_related_medical_row(med)
        for med in (out.get("related_medical") or [])
        if isinstance(med, dict)
    ]
    if isinstance(out.get("required_documents"), list):
        out["required_documents"] = [
            clean_display_text(doc)
            for doc in out["required_documents"]
            if str(doc or "").strip()
        ]
    return out


def _sum_category_self_pay(candidates: list[dict[str, Any]]) -> int:
    return sum(int(c.get("self_pay_amount") or 0) for c in candidates)


def _sum_category_estimated(candidates: list[dict[str, Any]]) -> int:
    return sum(int(c.get("estimated_amount") or 0) for c in candidates)


def compute_analysis_totals(categories: dict[str, Any]) -> dict[str, Any]:
    """카테고리별 원금(본인부담)·공제 후 추정액 합산."""
    all_candidates: list[dict[str, Any]] = []
    for key in _CATEGORY_KEYS:
        all_candidates.extend(
            c for c in (categories.get(key) or []) if isinstance(c, dict)
        )
    total_base = _sum_category_self_pay(all_candidates)
    total_estimated = _sum_category_estimated(all_candidates)
    totals: dict[str, Any] = {
        "total_candidate_count": len(all_candidates),
        "total_candidate_base_amount": total_base,
        "total_candidate_base_display": _format_amount_krw(total_base),
        "total_candidate_base_label": "청구 후보 원금",
        "total_estimated_amount": total_estimated,
        "total_estimated_display": _format_amount_krw(total_estimated),
        "total_estimated_label": "공제 후 추정액",
        "total_payable_note": _AMOUNT_CALCULATION_MEMO,
        "amount_summary_mode": (
            "partial_deductible"
            if total_estimated < total_base
            else "before_deductible"
        ),
    }
    for cat_key, prefix in _CATEGORY_TOTAL_PREFIX:
        items = [c for c in (categories.get(cat_key) or []) if isinstance(c, dict)]
        base = _sum_category_self_pay(items)
        est = _sum_category_estimated(items)
        totals[f"{prefix}_count"] = len(items)
        totals[f"{prefix}_base_amount"] = base
        totals[f"{prefix}_estimated_amount"] = est
        totals[f"{prefix}_base_display"] = _format_amount_krw(base)
        totals[f"{prefix}_estimated_display"] = _format_amount_krw(est)
        totals[f"{prefix}_amount"] = est
    return totals


def build_category_tabs_from_totals(totals: dict[str, Any]) -> list[dict[str, Any]]:
    """카테고리 탭 — 공제 후 추정액(표시·합산)과 원금을 분리."""
    tabs: list[dict[str, Any]] = []
    for cat_key, prefix in _CATEGORY_TOTAL_PREFIX:
        base = int(totals.get(f"{prefix}_base_amount") or 0)
        est = int(
            totals.get(f"{prefix}_estimated_amount")
            or totals.get(f"{prefix}_amount")
            or 0
        )
        count = int(totals.get(f"{prefix}_count") or 0)
        base_display = str(
            totals.get(f"{prefix}_base_display") or _format_amount_krw(base)
        )
        est_display = str(
            totals.get(f"{prefix}_estimated_display") or _format_amount_krw(est)
        )
        tabs.append(
            {
                "id": cat_key,
                "label": _CATEGORY_LABELS[cat_key],
                "count": count,
                "base_amount": base,
                "estimated_amount": est,
                "base_amount_display": base_display,
                "estimated_amount_display": est_display,
                "amount": est,
                "amount_display": est_display,
            }
        )
    return tabs


def sanitize_ai_analysis_result(result: dict[str, Any]) -> dict[str, Any]:
    """ai_analysis_result 화면 표시용 정리."""
    out = dict(result)
    categories = out.get("categories")
    if isinstance(categories, dict):
        sanitized_categories: dict[str, list[dict[str, Any]]] = {}
        for key, items in categories.items():
            sanitized_categories[key] = [
                _sanitize_candidate_for_display(item)
                for item in (items or [])
                if isinstance(item, dict)
            ]
        reconciled = _reconcile_category_buckets(sanitized_categories)
        out["categories"] = reconciled
        out["totals"] = compute_analysis_totals(reconciled)
        out["category_tabs"] = build_category_tabs_from_totals(out["totals"])
        out["classification_debug"] = build_classification_debug(reconciled)
    detected = out.get("detected_actual_loss_products")
    if isinstance(detected, list):
        cleaned_detected: list[dict[str, Any]] = []
        for row in detected:
            if not isinstance(row, dict):
                continue
            cleaned_detected.append(
                {
                    "company_name": clean_display_text(
                        row.get("company_name") or "보험회사 확인 필요"
                    ),
                    "insurance_name": clean_display_text(row.get("insurance_name")),
                    "policy_number_hid": str(row.get("policy_number_hid") or "—"),
                    "detection_reason": clean_display_text(
                        row.get("detection_reason") or "—"
                    ),
                }
            )
        out["detected_actual_loss_products"] = cleaned_detected
    engine_raw = str(out.get("engine") or "")
    fallback = str(out.get("fallback_reason") or "").strip()
    if "openai:" in engine_raw and not fallback:
        model_part = engine_raw.split("openai:", 1)[-1].split("+", 1)[0].strip()
        out["engine_display"] = _AI_ENGINE_DISPLAY_ENHANCED
        out["engine_debug"] = f"openai:{model_part}" if model_part else engine_raw
        out["ai_enhance_note"] = None
    else:
        out["engine_display"] = _AI_ENGINE_DISPLAY
        out["engine_debug"] = engine_raw or _AI_ENGINE_RULE
        if fallback and fallback != "openai_key_missing":
            out["ai_enhance_note"] = "AI 보강 실패로 rule 분석 결과를 표시합니다."
        else:
            out["ai_enhance_note"] = None
    return out


def build_related_insurance_for_visit(
    actual_loss_products: list[dict[str, Any]],
    visit: dict[str, Any],
) -> list[dict[str, Any]]:
    """진료 유형에 맞는보만 매칭한 관련 보험."""
    items: list[dict[str, Any]] = []
    for product in actual_loss_products:
        if _is_excluded_insurance_product(product):
            continue
        matched_labels = _matched_coverages_for_visit(product, visit)
        if not matched_labels:
            continue
        matched_coverage = ", ".join(matched_labels[:5])
        if _is_excluded_matched_coverage_label(matched_coverage):
            continue
        items.append(
            {
                "company_name": _resolve_related_company_name(
                    product, str(product.get("_group_company_name") or "")
                ),
                "insurance_name": str(product.get("insurance_name") or "—"),
                "policy_number_hid": str(product.get("policy_number_hid") or "—"),
                "contract_status": str(product.get("contract_status") or "—"),
                "matched_coverage": matched_coverage,
            }
        )
    return items


def build_related_insurance_placeholder() -> list[dict[str, Any]]:
    return [
        {
            "company_name": _RELATED_INSURANCE_PLACEHOLDER,
            "insurance_name": "—",
            "policy_number_hid": "—",
            "contract_status": "—",
            "matched_coverage": "—",
            "is_placeholder": True,
        }
    ]


def build_related_insurance_list(
    actual_loss_products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """하위 호환 — 방문 맥락 없이 상품 전체(담보 라벨만)."""
    items: list[dict[str, Any]] = []
    for product in actual_loss_products:
        if _is_excluded_insurance_product(product):
            continue
        matched = []
        for cov in product.get("coverages") or []:
            if not isinstance(cov, dict):
                continue
            label = str(cov.get("coverage_name") or cov.get("agreement_type") or "").strip()
            if label and label not in matched and not _is_excluded_matched_coverage_label(label):
                matched.append(label)
        if not matched:
            continue
        items.append(
            {
                "company_name": _resolve_related_company_name(
                    product, str(product.get("_group_company_name") or "")
                ),
                "insurance_name": str(product.get("insurance_name") or "—"),
                "policy_number_hid": str(product.get("policy_number_hid") or "—"),
                "contract_status": str(product.get("contract_status") or "—"),
                "matched_coverage": ", ".join(matched[:5]),
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


def _visit_self_pay_amount(
    visit: dict[str, Any], payable: dict[str, Any] | None = None
) -> int:
    payable = payable if isinstance(payable, dict) else {}
    return int(
        payable.get("base_self_pay_amount")
        or visit.get("self_pay_amount")
        or visit.get("copay_amount")
        or 0
    )


def _visit_has_self_pay_field(visit: dict[str, Any]) -> bool:
    for key in ("self_pay_amount", "copay_amount"):
        if key not in visit:
            continue
        raw = visit.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            return True
        if str(raw).strip() not in ("", "—", "-"):
            return True
    return False


def _has_matched_actual_loss_coverage(related_insurance: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(row, dict) and not row.get("is_placeholder")
        for row in (related_insurance or [])
    )


def _visit_combined_text(visit: dict[str, Any]) -> tuple[str, str, str, str, str]:
    hospital = str(visit.get("hospital_name") or "").strip()
    diagnosis = str(visit.get("diagnosis") or "").strip()
    department = str(visit.get("department") or "").strip()
    hospital_type = str(visit.get("hospital_type") or classify_hospital_type(visit))
    combined = f"{hospital} {department} {diagnosis}"
    return hospital, diagnosis, department, hospital_type, combined


def _is_high_priority_visit(
    visit: dict[str, Any], *, self_pay: int, combined: str, department: str, diagnosis: str
) -> bool:
    if "응급" in combined or "응급실" in combined:
        return True
    if "정형외과" in department and any(
        token in diagnosis for token in ("상해", "골절", "탈구", "염좌", "손상")
    ):
        return True
    if self_pay >= 50_000:
        return True
    exam_tokens = ("MRI", "CT", "초음파", "조직검사", "내시경", "PET")
    return any(token in combined for token in exam_tokens)


def _documents_category_if_needed(
    combined: str,
) -> tuple[str, str, str, list[str]] | None:
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
    if not any(token in combined for token in docs_keywords):
        return None
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


def _enforce_candidate_category(
    category: str,
    *,
    estimated_amount: int,
    self_pay_amount: int,
) -> str:
    """공제 후 금액이 있으면 low_potential 금지."""
    if estimated_amount > 0 and category == "low_potential":
        return "need_review"
    if estimated_amount > 0 and category not in (
        "high_potential",
        "need_review",
        "need_documents",
    ):
        return "need_review"
    return category


def _classify_when_estimated_positive(
    visit: dict[str, Any],
    *,
    self_pay: int,
    has_matched_coverage: bool,
    hospital: str,
    diagnosis: str,
    department: str,
    hospital_type: str,
    combined: str,
) -> tuple[str, str, str, list[str]]:
    """estimated_amount > 0 — high_potential / need_review / need_documents만."""
    required_docs: list[str] = []

    if not hospital or hospital == "—":
        return (
            "need_review",
            "병원명 정보가 부족해 추가 검토가 필요합니다.",
            "진료기관명을 보강한 뒤 청구 검토를 진행해 주세요.",
            required_docs,
        )
    if not diagnosis or diagnosis == "—":
        return (
            "need_documents",
            "상병·진료명 정보 보강 후 청구 검토를 권장합니다.",
            "진단서·진료확인서 등 서류를 준비해 주세요.",
            ["진단서", "진료확인서"],
        )
    if not has_matched_coverage:
        return (
            "need_review",
            "실손 담보는 있으나 이 진료와 직접 매칭된 담보 확인이 필요합니다(추정).",
            "관련 실손 담보·약관을 확인해 주세요.",
            required_docs,
        )

    docs_result = _documents_category_if_needed(combined)
    if docs_result and not _is_high_priority_visit(
        visit, self_pay=self_pay, combined=combined, department=department, diagnosis=diagnosis
    ):
        return docs_result

    if _is_high_priority_visit(
        visit, self_pay=self_pay, combined=combined, department=department, diagnosis=diagnosis
    ):
        if "응급" in combined or "응급실" in combined:
            return (
                "high_potential",
                "응급 진료로 실손 청구 검토 후보 가능성이 있습니다(추정).",
                "응급실 진료기록·영수증을 확인해 주세요.",
                ["응급실 진료기록"],
            )
        if "정형외과" in department:
            return (
                "high_potential",
                "정형외과 상해성 질환으로 실손 청구 검토 후보 가능성이 있습니다(추정).",
                "초진·외래 영수증과 상해 관련 소견을 확인해 주세요.",
                [],
            )
        if self_pay >= 50_000:
            return (
                "high_potential",
                "본인부담금 규모가 커 실손 청구 검토 후보로 우선 검토할 수 있습니다(추정).",
                "진료비 영수증·세부내역을 준비해 주세요.",
                [],
            )
        return (
            "high_potential",
            "검사·시술성 진료로 실손 청구 검토 후보 가능성이 있습니다(추정).",
            "검사결과·영수증을 확인해 주세요.",
            ["검사결과지"],
        )

    if docs_result:
        return docs_result

    if any(token in diagnosis for token in ("감기", "비염", "기관지", "몸살")):
        return (
            "need_review",
            "단순 호흡기 질환으로 소액·반복 청구 여부를 검토해야 합니다(추정).",
            "통원 횟수·약관 면책 조항을 확인해 주세요.",
            [],
        )
    if hospital_type == "pharmacy" or "약국" in hospital or "조제" in diagnosis:
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
    return (
        "need_review",
        "공제 후 청구가능금액(추정)이 있어 실손 청구 검토가 필요합니다(추정).",
        "영수증·진료내역·담보를 대조해 주세요.",
        [],
    )


def _classify_visit_candidate(
    visit: dict[str, Any],
    *,
    has_actual_loss: bool,
    related_insurance: list[dict[str, Any]],
    payable: dict[str, Any] | None = None,
) -> tuple[str, str, str, list[str]]:
    """금액(공제 후 추정액) 확정 후 카테고리 결정."""
    payable = payable if isinstance(payable, dict) else {}
    self_pay = _visit_self_pay_amount(visit, payable)
    estimated = int(payable.get("estimated_payable_amount") or 0)
    has_matched_coverage = _has_matched_actual_loss_coverage(related_insurance)
    hospital, diagnosis, department, hospital_type, combined = _visit_combined_text(visit)
    required_docs: list[str] = []

    if not _visit_has_self_pay_field(visit) and self_pay <= 0:
        return (
            "low_potential",
            "본인부담금 정보가 없어 청구 검토 후보 추정이 어렵습니다.",
            "진료비·본인부담금 영수증을 확인해 주세요.",
            required_docs,
        )
    if self_pay <= 0:
        return (
            "low_potential",
            "본인부담금이 확인되지 않아 청구 검토 후보 추정이 어렵습니다.",
            "진료비·본인부담금 영수증을 확인해 주세요.",
            required_docs,
        )

    if estimated > 0:
        if not has_actual_loss:
            return (
                "need_review",
                "공제 후 청구가능금액(추정)이 있으나 실손 담보 확인이 필요합니다(추정).",
                "보험가입이력에서 실손 담보를 확인해 주세요.",
                required_docs,
            )
        return _classify_when_estimated_positive(
            visit,
            self_pay=self_pay,
            has_matched_coverage=has_matched_coverage,
            hospital=hospital,
            diagnosis=diagnosis,
            department=department,
            hospital_type=hospital_type,
            combined=combined,
        )

    if not has_actual_loss:
        return (
            "low_potential",
            "실손성 상품·담보가 확인되지 않아 청구 검토 가능성이 낮습니다(추정).",
            "보험가입이력에서 실손 담보를 다시 확인해 주세요.",
            required_docs,
        )

    if payable.get("below_deductible_threshold") or (
        self_pay > 0 and estimated == 0
    ):
        return (
            "need_review",
            "기본 공제금액 차감 후 청구가능금액(추정)이 0원입니다. 약관·공제 조건 확인이 필요합니다(추정).",
            "약관상 공제금액·통원/조제 한도를 확인해 주세요.",
            required_docs,
        )

    if not has_matched_coverage:
        return (
            "low_potential",
            "관련 실손 담보 확인이 어려워 청구 검토 가능성이 낮습니다(추정).",
            "보험가입이력·약관에서 담보를 확인해 주세요.",
            required_docs,
        )

    return (
        "low_potential",
        "청구가능금액(추정)이 0원이며 공제 이하 등으로 청구 검토 우선순위가 낮습니다(추정).",
        "약관·면책·공제 조건을 확인해 주세요.",
        required_docs,
    )


def _reconcile_category_buckets(
    categories: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """저장본·분류 오류: low_potential에 공제 후 금액이 있으면 재배치."""
    reconciled: dict[str, list[dict[str, Any]]] = {key: [] for key in _CATEGORY_KEYS}
    for bucket_key in _CATEGORY_KEYS:
        for item in categories.get(bucket_key) or []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            estimated = int(row.get("estimated_amount") or 0)
            category = str(row.get("category") or bucket_key)
            category = _enforce_candidate_category(
                category,
                estimated_amount=estimated,
                self_pay_amount=int(row.get("self_pay_amount") or 0),
            )
            if estimated > 0 and category == "low_potential":
                category = "need_review"
            if category not in _CATEGORY_KEYS:
                category = "need_review" if estimated > 0 else "low_potential"
            row["category"] = category
            row["category_label"] = _CATEGORY_LABELS[category]
            reconciled[category].append(row)
    return reconciled


def build_classification_debug(
    categories: dict[str, Any],
) -> dict[str, Any]:
    """카테고리별 공제 후 합계·low 오분류 검증."""
    sums: dict[str, int] = {}
    displays: dict[str, str] = {}
    for cat_key in _CATEGORY_KEYS:
        items = [c for c in (categories.get(cat_key) or []) if isinstance(c, dict)]
        total = sum(int(c.get("estimated_amount") or 0) for c in items)
        sums[cat_key] = total
        displays[cat_key] = _format_amount_krw(total)

    low_positive = [
        c
        for c in (categories.get("low_potential") or [])
        if isinstance(c, dict) and int(c.get("estimated_amount") or 0) > 0
    ]
    errors: list[str] = []
    if low_positive:
        errors.append(
            f"low_potential에 공제 후 금액>0 후보 {len(low_positive)}건 — 분류 오류"
        )
    tab_sum = sum(sums.values())
    return {
        "category_estimated_sums": sums,
        "category_estimated_displays": displays,
        "category_estimated_tab_sum": tab_sum,
        "category_estimated_tab_sum_display": _format_amount_krw(tab_sum),
        "low_potential_estimated_positive_count": len(low_positive),
        "classification_ok": len(low_positive) == 0,
        "classification_errors": errors,
    }


def _build_amount_breakdown(visit: dict[str, Any], payable: dict[str, Any]) -> dict[str, Any]:
    self_pay = int(payable.get("base_self_pay_amount") or 0)
    deductible = int(payable.get("deductible_amount") or 0)
    est = int(payable.get("estimated_payable_amount") or 0)
    return {
        "self_pay_display": _format_amount_krw(self_pay) if self_pay > 0 else None,
        "deductible_amount_display": (
            _format_amount_krw(deductible) if deductible > 0 else "0원(입원·약관 확인)"
        ),
        "claimable_amount_display": _format_amount_krw(est),
        "calculation_memo": str(payable.get("calculation_note") or _AMOUNT_CALCULATION_MEMO),
        "deductible_rule": str(payable.get("deductible_rule") or ""),
    }


def _candidate_amount_label(self_pay: int, estimated: int) -> str:
    if estimated > 0:
        return f"청구가능금액(추정) {_format_amount_krw(estimated)}"
    if self_pay > 0:
        return f"청구가능금액(추정) 0원"
    return "청구가능금액(추정) 0원"


def run_ai_claim_analysis(
    medical_visits: list[dict[str, Any]],
    insured_summary: dict[str, Any] | None,
    *,
    product_count: int = 0,
) -> dict[str, Any]:
    """rule 기반 청구 검토 후보(ai_analysis_result 구조)."""
    actual_loss_products = detect_actual_loss_products(insured_summary)
    has_actual_loss = bool(actual_loss_products)
    all_product_names = _all_insurance_product_names(insured_summary)
    product_info = actual_loss_products[0] if actual_loss_products else None

    categories: dict[str, list[dict[str, Any]]] = {key: [] for key in _CATEGORY_KEYS}
    for index, visit in enumerate(medical_visits):
        if not visit.get("hospital_type"):
            visit = dict(visit)
            visit["hospital_type"] = classify_hospital_type(visit)
            visit["visit_type"] = classify_visit_type(visit, visit["hospital_type"])
            visit["group_key"] = build_visit_group_key(visit)
        payable = estimate_actual_loss_payable_amount(visit, product_info)
        related_insurance = build_related_insurance_for_visit(
            actual_loss_products, visit
        )
        self_pay = int(payable.get("base_self_pay_amount") or 0)
        estimated = int(payable.get("estimated_payable_amount") or 0)
        category, reason, next_action, required_docs = _classify_visit_candidate(
            visit,
            has_actual_loss=has_actual_loss,
            related_insurance=related_insurance,
            payable=payable,
        )
        category = _enforce_candidate_category(
            category,
            estimated_amount=estimated,
            self_pay_amount=self_pay,
        )
        candidate = {
            "id": f"visit-{index + 1}",
            "category": category,
            "category_label": _CATEGORY_LABELS[category],
            "date": visit.get("visit_date") or "—",
            "hospital_name": visit.get("hospital_name") or "—",
            "department": visit.get("department") or "—",
            "diagnosis_name": visit.get("diagnosis") or "—",
            "diagnosis_code": visit.get("diagnosis_code") or "—",
            "self_pay_amount": self_pay,
            "deductible_amount": int(payable.get("deductible_amount") or 0),
            "estimated_amount": estimated,
            "amount_basis": "본인부담금 기준",
            "deductible_applied": bool(payable.get("deductible_applied")),
            "deductible_note": str(payable.get("calculation_note") or ""),
            "hospital_type": visit.get("hospital_type") or "unknown",
            "visit_type": visit.get("visit_type") or "unknown",
            "group_key": visit.get("group_key") or "",
            "amount_label": _candidate_amount_label(self_pay, estimated),
            "reason": reason,
            "next_action": next_action,
            "required_documents": required_docs,
            "amount_breakdown": _build_amount_breakdown(visit, payable),
            "related_medical": [
                {
                    "visit_date": visit.get("visit_date"),
                    "hospital_name": visit.get("hospital_name"),
                    "department": visit.get("department"),
                    "diagnosis": visit.get("diagnosis"),
                    "copay_display": visit.get("copay_display"),
                    "hospital_type": visit.get("hospital_type"),
                }
            ],
            "related_insurance": (
                related_insurance
                if related_insurance
                else build_related_insurance_placeholder()
            ),
            "visit_date": visit.get("visit_date"),
            "diagnosis": visit.get("diagnosis"),
            "estimated_display": _format_amount_krw(estimated),
        }
        categories[category].append(candidate)

    categories = _reconcile_category_buckets(categories)
    totals = compute_analysis_totals(categories)
    classification_debug = build_classification_debug(categories)

    detected_summary = [
        {
            "company_name": _resolve_related_company_name(
                p, p.get("_group_company_name", "")
            ),
            "insurance_name": p.get("insurance_name"),
            "policy_number_hid": p.get("policy_number_hid"),
            "detection_reason": _actual_loss_detection_reason(p),
        }
        for p in actual_loss_products
    ]

    base = {
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
        "category_tabs": build_category_tabs_from_totals(totals),
        "classification_debug": classification_debug,
        "default_category": "high_potential",
    }
    return sanitize_ai_analysis_result(base)


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
        return sanitize_ai_analysis_result(result)
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
            return sanitize_ai_analysis_result(result)
        result["engine"] = f"openai:{model} + rule 기반"
        result["fallback_reason"] = None
    except Exception as exc:
        logger.warning("openai enhance skipped: %s", type(exc).__name__)
        result["fallback_reason"] = f"openai_error:{type(exc).__name__}"
    return sanitize_ai_analysis_result(result)


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
    else:
        result = sanitize_ai_analysis_result(result)
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
    medical_visits = data.get("medical_visits") or []
    return {
        "flow_id": flow_id,
        "current_step": 5,
        "debug_panel": debug,
        "analysis_debug": debug_info,
        "customer_display": data.get("customer"),
        "medical_counts": data["medical_counts"],
        "medical_preview": build_medical_preview(medical_visits),
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
    elif ai_result:
        ai_result = sanitize_ai_analysis_result(ai_result)

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
            "ai_total_candidate_base": totals.get("total_candidate_base_display", "—"),
            "detected_actual_loss_count": len(
                (ai_result or {}).get("detected_actual_loss_products") or []
            ),
            "fallback_reason": (ai_result or {}).get("fallback_reason", "—"),
        }
        cls_debug = (ai_result or {}).get("classification_debug") or {}
        if cls_debug:
            debug_info["category_estimated_sums"] = cls_debug.get(
                "category_estimated_displays", cls_debug.get("category_estimated_sums")
            )
            debug_info["low_potential_estimated_positive_count"] = cls_debug.get(
                "low_potential_estimated_positive_count", 0
            )
            debug_info["classification_ok"] = cls_debug.get("classification_ok", "—")
            debug_info["classification_errors"] = (
                "; ".join(cls_debug.get("classification_errors") or [])
                or "—"
            )

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
