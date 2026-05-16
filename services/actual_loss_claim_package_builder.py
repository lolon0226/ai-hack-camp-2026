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
)
from services.persistent_store import (
    _parse_received_document_metadata,
    get_customer_profile_by_key,
    list_operator_customers,
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

_OCR_COMPLETED_STATUSES = frozenset({"completed", "complete", "done", "success", "ok"})
_INPUT_REQUIRED = "추가 입력 필요"
_CONSENT_REQUIRED = "추가 입력 필요 또는 동의 필요"
_DEFAULT_NOTICE_METHOD = "문자/카카오 알림톡"


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


def _is_ocr_completed(ocr_status: str) -> bool:
    return str(ocr_status or "").strip().lower() in _OCR_COMPLETED_STATUSES


def _normalize_person_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _normalize_hospital_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _hospital_names_match(ocr_hospital: str, visit_hospital: str) -> bool:
    a = _normalize_hospital_name(ocr_hospital)
    b = _normalize_hospital_name(visit_hospital)
    if not a:
        return True
    if not b:
        return False
    return a in b or b in a


def _visit_date_digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))[:8]


def _normalize_visit_dates(raw: Any) -> list[str]:
    items: list[str] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return items
    for entry in raw:
        digits = _visit_date_digits(str(entry))
        if len(digits) == 8:
            items.append(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}")
    return list(dict.fromkeys(items))


def _infer_visit_dates_from_title(title: str) -> list[str]:
    dates: list[str] = []
    for match in re.finditer(r"(\d{4})[-.]?(\d{2})[-.]?(\d{2})", str(title or "")):
        dates.append(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
    return list(dict.fromkeys(dates))


def _extract_ocr_fields(doc: dict[str, Any], customer_name: str) -> dict[str, Any]:
    """수신문서 metadata·OCR 결과(또는 데모 추론)."""
    meta = _parse_received_document_metadata(doc.get("metadata_json"))
    ocr = meta.get("ocr") if isinstance(meta.get("ocr"), dict) else {}
    if not ocr and isinstance(meta.get("ocr_extracted"), dict):
        ocr = meta.get("ocr_extracted")

    patient_name = str(
        ocr.get("patient_name")
        or meta.get("patient_name")
        or meta.get("ocr_patient_name")
        or ""
    ).strip()
    hospital_name = str(
        ocr.get("hospital_name")
        or meta.get("hospital_name")
        or doc.get("hospital_name_display")
        or ""
    ).strip()
    if hospital_name in ("—", "TEST_HOSPITAL"):
        hospital_name = str(meta.get("hospital_name") or ocr.get("hospital_name") or "").strip()

    visit_dates = _normalize_visit_dates(
        ocr.get("visit_dates") or ocr.get("visit_date") or meta.get("visit_dates")
    )
    if not visit_dates and _is_ocr_completed(str(doc.get("ocr_status") or "")):
        visit_dates = _infer_visit_dates_from_title(str(doc.get("document_title") or ""))

    document_type = str(
        ocr.get("document_type")
        or meta.get("document_type")
        or doc.get("document_type_candidate")
        or ""
    ).strip()

    if not patient_name:
        linked = str(doc.get("linked_customer_name") or "").strip()
        if linked and linked not in ("—", "TEST_HOSPITAL") and _normalize_person_name(linked):
            patient_name = linked
        elif customer_name:
            patient_name = customer_name

    return {
        "patient_name": patient_name,
        "hospital_name": hospital_name,
        "visit_dates": visit_dates,
        "document_type": document_type,
    }


def _field_or_required(value: Any, *, default: str = _INPUT_REQUIRED) -> str:
    text = str(value or "").strip()
    if not text or text in ("—", "-", "None"):
        return default
    return text


def _contact_masks_from_entry(
    entry: dict[str, Any] | None,
    profile: dict[str, Any],
) -> tuple[str, str]:
    """주민·전화 마스킹(없으면 추가 입력 필요)."""
    identity_raw = ""
    phone_raw = ""
    if isinstance(entry, dict):
        customer = entry.get("customer") if isinstance(entry.get("customer"), dict) else {}
        identity_raw = str(customer.get("identity") or customer.get("resident") or "").strip()
        phone_raw = str(customer.get("phone") or customer.get("mobile") or "").strip()
    rrn_masked = (
        mask_identity_display(identity_raw) if identity_raw else _INPUT_REQUIRED
    )
    phone_masked = mask_phone_display(phone_raw) if phone_raw else _INPUT_REQUIRED
    return rrn_masked, phone_masked


def _infer_accident_type(diagnosis: str) -> str:
    text = str(diagnosis or "")
    if "교통" in text:
        return "교통"
    if any(token in text for token in ("상해", "외상", "골절", "화상")):
        return "상해"
    return "질병"


def _infer_coverage_detail_from_target(target: dict[str, Any]) -> str:
    visit_type = str(target.get("visit_type") or "").strip().lower()
    hospital = str(target.get("hospital_name") or "")
    department = str(target.get("department") or "")
    if visit_type == "pharmacy" or "약국" in hospital or "약제" in department:
        return "처방조제"
    if visit_type == "inpatient" or "입원" in department:
        return "입원"
    return "통원"


def _treatment_dates_from_targets(claim_targets: list[dict[str, Any]]) -> list[str]:
    dates: list[str] = []
    for target in claim_targets:
        digits = _visit_date_digits(str(target.get("visit_date") or ""))
        if len(digits) == 8:
            dates.append(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}")
    return list(dict.fromkeys(dates))


def build_claim_form_package(
    *,
    customer_profile: dict[str, Any],
    claim_targets: list[dict[str, Any]],
    attached_documents: list[dict[str, Any]],
    identity_masked: str,
    phone_masked: str,
) -> dict[str, Any]:
    """보험금 청구서 필수 항목(데모 — 미입력은 추가 입력 필요)."""
    customer_name = _field_or_required(customer_profile.get("name"))
    email = str(customer_profile.get("email") or "").strip()

    treatment_dates = _treatment_dates_from_targets(claim_targets)
    diagnoses = [
        str(t.get("diagnosis") or "").strip()
        for t in claim_targets
        if str(t.get("diagnosis") or "").strip() not in ("—", "")
    ]
    diagnosis_text = (
        diagnoses[0]
        if len(diagnoses) == 1
        else (" / ".join(diagnoses[:3]) if diagnoses else _INPUT_REQUIRED)
    )

    hospitals = list(
        dict.fromkeys(
            str(t.get("hospital_name") or "").strip()
            for t in claim_targets
            if str(t.get("hospital_name") or "").strip() not in ("—", "")
        )
    )
    hospital_name = (
        hospitals[0]
        if len(hospitals) == 1
        else (hospitals[0] if hospitals else _INPUT_REQUIRED)
    )

    accident_type = (
        _infer_accident_type(diagnosis_text)
        if diagnosis_text != _INPUT_REQUIRED
        else "질병"
    )
    coverage_set = list(
        dict.fromkeys(_infer_coverage_detail_from_target(t) for t in claim_targets)
    )
    if not coverage_set:
        coverage_detail = _INPUT_REQUIRED
    elif len(coverage_set) == 1:
        coverage_detail = coverage_set[0]
    else:
        coverage_detail = "통원/입원/처방조제 중 추정"

    onset_date = treatment_dates[0] if treatment_dates else _INPUT_REQUIRED

    transmission_attached = build_transmission_attached_documents(attached_documents)

    return {
        "insured_person": {
            "name": customer_name,
            "rrn_masked": identity_masked,
            "phone_masked": phone_masked,
            "medical_aid_status": _INPUT_REQUIRED,
        },
        "policyholder": {
            "name": customer_name if customer_name != _INPUT_REQUIRED else _INPUT_REQUIRED,
            "rrn_masked": _INPUT_REQUIRED,
            "same_as_insured_note": (
                "피보험자와 동일 추정"
                if customer_name != _INPUT_REQUIRED
                else ""
            ),
        },
        "claim_notice_receiver": {
            "name": customer_name,
            "phone_masked": phone_masked,
            "notice_method": _DEFAULT_NOTICE_METHOD,
            "email": email if email else _INPUT_REQUIRED,
            "fax": _INPUT_REQUIRED,
            "address": _INPUT_REQUIRED,
        },
        "accident_or_treatment": {
            "claim_type": "실손의료비",
            "accident_type": accident_type,
            "receipt_type": "신규",
            "accident_or_onset_date": onset_date,
            "hospital_name": hospital_name,
            "diagnosis": diagnosis_text,
            "treatment_dates": treatment_dates,
            "traffic_accident": accident_type == "교통",
        },
        "claim_coverage": {
            "main_coverage": "실손의료비",
            "detail": coverage_detail,
            "other_coverage_review": "제외 또는 검토 필요",
        },
        "payment_account": {
            "bank_name": _INPUT_REQUIRED,
            "account_number": _INPUT_REQUIRED,
            "account_holder": (
                customer_name
                if customer_name != _INPUT_REQUIRED
                else _INPUT_REQUIRED
            ),
        },
        "consents": {
            "required_collection_use": _CONSENT_REQUIRED,
            "required_provision": _CONSENT_REQUIRED,
            "required_inquiry": _CONSENT_REQUIRED,
            "sensitive_info": _CONSENT_REQUIRED,
            "unique_identifier": _CONSENT_REQUIRED,
        },
        "attached_documents": transmission_attached,
    }


def build_transmission_attached_documents(
    attached_documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """전송용 첨부 문서 목록(수신문서함 기준)."""
    items: list[dict[str, Any]] = []
    for doc in attached_documents:
        ocr_extracted = doc.get("ocr_extracted") if isinstance(doc.get("ocr_extracted"), dict) else {}
        matched_dates = doc.get("matched_treatment_dates")
        if not matched_dates and isinstance(ocr_extracted, dict):
            matched_dates = _normalize_visit_dates(ocr_extracted.get("visit_dates"))
        items.append(
            {
                "received_document_id": doc.get("document_id"),
                "document_name": _field_or_required(doc.get("title")),
                "document_type_candidate": _field_or_required(doc.get("type_candidate")),
                "ocr_status": str(doc.get("ocr_status") or "pending"),
                "file_link": _field_or_required(doc.get("file_link")),
                "matched_customer": _field_or_required(doc.get("matched_customer")),
                "matched_treatment_dates": matched_dates or [],
                "link_status": doc.get("link_status"),
                "included_in_claim_targets": bool(doc.get("included_in_claim_targets")),
            }
        )
    return items


def _patient_names_match(ocr_patient: str, customer_name: str) -> bool:
    ocr_norm = _normalize_person_name(ocr_patient)
    customer_norm = _normalize_person_name(customer_name)
    if not ocr_norm or not customer_norm:
        return True
    return ocr_norm == customer_norm


def _visit_to_claim_target(
    visit: dict[str, Any],
    *,
    document_id: int | None,
    document_title: str,
    ocr_fields: dict[str, Any],
) -> dict[str, Any]:
    self_pay = int(visit.get("self_pay_amount") or 0)
    display = visit.get("copay_display") or (
        f"{self_pay:,}원" if self_pay else "—"
    )
    return {
        "visit_date": visit.get("visit_date"),
        "hospital_name": visit.get("hospital_name"),
        "department": visit.get("department"),
        "diagnosis": visit.get("diagnosis"),
        "visit_type": visit.get("visit_type"),
        "self_pay_display": display,
        "self_pay_amount": self_pay,
        "estimated_amount": None,
        "estimated_display": display,
        "category_label": "수신문서 연결",
        "linked_document_id": document_id,
        "linked_document_title": document_title,
        "ocr_document_type": ocr_fields.get("document_type"),
        "ocr_visit_dates": list(ocr_fields.get("visit_dates") or []),
    }


def _build_document_linked_claim_targets(
    *,
    customer_key: str,
    customer_name: str,
    medical_visits: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """병원 수신문서(OCR 완료·고객 일치)와 매칭된 진료만 claim_targets로 생성."""
    claim_targets: list[dict[str, Any]] = []
    attached_documents: list[dict[str, Any]] = []
    seen_visit_keys: set[str] = set()

    for doc in documents:
        doc_id = doc.get("id")
        doc_customer = str(doc.get("customer_key") or "").strip()
        ocr_status = str(doc.get("ocr_status") or "pending")
        base = {
            "document_id": doc_id,
            "title": doc.get("document_title"),
            "type_candidate": doc.get("document_type_candidate"),
            "ocr_status": ocr_status,
            "customer_key": doc_customer or None,
            "file_link": doc.get("file_url") or "",
            "matched_customer": customer_name if doc_customer == customer_key else "",
            "matched_treatment_dates": [],
            "included_in_claim_targets": False,
        }

        if doc_customer and doc_customer != customer_key:
            continue

        if not doc_customer:
            attached_documents.append(
                {
                    **base,
                    "link_status": "customer_unmatched",
                    "link_note": "OCR/고객매칭 필요",
                }
            )
            continue

        if not _is_ocr_completed(ocr_status):
            attached_documents.append(
                {
                    **base,
                    "link_status": "ocr_pending",
                    "link_note": "OCR/고객매칭 필요",
                }
            )
            continue

        ocr_fields = _extract_ocr_fields(doc, customer_name)
        if not _patient_names_match(ocr_fields.get("patient_name", ""), customer_name):
            attached_documents.append(
                {
                    **base,
                    "link_status": "ocr_pending",
                    "link_note": "OCR/고객매칭 필요",
                }
            )
            continue

        visit_date_digits = {_visit_date_digits(d) for d in ocr_fields.get("visit_dates") or []}
        hospital_filter = str(ocr_fields.get("hospital_name") or "").strip()

        if not visit_date_digits:
            attached_documents.append(
                {
                    **base,
                    "link_status": "ocr_pending",
                    "link_note": "OCR/고객매칭 필요",
                    "ocr_extracted": ocr_fields,
                }
            )
            continue

        matched_count = 0
        doc_matched_dates: list[str] = []
        for visit in medical_visits:
            vd = _visit_date_digits(str(visit.get("visit_date") or ""))
            if vd not in visit_date_digits:
                continue
            if hospital_filter and not _hospital_names_match(
                hospital_filter, str(visit.get("hospital_name") or "")
            ):
                continue
            visit_key = f"{vd}|{_normalize_hospital_name(str(visit.get('hospital_name') or ''))}"
            if visit_key in seen_visit_keys:
                continue
            seen_visit_keys.add(visit_key)
            target = _visit_to_claim_target(
                visit,
                document_id=int(doc_id) if doc_id is not None else None,
                document_title=str(doc.get("document_title") or ""),
                ocr_fields=ocr_fields,
            )
            claim_targets.append(target)
            date_digits = _visit_date_digits(str(target.get("visit_date") or ""))
            if len(date_digits) == 8:
                doc_matched_dates.append(
                    f"{date_digits[:4]}-{date_digits[4:6]}-{date_digits[6:8]}"
                )
            matched_count += 1

        base["matched_treatment_dates"] = list(dict.fromkeys(doc_matched_dates))

        if matched_count > 0:
            attached_documents.append(
                {
                    **base,
                    "link_status": "linked",
                    "included_in_claim_targets": True,
                    "matched_visit_count": matched_count,
                    "ocr_extracted": ocr_fields,
                }
            )
        else:
            attached_documents.append(
                {
                    **base,
                    "link_status": "no_matching_visit",
                    "link_note": "OCR/고객매칭 필요",
                    "ocr_extracted": ocr_fields,
                }
            )

    return claim_targets, attached_documents


def build_transmission_payload(
    *,
    customer_profile: dict[str, Any],
    products: list[dict[str, Any]],
    claim_targets: list[dict[str, Any]],
    claim_form: dict[str, Any],
    package_summary: dict[str, Any],
) -> dict[str, Any]:
    """보험회사 전송용 JSON(민감정보 마스킹, 실제 API 미호출)."""
    raw = {
        "package_version": "redribbon-actual-loss-v2",
        "transmission_mode": "demo_prepare_only",
        "package_basis": "received_documents",
        "customer": {
            "customer_id": customer_profile.get("customer_key"),
            "name": customer_profile.get("name"),
        },
        "summary": package_summary,
        "claim_form": claim_form,
        "insured_person": claim_form.get("insured_person"),
        "policyholder": claim_form.get("policyholder"),
        "claim_notice_receiver": claim_form.get("claim_notice_receiver"),
        "accident_or_treatment": claim_form.get("accident_or_treatment"),
        "claim_coverage": claim_form.get("claim_coverage"),
        "payment_account": claim_form.get("payment_account"),
        "consents": claim_form.get("consents"),
        "attached_documents": claim_form.get("attached_documents"),
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
    }
    return mask_sensitive_payload(raw)


def build_actual_loss_claim_package(
    customer_key: str,
    *,
    entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """고객별 실손 청구 패키지 — 병원 수신문서 기준(6단계 AI 전체 후보 미사용)."""
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

    seed_operator_received_documents_if_empty()
    documents = list_operator_received_documents(customer_key=key, limit=50)
    customer_name = str(profile.get("name") or "").strip()

    claim_targets, attached_documents = _build_document_linked_claim_targets(
        customer_key=key,
        customer_name=customer_name,
        medical_visits=medical_visits,
        documents=documents,
    )

    identity_masked, phone_masked = _contact_masks_from_entry(entry, profile)
    claim_form = build_claim_form_package(
        customer_profile=profile,
        claim_targets=claim_targets,
        attached_documents=attached_documents,
        identity_masked=identity_masked,
        phone_masked=phone_masked,
    )

    total_linked_self_pay = sum(int(t.get("self_pay_amount") or 0) for t in claim_targets)
    ocr_completed_count = sum(
        1 for d in attached_documents if _is_ocr_completed(str(d.get("ocr_status") or ""))
    )

    package_summary = {
        "actual_loss_product_count": len(products),
        "claim_target_count": len(claim_targets),
        "received_document_count": len(attached_documents),
        "ocr_completed_document_count": ocr_completed_count,
        "total_estimated_amount": total_linked_self_pay,
        "total_estimated_display": (
            f"{total_linked_self_pay:,}원" if total_linked_self_pay else "0원"
        ),
        "medical_visit_count": len(medical_visits),
        "has_medical": bool(medical_bundle),
        "has_insurance": bool(insurance_bundle),
        "package_basis": "received_documents",
    }

    transmission_payload = build_transmission_payload(
        customer_profile=profile,
        products=products,
        claim_targets=claim_targets,
        claim_form=claim_form,
        package_summary=package_summary,
    )
    demo_state = load_actual_loss_claim_demo_state(key)

    return {
        "customer_profile": {
            **profile,
            "identity_masked": identity_masked,
            "phone_masked": phone_masked,
        },
        "claim_form": claim_form,
        "package_summary": package_summary,
        "actual_loss_products": products,
        "claim_targets": claim_targets,
        "received_documents": documents,
        "attached_documents": attached_documents,
        "transmission_json": json.dumps(
            transmission_payload, ensure_ascii=False, indent=2
        ),
        "transmission_payload": transmission_payload,
        "demo_state": demo_state,
        "api_ready": bool(products) and bool(claim_targets),
        "api_ready_note": (
            "데모: 보험사 API는 호출하지 않으며 전송용 JSON·상태만 저장합니다."
            if products and claim_targets
            else "실손 담보·수신문서 연결 진료 확인이 필요합니다."
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
        "debug": {
            "ai_analysis_not_used_for_operator_package": True,
        },
    }


def build_operator_customer_picker(
    flow_store: dict[str, Any],
) -> list[dict[str, Any]]:
    """DB 고객 + FLOW_STORE에만 있는 고객 병합."""
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    try:
        db_rows = list_operator_customers(limit=200)
    except Exception:
        db_rows = []
    for row in db_rows:
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
                "email": str(customer.get("email") or "").strip(),
                "medical_record_count": 0,
                "insurance_record_count": 0,
                "latest_flow_id": flow_id,
                "has_medical": entry.get("medical_status") == "completed",
                "has_insurance": entry.get("insurance_status") == "completed",
            }
        )
    return items
