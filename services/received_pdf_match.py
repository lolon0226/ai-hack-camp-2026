# -*- coding: utf-8 -*-
"""Print Receiver PDF OCR 추출·고객 매칭(심평원·AI 분석 미사용)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from services.persistent_store import (
    PersistentStoreConfigError,
    _identity_hash,
    _phone_hash,
    get_received_document_by_id,
    is_search_hash_secret_configured,
    list_customer_match_targets,
    update_operator_received_document_ocr,
)

_MATCH_STATUS_AUTO = "auto_matched"
_MATCH_STATUS_REVIEW = "review_required"
_MATCH_STATUS_UNMATCHED = "unmatched"

_RE_VISIT_DATE = re.compile(r"(\d{4})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})")
_RE_PHONE = re.compile(r"(01[0-9])[-\s]?(\d{3,4})[-\s]?(\d{4})")
_RE_RRN = re.compile(r"(\d{6})[-\s]?([1-4]\d{6})")
_RE_AMOUNT = re.compile(
    r"(총액|진료비\s*총액|합계|본인부담금?|납부금액?|실제\s*납부|카드|현금)\s*[:：]?\s*([\d,]+)\s*원?",
    re.IGNORECASE,
)


def normalize_person_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def normalize_phone_digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_rrn_digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))[:13]


def mask_phone_number(value: str) -> str:
    digits = normalize_phone_digits(value)
    if len(digits) <= 3:
        return "***"
    return digits[:3] + ("*" * max(len(digits) - 3, 4))


def mask_rrn(value: str) -> str:
    digits = normalize_rrn_digits(value)
    if len(digits) >= 6:
        return f"{digits[:6]}-*******"
    return "*******"


def _normalize_visit_dates_from_text(text: str) -> list[str]:
    dates: list[str] = []
    for match in _RE_VISIT_DATE.finditer(str(text or "")):
        y, m, d = match.group(1), match.group(2).zfill(2), match.group(3).zfill(2)
        dates.append(f"{y}-{m}-{d}")
    return list(dict.fromkeys(dates))


def _parse_amount_token(raw: str) -> int | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _extract_amounts_from_text(text: str) -> dict[str, int | None]:
    amounts: dict[str, int | None] = {
        "total_amount": None,
        "self_pay_amount": None,
        "paid_amount": None,
    }
    for label, raw in _RE_AMOUNT.findall(str(text or "")):
        amount = _parse_amount_token(raw)
        if amount is None:
            continue
        lowered = str(label).replace(" ", "")
        if "본인" in lowered:
            amounts["self_pay_amount"] = amount
        elif "납부" in lowered or "카드" in lowered or "현금" in lowered:
            amounts["paid_amount"] = amount
        elif "총" in lowered or "합계" in lowered or "진료비" in lowered:
            amounts["total_amount"] = amount
    return amounts


def _read_pdf_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-untyped]

        reader = PdfReader(str(pdf_path))
        chunks: list[str] = []
        for page in reader.pages[:20]:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks)
    except Exception:
        pass
    try:
        raw = pdf_path.read_bytes()
        decoded = raw.decode("utf-8", errors="ignore")
        if len(decoded.strip()) < 8:
            decoded = raw.decode("latin-1", errors="ignore")
        return decoded
    except OSError:
        return ""


def extract_ocr_from_pdf(
    pdf_path: str | Path,
    *,
    filename: str = "",
    hospital_name: str = "",
    document_type_candidate: str = "",
) -> dict[str, Any]:
    """PDF에서 OCR 항목 6종 추출(데모: 텍스트·파일명 패턴)."""
    path = Path(pdf_path)
    text = _read_pdf_text(path) if path.is_file() else ""
    combined = f"{filename}\n{text}"

    patient_name = ""
    name_match = re.search(r"(?:환자|성명|이름)\s*[:：]\s*([가-힣]{2,5})", combined)
    if name_match:
        patient_name = name_match.group(1).strip()

    hospital = str(hospital_name or "").strip()
    if not hospital:
        hosp_match = re.search(r"(?:병원|의원|약국|기관)\s*[:：]?\s*([가-힣0-9A-Za-z\s]{2,30})", combined)
        if hosp_match:
            hospital = hosp_match.group(1).strip()

    visit_dates = _normalize_visit_dates_from_text(combined)
    if not visit_dates:
        visit_dates = _normalize_visit_dates_from_text(str(filename or ""))

    amounts = _extract_amounts_from_text(combined)

    phone_raw = ""
    phone_match = _RE_PHONE.search(combined)
    if phone_match:
        phone_raw = "".join(phone_match.groups())

    rrn_raw = ""
    rrn_match = _RE_RRN.search(combined)
    if rrn_match:
        rrn_raw = rrn_match.group(1) + rrn_match.group(2)

    return {
        "patient_name": patient_name,
        "hospital_name": hospital,
        "visit_dates": visit_dates,
        "amounts": amounts,
        "phone_number_raw": phone_raw,
        "rrn_raw": rrn_raw,
        "document_type_candidate": str(document_type_candidate or "").strip(),
    }


def build_ocr_metadata_block(ocr_raw: dict[str, Any]) -> dict[str, Any]:
    """저장용 OCR 블록(원문 주민·전화 미저장)."""
    amounts_in = ocr_raw.get("amounts") if isinstance(ocr_raw.get("amounts"), dict) else {}
    phone_raw = str(ocr_raw.get("phone_number_raw") or "")
    rrn_raw = str(ocr_raw.get("rrn_raw") or "")
    return {
        "patient_name": str(ocr_raw.get("patient_name") or "").strip(),
        "hospital_name": str(ocr_raw.get("hospital_name") or "").strip(),
        "visit_dates": list(ocr_raw.get("visit_dates") or []),
        "amounts": {
            "total_amount": amounts_in.get("total_amount"),
            "self_pay_amount": amounts_in.get("self_pay_amount"),
            "paid_amount": amounts_in.get("paid_amount"),
        },
        "phone_number_masked": mask_phone_number(phone_raw) if phone_raw else "",
        "rrn_masked": mask_rrn(rrn_raw) if rrn_raw else "",
    }


def _enrich_match_targets_with_flow(
    targets: list[dict[str, Any]],
    flow_store: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(flow_store, dict):
        return targets
    by_key = {str(t.get("customer_key") or ""): dict(t) for t in targets}
    for entry in flow_store.values():
        if not isinstance(entry, dict):
            continue
        ck = str(entry.get("customer_key") or "").strip()
        if not ck or ck not in by_key:
            continue
        customer = entry.get("customer") if isinstance(entry.get("customer"), dict) else {}
        by_key[ck]["identity_digits"] = normalize_rrn_digits(
            str(customer.get("identity") or customer.get("resident") or "")
        )
        by_key[ck]["phone_digits"] = normalize_phone_digits(
            str(customer.get("phone") or customer.get("mobile") or "")
        )
    return list(by_key.values())


def _score_rrn_field(
    ocr_rrn_digits: str,
    target: dict[str, Any],
) -> tuple[bool, bool]:
    """(matched, partial_only)."""
    identity_hash = str(target.get("identity_hash") or "")
    identity_digits = str(target.get("identity_digits") or "")
    if len(ocr_rrn_digits) >= 13 and identity_hash:
        try:
            if _identity_hash(ocr_rrn_digits) == identity_hash:
                return True, False
        except PersistentStoreConfigError:
            return False, False
    if len(ocr_rrn_digits) >= 6 and len(identity_digits) >= 6:
        if ocr_rrn_digits[:6] == identity_digits[:6]:
            return True, True
        if len(ocr_rrn_digits) >= 7 and ocr_rrn_digits[:7] == identity_digits[:7]:
            return True, True
    return False, False


def match_customer_for_ocr(
    ocr_raw: dict[str, Any],
    *,
    flow_store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """이름·전화·주민 3항목 중 2개 이상 → auto_matched."""
    targets = _enrich_match_targets_with_flow(list_customer_match_targets(), flow_store)
    ocr_name = normalize_person_name(str(ocr_raw.get("patient_name") or ""))
    ocr_phone = normalize_phone_digits(str(ocr_raw.get("phone_number_raw") or ""))
    ocr_rrn = normalize_rrn_digits(str(ocr_raw.get("rrn_raw") or ""))

    best: dict[str, Any] = {
        "match_score": 0,
        "matched_fields": [],
        "match_status": _MATCH_STATUS_UNMATCHED,
        "matched_customer_key": None,
        "matched_customer_name": "",
    }

    hash_available = is_search_hash_secret_configured()

    for target in targets:
        matched_fields: list[str] = []
        score = 0

        target_name = normalize_person_name(str(target.get("name") or ""))
        if ocr_name and target_name and ocr_name == target_name:
            matched_fields.append("이름")
            score += 1

        if hash_available and ocr_phone and len(ocr_phone) >= 10:
            try:
                if _phone_hash(ocr_phone) == str(target.get("phone_hash") or ""):
                    matched_fields.append("전화번호")
                    score += 1
            except PersistentStoreConfigError:
                pass

        rrn_matched, rrn_partial = _score_rrn_field(ocr_rrn, target)
        if rrn_matched:
            matched_fields.append(
                "주민번호(생년월일)" if rrn_partial and len(ocr_rrn) < 13 else "주민번호"
            )
            score += 1

        if score > int(best.get("match_score") or 0):
            if score >= 2:
                status = _MATCH_STATUS_AUTO
            elif score == 1:
                status = _MATCH_STATUS_REVIEW
            else:
                status = _MATCH_STATUS_UNMATCHED
            best = {
                "match_score": score,
                "matched_fields": matched_fields,
                "match_status": status,
                "matched_customer_key": target.get("customer_key"),
                "matched_customer_name": target.get("name"),
            }

    return best


def format_match_basis(matched_fields: list[str]) -> str:
    if not matched_fields:
        return "—"
    return "+".join(matched_fields) + " 일치"


def apply_received_pdf_ocr_and_match(
    document_id: int,
    *,
    pdf_path: str | Path,
    filename: str = "",
    hospital_name: str = "",
    document_type_candidate: str = "",
    flow_store: dict[str, Any] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """OCR 추출 후 DB 문서 갱신."""
    ocr_raw = extract_ocr_from_pdf(
        pdf_path,
        filename=filename,
        hospital_name=hospital_name,
        document_type_candidate=document_type_candidate,
    )
    ocr_block = build_ocr_metadata_block(ocr_raw)
    match = match_customer_for_ocr(ocr_raw, flow_store=flow_store)

    metadata: dict[str, Any] = dict(extra_metadata or {})
    metadata["ocr"] = ocr_block
    metadata["match"] = {
        "match_score": match.get("match_score"),
        "matched_fields": match.get("matched_fields"),
        "match_status": match.get("match_status"),
        "matched_customer_key": match.get("matched_customer_key"),
    }
    if hospital_name and not metadata.get("hospital_name"):
        metadata["hospital_name"] = hospital_name

    customer_key = match.get("matched_customer_key")
    linked_name = ""
    if match.get("match_status") == _MATCH_STATUS_AUTO:
        linked_name = str(match.get("matched_customer_name") or "")

    update_operator_received_document_ocr(
        document_id,
        metadata_json=metadata,
        ocr_status="completed",
        customer_key=str(customer_key) if customer_key else None,
        linked_customer_name=linked_name,
    )
    return get_received_document_by_id(document_id)


def is_ocr_auto_matched(doc: dict[str, Any]) -> bool:
    from services.persistent_store import _parse_received_document_metadata

    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    if not meta:
        meta = _parse_received_document_metadata(doc.get("metadata_json"))
    match = meta.get("match") if isinstance(meta.get("match"), dict) else {}
    return (
        str(match.get("match_status") or "") == _MATCH_STATUS_AUTO
        and str(doc.get("ocr_status") or "") == "completed"
    )
