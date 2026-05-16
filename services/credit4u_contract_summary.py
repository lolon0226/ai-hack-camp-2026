# -*- coding: utf-8 -*-
"""신용정보원 보험가입이력 원부 정규화·피보험자 기준 요약(CODEF API 호출 없음)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

_CONTRACT_LIST_KEYS = (
    ("actual_loss_contracts", "resActualLossContractList"),
    ("flat_rate_contracts", "resFlatRateContractList"),
    ("savings_contracts", "resSavingsContractList"),
    ("car_contracts", "resCarContractList"),
    ("property_contracts", "resPropertyContractList"),
    ("actual_loss_payments", "resActualLossPaymentList"),
    ("actual_loss_statistics", "resActualLossStatisticsList"),
    ("flat_rate_statistics", "resFlatRateStatisticsList"),
)

_SOURCE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("actual_loss_contracts", "actual_loss", "실손/자동차"),
    ("flat_rate_contracts", "flat_rate", "정액"),
    ("savings_contracts", "savings", "저축성"),
    ("car_contracts", "car", "자동차"),
    ("property_contracts", "property", "재물"),
)

_UNKNOWN_COMPANY_LABELS = frozenset(
    {"", "-", "—", "미상", "보험회사 미상", "보험회사미상"}
)

# (키워드 목록, 회사명) — 앞쪽 규칙이 우선(긴·구체적 키워드 먼저)
_PRODUCT_COMPANY_INFERENCE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("삼성생명",), "삼성생명보험"),
    (("한화생명",), "한화생명보험"),
    (("교보생명", "교보"), "교보생명보험"),
    (("신한라이프",), "신한라이프생명보험"),
    (("미래에셋생명", "미래에셋"), "미래에셋생명보험"),
    (("푸본현대생명", "푸본현대"), "푸본현대생명보험"),
    (("동양생명",), "동양생명보험"),
    (("흥국생명",), "흥국생명보험"),
    (("라이나생명", "라이나"), "라이나생명보험"),
    (("ABL생명", "ABL"), "ABL생명보험"),
    (("KDB생명", "KDB"), "KDB생명보험"),
    (("DB손해보험", "DB손보", "동부화재", "동부손보", "동부", "DB"), "DB손해보험"),
    (("NH농협손해보험", "NH농협", "NH", "농협", "헤아림"), "NH농협손해보험"),
    (("삼성화재해상", "삼성화재", "삼성"), "삼성화재해상보험"),
    (("현대해상화재", "현대해상", "현대"), "현대해상화재보험"),
    (("KB손해보험", "KB손보", "KB손해", "KB"), "KB손해보험"),
    (("메리츠화재", "메리츠"), "메리츠화재보험"),
    (("롯데손해보험", "롯데손보", "롯데"), "롯데손해보험"),
    (("한화손해보험", "한화손보", "한화손해"), "한화손해보험"),
    (("흥국화재해상", "흥국화재", "흥국"), "흥국화재해상보험"),
    (("MG손해보험", "MG손보", "MG", "새마을"), "MG손해보험"),
    (("AIG손해보험", "AIG"), "AIG손해보험"),
    (("AXA손해보험", "AXA", "악사"), "AXA손해보험"),
    (("하나손해보험", "하나손보", "하나"), "하나손해보험"),
    (("캐롯손해보험", "캐롯"), "캐롯손해보험"),
    (("신한EZ손해보험", "신한EZ", "신한이지", "신한"), "신한EZ손해보험"),
)


def as_dict_list(val: Any) -> list[dict[str, Any]]:
    if val is None:
        return []
    if isinstance(val, dict):
        return [val]
    if isinstance(val, list):
        return [x for x in val if isinstance(x, dict)]
    return []


def pick_row_str(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "-"


def norm_name(value: Any) -> str:
    return (str(value) if value is not None else "").replace(" ", "").strip()


def is_unknown_company_name(name: Any) -> bool:
    text = str(name or "").strip()
    if not text or text in _UNKNOWN_COMPANY_LABELS:
        return True
    compact = text.replace(" ", "")
    return compact in ("보험회사미상", "미상") or "미상" == text


def infer_company_name_from_product(product_name: str) -> dict[str, Any]:
    """상품명·보험명 키워드로 보험회사명 추정."""
    text = display_clean_plus_text(product_name or "")
    if not text:
        return {"company_name": "", "company_inferred": False, "matched_keyword": ""}

    for keywords, company in _PRODUCT_COMPANY_INFERENCE_RULES:
        for keyword in keywords:
            if keyword and keyword in text:
                return {
                    "company_name": company,
                    "company_inferred": True,
                    "matched_keyword": keyword,
                }
    return {"company_name": "", "company_inferred": False, "matched_keyword": ""}


def resolve_company_name_for_product(
    raw_company_name: Any,
    insurance_name: Any,
) -> dict[str, Any]:
    """원부 회사명 우선, 없으면 상품명 추정."""
    raw = str(raw_company_name or "").strip()
    if raw == "-":
        raw = ""
    if not is_unknown_company_name(raw):
        return {
            "company_name": raw,
            "company_inferred": False,
            "company_inferred_from": "",
            "company_matched_keyword": "",
        }

    inferred = infer_company_name_from_product(str(insurance_name or ""))
    inferred_name = str(inferred.get("company_name") or "").strip()
    if inferred_name and not is_unknown_company_name(inferred_name):
        return {
            "company_name": inferred_name,
            "company_inferred": True,
            "company_inferred_from": "insurance_name",
            "company_matched_keyword": str(inferred.get("matched_keyword") or ""),
        }
    return {
        "company_name": "보험회사 미상",
        "company_inferred": False,
        "company_inferred_from": "",
        "company_matched_keyword": "",
    }


def apply_company_name_inference(products: list[dict[str, Any]]) -> dict[str, Any]:
    """상품 목록 회사명 보정 및 DEBUG 집계."""
    company_inferred_count = 0
    company_unknown_count = 0
    inferred_preview: list[dict[str, Any]] = []
    unknown_preview: list[dict[str, Any]] = []
    keyword_counts: dict[str, int] = {}

    for product in products:
        resolved = resolve_company_name_for_product(
            product.get("company_name"),
            product.get("insurance_name"),
        )
        product["company_name"] = resolved["company_name"]
        if resolved["company_inferred"]:
            product["company_inferred"] = True
            product["company_inferred_from"] = resolved["company_inferred_from"]
            product["company_matched_keyword"] = resolved["company_matched_keyword"]
            company_inferred_count += 1
            kw = str(resolved["company_matched_keyword"] or "")
            if kw:
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
            if len(inferred_preview) < 20:
                inferred_preview.append(
                    {
                        "company_name": resolved["company_name"],
                        "insurance_name": product.get("insurance_name") or "",
                        "matched_keyword": kw,
                    }
                )
        else:
            product["company_inferred"] = False
            product.pop("company_inferred_from", None)
            product.pop("company_matched_keyword", None)
            if is_unknown_company_name(resolved["company_name"]):
                company_unknown_count += 1
                if len(unknown_preview) < 20:
                    unknown_preview.append(
                        {
                            "insurance_name": product.get("insurance_name") or "",
                            "policy_number_hid": product.get("policy_number_hid") or "",
                        }
                    )

    return {
        "company_inferred_count": company_inferred_count,
        "company_unknown_count": company_unknown_count,
        "company_inferred_preview": inferred_preview,
        "company_unknown_preview": unknown_preview,
        "company_matched_keyword_counts": keyword_counts,
    }


def display_clean_plus_text(value: Any) -> str:
    text = str(value) if value is not None else ""
    return " ".join(text.replace("+", " ").split())


def format_yyyymmdd_dots(yyyymmdd: str) -> str:
    text = re.sub(r"\D", "", str(yyyymmdd or ""))[:8]
    if len(text) == 8:
        return f"{text[0:4]}.{text[4:6]}.{text[6:8]}"
    return str(yyyymmdd or "").strip()


def format_currency_amount(raw: Any) -> str:
    text = str(raw).strip() if raw is not None else ""
    if not text or text == "-":
        return ""
    digits = "".join(c for c in text if c.isdigit())
    if digits and len(digits) == len(text):
        try:
            return f"{int(digits):,}원"
        except ValueError:
            pass
    return display_clean_plus_text(text)


def contract_status_rank(status: str) -> int:
    value = (status or "").strip()
    if not value or value == "-":
        return 90
    if "정상" in value:
        return 0
    if "유지" in value:
        return 1
    if "실효" in value:
        return 2
    if "해지" in value:
        return 3
    if "취소" in value:
        return 4
    return 50


def contract_is_active_status(status: str) -> bool:
    return contract_status_rank(status) <= 1


def normalize_credit4u_contract_result(response_json: dict[str, Any] | None) -> dict[str, Any]:
    """CODEF raw_response.data → 계약·지급·통계 목록."""
    if not isinstance(response_json, dict):
        return {"counts": {}}

    if "actual_loss_contracts" in response_json:
        src = response_json
    else:
        data = response_json.get("data")
        src = data if isinstance(data, dict) else response_json

    out: dict[str, Any] = {}
    for out_key, res_key in _CONTRACT_LIST_KEYS:
        out[out_key] = as_dict_list(src.get(res_key) if isinstance(src, dict) else None)
        if not out[out_key] and isinstance(src, dict):
            out[out_key] = as_dict_list(src.get(out_key))
    out["counts"] = {key: len(val) for key, val in out.items() if isinstance(val, list)}
    return out


def resolve_normalized_payload(
    raw_response: Any,
    normalized_payload: Any,
) -> dict[str, Any]:
    """저장본 normalized 또는 raw에서 정규화 결과 확보."""
    if isinstance(normalized_payload, dict) and "actual_loss_contracts" in normalized_payload:
        norm = dict(normalized_payload)
        if "counts" not in norm:
            norm["counts"] = {
                key: len(norm.get(key) or [])
                for key, _ in _CONTRACT_LIST_KEYS
                if isinstance(norm.get(key), list)
            }
        return norm
    if isinstance(raw_response, dict):
        if "actual_loss_contracts" in raw_response:
            return normalize_credit4u_contract_result({"data": raw_response})
        return normalize_credit4u_contract_result(raw_response)
    return {"counts": {}}


def _coverage_list(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return as_dict_list(contract.get("resCoverageLists")) or as_dict_list(contract.get("coverages"))


def _pick_company_block(contract: dict[str, Any]) -> dict[str, str]:
    name = pick_row_str(
        contract,
        "resCompanyName",
        "resInsurerName",
        "resInsuranceCompanyName",
        "resInsCompanyName",
        "resOrgNm",
        "resInsOrgName",
    )
    if name == "-":
        name = ""
    return {
        "company_name": name,
        "company_code": pick_row_str(
            contract,
            "resCompanyCode",
            "resInsOrgCode",
            "resOrganizationCode",
            "resInsurerCode",
            "organization",
        ),
        "home_page": pick_row_str(
            contract,
            "resHomePage",
            "resCompanyHomePage",
            "resCompanyHomepage",
            "resHomepageUrl",
            "resInsurerHomePage",
        ),
        "phone_no": pick_row_str(
            contract,
            "resTelNo",
            "resCompanyTelNo",
            "resInsurerTelNo",
            "resPhoneNo",
            "resCallCenterNo",
        ),
    }


def _format_phone_display(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:]}"
    if len(digits) == 9:
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
    if len(digits) >= 10:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}" if len(digits) == 11 else (
            f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        )
    return (phone or "").strip()


def _parse_period_from_contract(contract: dict[str, Any]) -> tuple[str, str]:
    start = str(contract.get("commStartDate") or contract.get("resCommStartDate") or "").strip()[:8]
    end = str(contract.get("commEndDate") or contract.get("resCommEndDate") or "").strip()[:8]
    if len(start) == 8 and start.isdigit() and len(end) == 8 and end.isdigit():
        return start, end
    period = str(
        contract.get("resPeriodOfInsurance")
        or contract.get("resInsurancePeriod")
        or contract.get("resInsPeriod")
        or ""
    ).strip()
    if "~" in period:
        left, _, right = period.partition("~")
        start_digits = "".join(c for c in left if c.isdigit())[:8]
        end_digits = "".join(c for c in right if c.isdigit())[:8]
        if len(start_digits) == 8 and len(end_digits) == 8:
            return start_digits, end_digits
    return "", ""


def _policy_number_hid(policy: str) -> str:
    value = (policy or "").strip()
    if not value or value == "-":
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:4]}{'*' * max(3, len(value) - 7)}{value[-3:]}"


def _build_coverage_row(
    *,
    agreement_type: str,
    coverage_name: str,
    status: str,
    amount_display: str,
    code: str,
    insured_person: str,
    start_d: str,
    end_d: str,
) -> dict[str, Any]:
    period_display = ""
    if len(start_d) == 8 and start_d.isdigit() and len(end_d) == 8 and end_d.isdigit():
        period_display = f"{format_yyyymmdd_dots(start_d)} ~ {format_yyyymmdd_dots(end_d)}"
    elif len(start_d) == 8 and start_d.isdigit():
        period_display = format_yyyymmdd_dots(start_d)
    return {
        "agreement_type": agreement_type,
        "coverage_name": coverage_name,
        "status": status,
        "amount_display": amount_display,
        "code": code,
        "insured_person": insured_person,
        "period_display": period_display,
    }


def build_insured_contract_summary(
    insurance_result: dict[str, Any] | None,
    customer_name: str,
) -> dict[str, Any]:
    """피보험자 기준 company_groups·counts·debug."""
    customer = norm_name(customer_name)
    empty: dict[str, Any] = {
        "insured_name": (customer_name or "").strip(),
        "company_groups": [],
        "counts": {
            "company_count": 0,
            "product_count": 0,
            "coverage_count": 0,
            "active_product_count": 0,
        },
        "debug": {
            "original_contract_total": 0,
            "excluded_as_other_insured_count": 0,
            "unknown_insured_contract_count": 0,
            "unknown_contracts_preview": [],
            "payment_estimated_count": 0,
            "payment_excluded_by_insured_count": 0,
            "deduped_product_count": 0,
            "duplicate_products_preview": [],
        },
    }
    if not isinstance(insurance_result, dict) or "actual_loss_contracts" not in insurance_result:
        return empty

    src = insurance_result
    original_total = 0
    excluded_other = 0
    unknown_cov = 0
    unknown_preview: list[dict[str, Any]] = []
    products_flat: list[dict[str, Any]] = []

    for list_key, source_type, source_label in _SOURCE_SPECS:
        for contract in as_dict_list(src.get(list_key)):
            original_total += 1
            coverages = _coverage_list(contract)
            if not coverages:
                unknown_cov += 1
                if len(unknown_preview) < 20:
                    block = _pick_company_block(contract)
                    unknown_preview.append(
                        {
                            "source_type": source_type,
                            "source_label": source_label,
                            "company_name": block["company_name"],
                            "insurance_name": display_clean_plus_text(
                                pick_row_str(
                                    contract,
                                    "resInsuranceName",
                                    "resInsuranceProductName",
                                    "resInsProductName",
                                    "resProductName",
                                )
                            ),
                            "policy_number": pick_row_str(
                                contract, "resPolicyNumber", "resPolicyNo"
                            ),
                        }
                    )
                continue

            contract_insured_raw = pick_row_str(
                contract,
                "resInsuredPerson",
                "resInsuredName",
                "resInsured",
                "resAssuredName",
            )
            contract_insured_norm = (
                norm_name(contract_insured_raw) if contract_insured_raw != "-" else ""
            )
            contract_insured_is_customer = bool(contract_insured_norm) and (
                contract_insured_norm == customer
            )

            matched: list[dict[str, Any]] = []
            for cov in coverages:
                insured_raw = cov.get("resInsuredPerson")
                if insured_raw is None:
                    insured_raw = cov.get("insured_person")
                insured_norm = norm_name(insured_raw)
                if insured_norm and insured_norm == customer:
                    matched.append(cov)
                    continue
                if not insured_norm and contract_insured_is_customer:
                    matched.append(cov)

            if not matched:
                excluded_other += 1
                continue

            block = _pick_company_block(contract)
            insurance_name = display_clean_plus_text(
                pick_row_str(
                    contract,
                    "resInsuranceName",
                    "resInsuranceProductName",
                    "resInsProductName",
                    "resProductName",
                )
            )
            policy = pick_row_str(contract, "resPolicyNumber", "resPolicyNo", "resInsPolicyNo")
            contract_status = pick_row_str(
                contract, "resContractStatus", "resContStatus", "resStatus"
            )
            contractor = pick_row_str(
                contract, "resContractor", "resContractorName", "resPolicyHolder"
            )
            premium = pick_row_str(contract, "resPremium", "resInsPremium", "resPaymentAmount", "resPayAmt")
            pay_cycle = pick_row_str(
                contract,
                "resPaymentCycle",
                "resPayCycle",
                "resPremiumPayCycle",
                "resPayMethod",
            )
            pay_period = pick_row_str(
                contract,
                "resPaymentPeriod",
                "resPayPeriod",
                "resPayEndDate",
                "resInsPayPeriod",
            )
            start_date, end_date = _parse_period_from_contract(contract)

            cover_rows: list[dict[str, Any]] = []
            for cov in matched:
                amount_raw = (
                    cov.get("resCoverageAmount")
                    or cov.get("resCoverageAmt")
                    or cov.get("resAmount")
                )
                cov_start = str(cov.get("commStartDate") or cov.get("resCommStartDate") or "").strip()[:8]
                cov_end = str(cov.get("commEndDate") or cov.get("resCommEndDate") or "").strip()[:8]
                if not cov_start and not cov_end:
                    cov_start, cov_end = start_date, end_date
                cover_rows.append(
                    _build_coverage_row(
                        agreement_type=display_clean_plus_text(
                            pick_row_str(cov, "resAgreementType", "resAgreeType", "resGubun")
                        ),
                        coverage_name=display_clean_plus_text(
                            pick_row_str(
                                cov,
                                "resCoverageName",
                                "resCoverName",
                                "resGuaranteeName",
                            )
                        ),
                        status=display_clean_plus_text(
                            pick_row_str(cov, "resCoverageStatus", "resCoverStatus", "resStatus")
                        ),
                        amount_display=format_currency_amount(amount_raw),
                        code=pick_row_str(cov, "resCoverageCode", "resCoverCode", "resCode"),
                        insured_person=display_clean_plus_text(
                            pick_row_str(cov, "resInsuredPerson", "insured_person", "resInsuredName")
                        ),
                        start_d=cov_start,
                        end_d=cov_end,
                    )
                )

            product_key_source = (
                f"{block.get('company_code')}|{policy}|{source_type}|{start_date}|{insurance_name}"
            )
            products_flat.append(
                {
                    "product_key": hashlib.md5(product_key_source.encode("utf-8")).hexdigest()[:20],
                    "company_name": block["company_name"],
                    "company_code": block["company_code"],
                    "home_page": block["home_page"],
                    "phone_no": block["phone_no"],
                    "insurance_name": insurance_name,
                    "policy_number": policy if policy != "-" else "",
                    "policy_number_hid": _policy_number_hid(policy) if policy != "-" else "",
                    "contract_status": contract_status if contract_status != "-" else "",
                    "contractor": contractor if contractor != "-" else "",
                    "insured_name": (customer_name or "").strip(),
                    "start_date": start_date,
                    "end_date": end_date,
                    "premium": premium if premium != "-" else "",
                    "payment_cycle": pay_cycle if pay_cycle != "-" else "",
                    "payment_period": pay_period if pay_period != "-" else "",
                    "coverage_count": len(cover_rows),
                    "coverages": cover_rows,
                    "source_type": source_type,
                    "source_type_label": source_label,
                    "_status_rank": contract_status_rank(contract_status),
                    "_active": contract_is_active_status(contract_status),
                    "_sort_start": start_date,
                }
            )

    def norm_compare(value: Any) -> str:
        return "".join(
            ch for ch in str(value or "") if not ch.isspace() and ch != "+"
        ).strip().lower()

    existing_policy_keys: set[str] = set()
    existing_name_keys: set[tuple[str, str]] = set()
    existing_name_only_keys: set[str] = set()
    for product in products_flat:
        policy_no = str(product.get("policy_number") or "").strip()
        if policy_no:
            existing_policy_keys.add(policy_no)
        name_key = (
            norm_compare(product.get("company_name")),
            norm_compare(product.get("insurance_name")),
        )
        if name_key[1]:
            existing_name_keys.add(name_key)
            existing_name_only_keys.add(name_key[1])

    seen_estimated: dict[str, dict[str, Any]] = {}
    payment_estimated_count = 0
    payment_excluded_by_insured_count = 0

    for payment in as_dict_list(src.get("actual_loss_payments")):
        pay_policy = pick_row_str(payment, "resPolicyNumber", "resPolicyNo", "resInsPolicyNo")
        pay_insurance = pick_row_str(
            payment,
            "resInsuranceName",
            "resInsuranceProductName",
            "resInsProductName",
            "resProductName",
            "resInsureProdNm",
            "resGoodsNm",
        )
        pay_company = pick_row_str(
            payment,
            "resCompanyName",
            "resInsCompanyName",
            "resInsOrgName",
            "resInsurerName",
            "resOrgNm",
        )
        pay_insured = pick_row_str(
            payment,
            "resInsuredPerson",
            "resInsuredName",
            "resInsured",
            "resAssuredName",
        )

        policy_clean = pay_policy if pay_policy != "-" else ""
        name_clean = display_clean_plus_text(pay_insurance) if pay_insurance != "-" else ""
        company_clean = pay_company if pay_company != "-" else ""

        insured_norm = norm_name(pay_insured) if pay_insured != "-" else ""
        if insured_norm and insured_norm != customer:
            payment_excluded_by_insured_count += 1
            continue

        if policy_clean and policy_clean in existing_policy_keys:
            continue
        name_key = (norm_compare(company_clean), norm_compare(name_clean))
        if name_key[1] and (
            name_key in existing_name_keys or name_key[1] in existing_name_only_keys
        ):
            continue

        dedupe_key = policy_clean or (f"{name_key[0]}|{name_key[1]}" if name_key[1] else "")
        if not dedupe_key:
            continue

        pay_amount = (
            payment.get("resPaymentAmount")
            or payment.get("resPaidAmount")
            or payment.get("resPayAmount")
            or payment.get("resInsClaimAmount")
        )
        pay_reason = display_clean_plus_text(
            pick_row_str(
                payment,
                "resReasonForPayment",
                "resAccidentReason",
                "resPaymentReason",
                "resPayCause",
                "resInsClaimReason",
            )
        )
        pay_status = display_clean_plus_text(
            pick_row_str(payment, "resPaymentStatus", "resPayStatus", "resInsClaimStatus", "resStatus")
        )
        pay_date_raw = str(
            payment.get("resPaymentDate")
            or payment.get("resPaidDate")
            or payment.get("resAccidentDate")
            or payment.get("resInsClaimDate")
            or ""
        ).strip()[:8]
        pay_date_display = (
            format_yyyymmdd_dots(pay_date_raw)
            if len(pay_date_raw) == 8 and pay_date_raw.isdigit()
            else ""
        )
        insured_display = (
            display_clean_plus_text(pay_insured)
            if pay_insured != "-"
            else (customer_name or "").strip()
        )
        coverage_row = _build_coverage_row(
            agreement_type="실손 지급내역",
            coverage_name=pay_reason if pay_reason != "-" else "지급 사유 미상",
            status=pay_status if pay_status != "-" else "지급",
            amount_display=format_currency_amount(pay_amount),
            code="",
            insured_person=insured_display,
            start_d=pay_date_raw,
            end_d="",
        )

        existing_entry = seen_estimated.get(dedupe_key)
        if existing_entry:
            existing_entry["coverages"].append(coverage_row)
            existing_entry["coverage_count"] = len(existing_entry["coverages"])
            continue

        block_pay = _pick_company_block(payment)
        estimated = {
            "product_key": hashlib.md5(
                f"{block_pay.get('company_code')}|{policy_clean}|actual_loss_estimate|{name_clean}".encode(
                    "utf-8"
                )
            ).hexdigest()[:20],
            "company_name": block_pay["company_name"],
            "company_code": block_pay["company_code"],
            "home_page": block_pay["home_page"],
            "phone_no": block_pay["phone_no"],
            "insurance_name": name_clean or "지급내역 기반 추정 상품",
            "policy_number": policy_clean,
            "policy_number_hid": _policy_number_hid(policy_clean) if policy_clean else "",
            "contract_status": "지급내역 기반 추정",
            "contractor": "",
            "insured_name": (customer_name or "").strip(),
            "start_date": "",
            "end_date": "",
            "premium": "",
            "payment_cycle": "",
            "payment_period": "",
            "coverage_count": 1,
            "coverages": [coverage_row],
            "source_type": "actual_loss_estimate",
            "source_type_label": "실손/추정",
            "_status_rank": 50,
            "_active": False,
            "_sort_start": "",
        }
        products_flat.append(estimated)
        seen_estimated[dedupe_key] = estimated
        payment_estimated_count += 1
        if policy_clean:
            existing_policy_keys.add(policy_clean)
        if name_key[1]:
            existing_name_keys.add(name_key)
            existing_name_only_keys.add(name_key[1])

    def dedupe_key(product: dict[str, Any]) -> str:
        hid = str(product.get("policy_number_hid") or "").strip()
        if hid:
            return f"hid|{hid}"
        policy_no = str(product.get("policy_number") or "").strip()
        if policy_no:
            return f"pol|{policy_no}"
        company = norm_compare(product.get("company_name"))
        name = norm_compare(product.get("insurance_name"))
        return f"meta|{company}|{name}|{product.get('start_date') or ''}|{product.get('end_date') or ''}"

    def merge_coverages(
        existing: list[dict[str, Any]], extra: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str, str, str]] = set()
        merged: list[dict[str, Any]] = []
        for row in list(existing) + list(extra):
            if not isinstance(row, dict):
                continue
            key = (
                str(row.get("agreement_type") or ""),
                str(row.get("coverage_name") or ""),
                str(row.get("code") or ""),
                str(row.get("amount_display") or ""),
                str(row.get("period_display") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        return merged

    deduped_map: dict[str, dict[str, Any]] = {}
    deduped_order: list[str] = []
    deduped_count = 0
    duplicate_preview: list[dict[str, Any]] = []
    for product in products_flat:
        key = dedupe_key(product)
        if key not in deduped_map:
            deduped_map[key] = product
            deduped_order.append(key)
            continue
        deduped_count += 1
        keeper = deduped_map[key]
        keeper["coverages"] = merge_coverages(
            keeper.get("coverages") or [], product.get("coverages") or []
        )
        keeper["coverage_count"] = len(keeper["coverages"])
        new_rank = int(product.get("_status_rank", 99) or 99)
        old_rank = int(keeper.get("_status_rank", 99) or 99)
        if new_rank < old_rank:
            for field in (
                "contract_status",
                "_status_rank",
                "_active",
                "start_date",
                "end_date",
                "_sort_start",
                "premium",
                "payment_cycle",
                "payment_period",
                "policy_number",
                "policy_number_hid",
                "contractor",
            ):
                value = product.get(field)
                if value not in (None, "", "-"):
                    keeper[field] = value
        for field in ("company_name", "company_code", "home_page", "phone_no"):
            if not str(keeper.get(field) or "").strip() and str(product.get(field) or "").strip():
                keeper[field] = product[field]
        old_label = str(keeper.get("source_type_label") or "").strip()
        new_label = str(product.get("source_type_label") or "").strip()
        if new_label and new_label not in old_label.split(", "):
            keeper["source_type_label"] = f"{old_label}, {new_label}" if old_label else new_label
        if len(duplicate_preview) < 20:
            duplicate_preview.append(
                {
                    "dedupe_key": key,
                    "company_name": product.get("company_name") or "",
                    "insurance_name": product.get("insurance_name") or "",
                    "policy_number": product.get("policy_number") or "",
                    "source_type": product.get("source_type") or "",
                }
            )

    products_flat = [deduped_map[key] for key in deduped_order]
    company_debug = apply_company_name_inference(products_flat)

    by_company: dict[str, list[dict[str, Any]]] = {}
    company_meta: dict[str, dict[str, Any]] = {}
    for product in products_flat:
        company_name = product.get("company_name") or "보험회사 미상"
        by_company.setdefault(company_name, []).append(product)
        if company_name not in company_meta:
            company_meta[company_name] = {
                "company_name": product.get("company_name"),
                "company_code": product.get("company_code"),
                "home_page": product.get("home_page"),
                "phone_no": product.get("phone_no"),
            }

    company_groups: list[dict[str, Any]] = []
    total_coverage = 0
    active_product_count = 0
    product_count = 0

    for company_name in sorted(by_company.keys()):
        product_list = by_company[company_name]
        product_list.sort(
            key=lambda row: (
                int(row.get("_status_rank", 99) or 99),
                -(int(row.get("_sort_start") or "0") or 0),
            )
        )
        meta = company_meta.get(company_name) or {}
        active_count = sum(1 for row in product_list if row.get("_active"))
        total_premium = 0
        for row in product_list:
            digits = "".join(c for c in str(row.get("premium") or "") if c.isdigit())
            if digits:
                try:
                    total_premium += int(digits)
                except ValueError:
                    pass
        for row in product_list:
            row.pop("_status_rank", None)
            row.pop("_active", None)
            row.pop("_sort_start", None)
            total_coverage += int(row.get("coverage_count") or 0)
            product_count += 1
            if contract_is_active_status(str(row.get("contract_status") or "")):
                active_product_count += 1

        group_inferred = any(bool(p.get("company_inferred")) for p in product_list)
        company_groups.append(
            {
                "company_name": meta.get("company_name") or company_name,
                "company_code": meta.get("company_code") or "",
                "home_page": str(meta.get("home_page") or "").strip(),
                "phone_no": str(meta.get("phone_no") or "").strip(),
                "phone_no_display": _format_phone_display(str(meta.get("phone_no") or "")),
                "contract_count": len(product_list),
                "active_contract_count": active_count,
                "ended_contract_count": len(product_list) - active_count,
                "total_premium": total_premium,
                "company_inferred": group_inferred,
                "products": product_list,
            }
        )

    empty["insured_name"] = (customer_name or "").strip()
    empty["company_groups"] = company_groups
    empty["counts"] = {
        "company_count": len(company_groups),
        "product_count": product_count,
        "coverage_count": total_coverage,
        "active_product_count": active_product_count,
    }
    empty["debug"] = {
        "original_contract_total": original_total,
        "excluded_as_other_insured_count": excluded_other,
        "unknown_insured_contract_count": unknown_cov,
        "unknown_contracts_preview": unknown_preview,
        "payment_estimated_count": payment_estimated_count,
        "payment_excluded_by_insured_count": payment_excluded_by_insured_count,
        "deduped_product_count": deduped_count,
        "duplicate_products_preview": duplicate_preview,
        **company_debug,
    }
    return empty


def build_summary_json_package(
    raw_response: Any,
    normalized_payload: Any,
    customer_name: str,
    *,
    preserve_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """normalized + insured_summary → summary_json 저장용."""
    norm = resolve_normalized_payload(raw_response, normalized_payload)
    insured_summary = build_insured_contract_summary(norm, customer_name)
    package: dict[str, Any] = {
        "insured_summary": insured_summary,
        "counts": norm.get("counts") if isinstance(norm.get("counts"), dict) else {},
    }
    if isinstance(preserve_meta, dict):
        for key in ("imported_from_success_program", "source_db", "source_record_id", "source_created_at"):
            if key in preserve_meta:
                package[key] = preserve_meta[key]
    return package
