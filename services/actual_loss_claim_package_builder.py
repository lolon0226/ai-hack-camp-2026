# -*- coding: utf-8 -*-
"""실손보험 청구 패키지 조립(CODEF·보험사 API 호출 없음)."""
from __future__ import annotations

import json
import re
from typing import Any

from services.credit4u_contract_summary import resolve_company_name_for_product
from services.insurance_summary import resolve_stored_insurance_for_display
from services.integrated_analysis import (
    _is_excluded_insurance_product,
    collect_actual_loss_coverages,
    detect_actual_loss_products,
    is_strict_actual_loss_product,
    normalize_medical_visit_row,
    run_ai_claim_analysis,
)
from services.persistent_store import (
    get_customer_profile_by_key,
    list_operator_received_documents,
    load_actual_loss_claim_demo_state,
    load_latest_insurance_record_by_customer_key,
    load_latest_medical_records_by_customer_key,
    seed_operator_received_documents_if_empty,
)

_SENSITIVE_JSON_KEYS = frozenset(
    {
        "identity",
        "resident",
        "jumin",
        "phone",
        "mobile",
        "tel",
        "policy_number",
        "policy_no",
        "account",
    }
)


def mask_identity_display(identity: str) -> str:
    digits = re.sub(r"\D", "", str(identity or ""))
    if len(digits) >= 6:
        return f"{digits[:6]}-*******"
    return "*******"


def mask_phone_display(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) <= 3:
        return "***"
    return digits[:3] + ("*" * max(len(digits) - 3, 4))


def mask_policy_number_display(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text or text in ("—", "-"):
        return "—"
    if len(text) <= 4:
        return "****"
    return ("*" * (len(text) - 4)) + text[-4:]


def _mask_sensitive_value(key: str, value: Any) -> Any:
    lowered = str(key).lower()
    if any(token in lowered for token in _SENSITIVE_JSON_KEYS):
        if "policy" in lowered or "증권" in lowered:
            return mask_policy_number_display(str(value or ""))
        if "phone" in lowered or "tel" in lowered or "mobile" in lowered:
            return mask_phone_display(str(value or ""))
        return mask_identity_display(str(value or ""))
    return value


def mask_sensitive_payload(data: Any) -> Any:
    """전송용 JSON 민감정보 마스킹."""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                out[key] = mask_sensitive_payload(value)
            else:
                out[key] = _mask_sensitive_value(str(key), value)
        return out
    if isinstance(data, list):
        return [mask_sensitive_payload(item) for item in data[:50]]
    return data


def _medical_visits_from_bundle(bundle: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(bundle, dict):
        return []
    basic = bundle.get("basic") or []
    if not isinstance(basic, list):
        return []
    visits: list[dict[str, Any]] = []
    for row in basic:
        if isinstance(row, dict):
            visits.append(normalize_medical_visit_row(row))
    return visits


def _resolve_product_company(product: dict[str, Any]) -> dict[str, Any]:
    row = dict(product)
    resolved = resolve_company_name_for_product(
        row.get("company_name"),
        row.get("insurance_name"),
    )
    row["company_name"] = resolved.get("company_name") or row.get("company_name") or "—"
    row["company_inferred"] = bool(resolved.get("company_inferred"))
    row["policy_number_hid"] = mask_policy_number_display(
        str(row.get("policy_number_hid") or row.get("policy_number") or "—")
    )
    return row


def _strict_actual_loss_products_for_package(
    insured_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    products = detect_actual_loss_products(insured_summary)
    items: list[dict[str, Any]] = []
    for product in products:
        if not is_strict_actual_loss_product(product):
            continue
        if _is_excluded_insurance_product(product):
            continue
        row = _resolve_product_company(product)
        coverages = collect_actual_loss_coverages(product)
        row["coverages_display"] = [
            str(c.get("coverage_name") or c.get("agreement_type") or "").strip()
            for c in coverages
            if isinstance(c, dict) and str(c.get("coverage_name") or "").strip()
        ][:8]
        items.append(row)
    return items


def _claim_target_visits(
    medical_visits: list[dict[str, Any]],
    ai_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """공제 후 청구가능금액이 있는 후보 우선."""
    if isinstance(ai_result, dict):
        picked: list[dict[str, Any]] = []
        categories = ai_result.get("categories")
        if isinstance(categories, dict):
            for key in ("high_potential", "need_review", "need_documents"):
                for item in categories.get(key) or []:
                    if not isinstance(item, dict):
                        continue
                    if int(item.get("estimated_amount") or 0) <= 0:
                        continue
                    picked.append(
                        {
                            "visit_date": item.get("date") or item.get("visit_date"),
                            "hospital_name": item.get("hospital_name"),
                            "department": item.get("department"),
                            "diagnosis": item.get("diagnosis_name") or item.get("diagnosis"),
                            "self_pay_display": item.get("amount_label"),
                            "estimated_amount": int(item.get("estimated_amount") or 0),
                            "estimated_display": item.get("estimated_display"),
                            "category_label": item.get("category_label"),
                        }
                    )
        if picked:
            return picked[:30]
    targets: list[dict[str, Any]] = []
    for visit in medical_visits:
        self_pay = int(visit.get("self_pay_amount") or 0)
        if self_pay <= 0:
            continue
        targets.append(
            {
                "visit_date": visit.get("visit_date"),
                "hospital_name": visit.get("hospital_name"),
                "department": visit.get("department"),
                "diagnosis": visit.get("diagnosis"),
                "self_pay_display": visit.get("copay_display"),
                "estimated_amount": None,
                "estimated_display": visit.get("copay_display"),
                "category_label": "진료내역",
            }
        )
    return targets[:30]


def build_transmission_payload(
    *,
    customer_profile: dict[str, Any],
    products: list[dict[str, Any]],
    claim_targets: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    package_summary: dict[str, Any],
) -> dict[str, Any]:
    """보험회사 전송용 JSON(민감정보 마스킹)."""
    raw = {
        "package_version": "redribbon-actual-loss-v1",
        "customer": {
            "customer_id": customer_profile.get("customer_key"),
            "name": customer_profile.get("name"),
        },
        "summary": package_summary,
        "actual_loss_contracts": [
            {
                "company_name": p.get("company_name"),
                "insurance_name": p.get("insurance_name"),
                "policy_number_hid": p.get("policy_number_hid"),
                "contract_status": p.get("contract_status"),
                "coverages": p.get("coverages_display") or [],
            }
            for p in products
        ],
        "claim_targets": claim_targets,
        "attached_documents": [
            {
                "document_id": d.get("id"),
                "title": d.get("document_title"),
                "type_candidate": d.get("document_type_candidate"),
                "ocr_status": d.get("ocr_status"),
            }
            for d in documents
        ],
        "transmission_mode": "demo_prepare_only",
    }
    return mask_sensitive_payload(raw)


def build_actual_loss_claim_package(
    customer_key: str,
    *,
    entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """고객별 실손 청구 패키지 화면 컨텍스트."""
    key = str(customer_key or "").strip()
    profile = get_customer_profile_by_key(key)
    if not profile:
        return {"error": "customer_not_found", "message": "고객을 찾을 수 없습니다."}

    medical_bundle = load_latest_medical_records_by_customer_key(key)
    insurance_bundle = load_latest_insurance_record_by_customer_key(key)
    medical_visits = _medical_visits_from_bundle(medical_bundle)

    insured_summary: dict[str, Any] = {}
    if insurance_bundle:
        display = resolve_stored_insurance_for_display(
            insurance_bundle.get("normalized_payload"),
            insurance_bundle.get("summary") or {},
            str(profile.get("name") or ""),
            raw_response=insurance_bundle.get("raw_response"),
        )
        insured_summary = display.get("insured_summary") or {}

    products = _strict_actual_loss_products_for_package(insured_summary)

    ai_result: dict[str, Any] | None = None
    if isinstance(entry, dict) and isinstance(entry.get("ai_analysis_result"), dict):
        ai_result = entry.get("ai_analysis_result")
    elif medical_visits or products:
        ai_result = run_ai_claim_analysis(
            medical_visits,
            insured_summary,
            product_count=len(products),
        )

    claim_targets = _claim_target_visits(medical_visits, ai_result)
    seed_operator_received_documents_if_empty()
    documents = list_operator_received_documents(customer_key=key, limit=50)
    if not documents:
        documents = list_operator_received_documents(limit=20)

    total_estimated = 0
    if isinstance(ai_result, dict):
        totals = ai_result.get("totals") or {}
        total_estimated = int(totals.get("total_estimated_amount") or 0)

    package_summary = {
        "actual_loss_product_count": len(products),
        "claim_target_count": len(claim_targets),
        "received_document_count": len(documents),
        "total_estimated_amount": total_estimated,
        "total_estimated_display": (
            f"{total_estimated:,}원" if total_estimated else "0원"
        ),
        "medical_visit_count": len(medical_visits),
        "has_medical": bool(medical_bundle),
        "has_insurance": bool(insurance_bundle),
    }

    transmission_payload = build_transmission_payload(
        customer_profile=profile,
        products=products,
        claim_targets=claim_targets,
        documents=documents,
        package_summary=package_summary,
    )
    demo_state = load_actual_loss_claim_demo_state(key)

    return {
        "customer_profile": {
            **profile,
            "identity_masked": mask_identity_display(""),
            "phone_masked": mask_phone_display(""),
        },
        "package_summary": package_summary,
        "actual_loss_products": products,
        "claim_targets": claim_targets,
        "received_documents": documents,
        "transmission_json": json.dumps(
            transmission_payload, ensure_ascii=False, indent=2
        ),
        "transmission_payload": transmission_payload,
        "demo_state": demo_state,
        "api_ready": bool(products) and bool(claim_targets),
        "api_ready_note": (
            "데모: 보험사 API는 호출하지 않으며 전송용 JSON·상태만 저장합니다."
            if products
            else "실손 담보·청구 대상 진료 확인이 필요합니다."
        ),
        "medical_bundle_meta": {
            "record_id": (medical_bundle or {}).get("record_id"),
            "flow_id": (medical_bundle or {}).get("flow_id"),
            "created_at": (medical_bundle or {}).get("created_at"),
        },
        "insurance_bundle_meta": {
            "record_id": (insurance_bundle or {}).get("record_id"),
            "flow_id": (insurance_bundle or {}).get("flow_id"),
            "created_at": (insurance_bundle or {}).get("created_at"),
        },
        "ai_analysis_summary": (ai_result or {}).get("totals") if ai_result else None,
    }


def build_operator_customer_picker(
    flow_store: dict[str, Any],
) -> list[dict[str, Any]]:
    """DB 고객 + FLOW_STORE에만 있는 고객 병합."""
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in list_operator_customers(limit=200):
        ck = str(row.get("customer_key") or "")
        if ck:
            seen.add(ck)
        items.append(row)
    for flow_id, entry in (flow_store or {}).items():
        if not isinstance(entry, dict):
            continue
        customer = entry.get("customer") if isinstance(entry.get("customer"), dict) else {}
        ck = str(entry.get("customer_key") or "").strip()
        if not ck:
            continue
        if ck in seen:
            for item in items:
                if item.get("customer_key") == ck and not item.get("latest_flow_id"):
                    item["latest_flow_id"] = flow_id
            continue
        seen.add(ck)
        items.append(
            {
                "customer_key": ck,
                "customer_id": ck,
                "name": str(customer.get("name") or "—"),
                "created_at": "",
                "medical_record_count": 0,
                "insurance_record_count": 0,
                "latest_flow_id": flow_id,
                "has_medical": entry.get("medical_status") == "completed",
                "has_insurance": entry.get("insurance_status") == "completed",
            }
        )
    return items
