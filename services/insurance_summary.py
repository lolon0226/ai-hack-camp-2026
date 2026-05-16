# -*- coding: utf-8 -*-
"""보험가입이력 원부 정규화·회사 추정·청구검토 대상 판정."""
from __future__ import annotations

from typing import Any

_COMPANY_FIELD_KEYS = (
    "company",
    "insuranceCompany",
    "resCompanyNm",
    "resCompanyName",
    "resInsuranceCompany",
    "organizationName",
)

_PRODUCT_NAME_KEYS = (
    "product_name",
    "insuranceName",
    "resInsuranceName",
    "resProductName",
    "productName",
)

_POLICY_NO_KEYS = (
    "policy_no",
    "policyNumber",
    "policyNumberHid",
    "resPolicyNo",
    "resPolicyNumber",
)

_STATUS_KEYS = (
    "status",
    "contractStatus",
    "resContractStatus",
    "resStatus",
)

_ROLE_KEYS = (
    "role",
    "resRole",
    "customerRole",
    "contractRole",
)

_PRODUCT_COMPANY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("NH", "농협", "헤아림", "프리미어운전자"), "NH농협손해보험"),
    (("DB", "동부", "프로미"), "DB손해보험"),
    (("삼성화재", "삼성"), "삼성화재"),
    (("현대해상", "현대", "굿앤굿"), "현대해상"),
    (("KB", "LIG"), "KB손해보험"),
    (("메리츠",), "메리츠화재"),
    (("한화손보", "한화손해"), "한화손해보험"),
    (("롯데",), "롯데손해보험"),
    (("흥국",), "흥국화재"),
    (("MG",), "MG손해보험"),
    (("AXA", "악사"), "악사손해보험"),
    (("AIG",), "AIG손해보험"),
    (("삼성생명",), "삼성생명"),
    (("한화생명",), "한화생명"),
    (("교보",), "교보생명"),
    (("신한라이프",), "신한라이프"),
    (("라이나",), "라이나생명"),
    (("미래에셋",), "미래에셋생명"),
)

_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("실손", "실비", "의료비", "헤아림실손"), "실손"),
    (("운전자", "교통", "자동차"), "운전자"),
    (("암",), "암"),
    (("종합", "건강", "질병", "상해"), "종합"),
    (("생명", "연금", "저축"), "생명/저축"),
)


def _pick_text(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _pick_nested_text(record: dict[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        node: Any = record
        for part in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if node is not None and str(node).strip():
            return str(node).strip()
    return ""


def _product_name_from_record(record: dict[str, Any]) -> str:
    return _pick_text(record, *_PRODUCT_NAME_KEYS)


def infer_insurance_company(record: dict[str, Any]) -> tuple[str, bool]:
    """보험회사명 반환 (company, company_inferred)."""
    if not isinstance(record, dict):
        return "—", True

    direct = _pick_text(record, *_COMPANY_FIELD_KEYS)
    if direct:
        return direct, False

    product_name = _product_name_from_record(record)
    if not product_name:
        return "—", True

    for keywords, company in _PRODUCT_COMPANY_RULES:
        for keyword in keywords:
            if keyword in product_name:
                return company, True

    return "—", True


def classify_insurance_product(record: dict[str, Any]) -> str:
    """상품명 기준 분류."""
    if not isinstance(record, dict):
        return "기타"

    preset = _pick_text(record, "category")
    if preset:
        return preset

    product_name = _product_name_from_record(record)
    if not product_name:
        return "기타"

    for keywords, category in _CATEGORY_RULES:
        for keyword in keywords:
            if keyword in product_name:
                return category
    return "기타"


def _normalize_person_name(name: Any) -> str:
    return str(name or "").strip()


def _names_match(left: Any, right: Any) -> bool:
    a = _normalize_person_name(left)
    b = _normalize_person_name(right)
    return bool(a) and a == b


def _insured_person_names(record: dict[str, Any]) -> list[str]:
    names: list[str] = []

    direct = _pick_text(record, "resInsuredPerson", "insuredPerson", "insured_name")
    if direct:
        names.append(direct)

    contract_insured = _pick_nested_text(record, ("contract", "resInsuredPerson"))
    if contract_insured:
        names.append(contract_insured)

    coverage = record.get("coverage")
    if isinstance(coverage, list):
        for item in coverage:
            if not isinstance(item, dict):
                continue
            cov_name = _pick_text(item, "resInsuredPerson", "insuredPerson", "insured_name")
            if cov_name:
                names.append(cov_name)

    return names


def _explicit_role(record: dict[str, Any]) -> str:
    role = _pick_text(record, *_ROLE_KEYS)
    if role in ("피보험자", "계약자", "수익자"):
        return role

    contractor = _pick_text(record, "resContractor", "contractor")
    beneficiary = _pick_text(record, "resBeneficiary", "beneficiary")
    insured = _pick_text(record, "resInsuredPerson", "insuredPerson")

    if contractor and not insured and not beneficiary:
        return "계약자"
    if beneficiary and not insured:
        return "수익자"
    if insured:
        return "피보험자"
    return ""


def _is_active_status(status: str) -> bool:
    if not status:
        return True
    normalized = status.strip().lower()
    inactive = ("해지", "실효", "만기", "청약철회", "terminated", "cancelled", "expired")
    return not any(token in normalized for token in inactive)


def is_claim_review_target(
    record: dict[str, Any],
    customer_name: str,
) -> dict[str, Any]:
    """청구검토 대상 여부·역할·사유."""
    if not isinstance(record, dict):
        return {
            "include_for_claim_review": False,
            "role": "참고",
            "reason": "유효하지 않은 계약 정보",
        }

    customer = _normalize_person_name(customer_name)
    explicit_role = _explicit_role(record)
    insured_names = _insured_person_names(record)
    customer_is_insured = any(_names_match(name, customer) for name in insured_names)
    contract_insured = _pick_nested_text(record, ("contract", "resInsuredPerson"))
    if contract_insured and _names_match(contract_insured, customer):
        customer_is_insured = True

    status = _pick_text(record, *_STATUS_KEYS) or "유지"
    if not _is_active_status(status):
        return {
            "include_for_claim_review": False,
            "role": explicit_role or "참고",
            "reason": "유지 계약이 아님",
        }

    if explicit_role == "계약자" and not customer_is_insured:
        return {
            "include_for_claim_review": False,
            "role": "계약자",
            "reason": "계약자 전용 계약",
        }

    if explicit_role == "수익자" and not customer_is_insured:
        return {
            "include_for_claim_review": False,
            "role": "수익자",
            "reason": "수익자 전용 계약",
        }

    if insured_names and customer and not customer_is_insured:
        return {
            "include_for_claim_review": False,
            "role": "참고",
            "reason": "피보험자가 고객과 다름",
        }

    if explicit_role == "피보험자" or customer_is_insured:
        return {
            "include_for_claim_review": True,
            "role": "피보험자",
            "reason": "피보험자 기준 포함",
        }

    preset_include = record.get("include_for_claim_review")
    preset_role = _pick_text(record, "role")
    if preset_include is True and preset_role == "피보험자":
        return {
            "include_for_claim_review": True,
            "role": "피보험자",
            "reason": "피보험자 기준 포함",
        }

    if preset_include is False and preset_role in ("계약자", "수익자"):
        return {
            "include_for_claim_review": False,
            "role": preset_role,
            "reason": f"{preset_role} 전용 계약",
        }

    return {
        "include_for_claim_review": False,
        "role": explicit_role or "참고",
        "reason": "청구검토 대상 아님",
    }


def normalize_insurance_record(
    record: dict[str, Any],
    customer_name: str,
) -> dict[str, Any]:
    """원부 1건을 화면·그룹핑용으로 정규화."""
    if not isinstance(record, dict):
        record = {}

    company, company_inferred = infer_insurance_company(record)
    product_name = _product_name_from_record(record) or "—"
    policy_no = _pick_text(record, *_POLICY_NO_KEYS) or "—"
    status = _pick_text(record, *_STATUS_KEYS) or "유지"
    category = classify_insurance_product(record)
    review = is_claim_review_target(record, customer_name)
    raw_type = _pick_text(record, "raw_type", "resInsuranceType", "insuranceType") or "—"

    return {
        "company": company,
        "company_inferred": company_inferred,
        "product_name": product_name,
        "policy_no": policy_no,
        "status": status,
        "role": review["role"],
        "category": category,
        "include_for_claim_review": bool(review["include_for_claim_review"]),
        "review_reason": review["reason"],
        "raw_type": raw_type,
    }


def insurance_summary_from_records(records: list[dict[str, Any]]) -> dict[str, int]:
    companies = {str(r.get("company") or "").strip() for r in records if r.get("company")}
    insured_valid = sum(
        1
        for r in records
        if r.get("include_for_claim_review")
        and str(r.get("role") or "") == "피보험자"
        and _is_active_status(str(r.get("status") or ""))
    )
    return {
        "total": len(records),
        "insured_valid": insured_valid,
        "company_count": len(companies),
    }


def build_insurance_company_groups(
    records: list[dict[str, Any]],
    customer_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    전체 records 정규화 후 회사별 그룹 반환.
    (normalized_records, company_groups)
    """
    normalized = [normalize_insurance_record(row, customer_name) for row in records]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in normalized:
        company = str(row.get("company") or "—").strip() or "—"
        grouped.setdefault(company, []).append(row)

    company_groups: list[dict[str, Any]] = []
    for company in sorted(grouped.keys()):
        products = grouped[company]
        products.sort(
            key=lambda item: (
                0 if item.get("include_for_claim_review") else 1,
                str(item.get("product_name") or ""),
            )
        )
        include_count = sum(1 for item in products if item.get("include_for_claim_review"))
        company_groups.append(
            {
                "company": company,
                "count": len(products),
                "claim_review_count": include_count,
                "include_count": include_count,
                "total_count": len(products),
                "products": products,
            }
        )

    return normalized, company_groups
