# -*- coding: utf-8 -*-
"""Print Receiver PDF OCR 추출·고객 매칭(심평원·AI 분석 미사용)."""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from services.persistent_store import (
    PersistentStoreConfigError,
    _identity_hash,
    _parse_received_document_metadata,
    _phone_hash,
    get_received_document_by_id,
    is_search_hash_secret_configured,
    list_customer_match_targets,
    update_operator_received_document_ocr,
)

logger = logging.getLogger(__name__)

_MATCH_STATUS_AUTO = "auto_matched"
_MATCH_STATUS_REVIEW = "review_required"
_MATCH_STATUS_UNMATCHED = "unmatched"

_PLACEHOLDER_HOSPITALS = frozenset({"TEST_HOSPITAL", "TEST HOSPITAL"})
_DEFAULT_DOC_TYPE = "병원출력물"
_MIN_PDF_TEXT_LAYER = 40
_MAX_OCR_PAGES_FAST = 2
_MAX_OCR_PAGES_STRONG = 10
_FAST_DPI = 200
_STRONG_DPIS = (300, 200)
_OCR_TIMEOUT_SECONDS = 20
_PREVIEW_MAX_LEN = 1000
_OCR_TEXT_EMPTY_MESSAGE = "OCR 실패: 텍스트 추출 불가"
_TESSERACT_NOT_FOUND_MESSAGE = "Tesseract OCR 엔진 경로를 찾을 수 없습니다"
_OCR_TIMEOUT_MESSAGE = "OCR 시간 초과(20초)"

_RE_VISIT_DATE = re.compile(r"(\d{4})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})")
_RE_VISIT_DATE_KR = re.compile(
    r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
)
_RE_VISIT_DATE_COMPACT = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
_RE_PHONE = re.compile(r"(01[0-9])[-\s]?(\d{3,4})[-\s]?(\d{4})")
_RE_PHONE_PLAIN = re.compile(r"(01[016789]\d{8})")
_RE_RRN = re.compile(r"(\d{6})[-\s]?([1-4]\d{6})")
_RE_NAME_LABEL = re.compile(
    r"(?:성명|환자명|수진자|가입자|피보험자)\s*[:：]?\s*([가-힣]{2,4})"
)
_RE_HOSPITAL_LABEL = re.compile(
    r"(?:요양기관명|병원명|의료기관명|의료기관|기관명|발행기관)\s*[:：]?\s*"
    r"([가-힣0-9A-Za-z()（）\s]{2,40})"
)
_RE_HOSPITAL_SUFFIX = re.compile(
    r"([가-힣\s]{4,60}?(?:정형외과|의원|병원|약국|내과))"
)
_RE_HOSPITAL_SUFFIX_LEGACY = re.compile(
    r"([가-힣]{2,30}(?:의료원|클리닉|센터|외과|정형|한의원|소아과|이비인후과|피부과))"
)
_RE_AMOUNT_COMMA = re.compile(r"(?<!\d)([\d,]{4,15})(?!\d)")
_RE_AMOUNT_PLAIN = re.compile(r"(?<!\d)(\d{4,7})(?!\d)")
_RE_VISIT_LABEL_DATE = re.compile(
    r"(?:진료일|진료기간|내원일|일자)\s*[:：]?\s*"
    r"(\d{4}[-./년\s]*\d{1,2}[-./월\s]*\d{1,2}(?:\s*일)?|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일|\d{8})"
)
_RE_BIZ_NUMBER = re.compile(r"\d{3}[-\s]?\d{2}[-\s]?\d{5}")
_RE_AMOUNT_WON = re.compile(r"([\d,]{1,15})\s*원")
_RE_AMOUNT_LABELED = re.compile(
    r"(환자부담총액|본인부담금?|납부금액?|수납금액|합계|카드|현금|총액|진료비\s*총액)"
    r"\s*[:：]?\s*([\d,]{1,15})\s*원?",
    re.IGNORECASE,
)

_YEAR_MIN = 2000
_YEAR_MAX = 2035
_VISIT_YEAR_MIN = 2020
_MAX_UNLABELED_VISIT_DATES = 5
_AMOUNT_PRIORITY_LABELS = ("환자부담", "본인부담", "납부", "수납", "합계")
_HOSPITAL_SUFFIX_ENDINGS = ("정형외과", "의원", "병원", "약국", "내과")
_HOSPITAL_PREFIX_LABELS = (
    "요양기관명",
    "의료기관명",
    "사업장명",
    "사업자명",
    "발행기관",
    "기관명",
    "상호",
)
_AMOUNT_SIMILAR_REL_TOL = 0.03
_AMOUNT_SIMILAR_ABS_TOL = 1000

_NAME_BLOCKLIST_EXACT = frozenset(
    {
        "나이",
        "성별",
        "주소",
        "전화",
        "금액",
        "합계",
        "진료",
        "병원",
        "내과",
        "외과",
        "환자",
        "성명",
        "일자",
        "카드",
        "현금",
        "납부",
        "총액",
        "영수",
        "부담",
        "수납",
        "내원",
        "기간",
        "번호",
        "종류",
        "구분",
        "비고",
        "합계",
        "소계",
        "면허",
        "종별",
        "차트",
        "서명",
        "발행",
        "요양",
        "기관",
        "의료",
        "보험",
        "약국",
        "의원",
        "센터",
        "클리닉",
        "정형",
        "한의",
        "산부",
        "소아",
        "이비",
        "안과",
        "치과",
        "피부",
        "전화",
        "팩스",
        "주민",
        "생년",
        "월일",
        "년월",
        "일시",
    }
)

_NAME_BLOCKLIST_SUBSTR = (
    "병원",
    "의원",
    "약국",
    "보험",
    "영수증",
    "진료비",
    "내역",
    "청구",
    "발급",
    "요양",
    "기관",
    "센터",
    "클리닉",
)


def normalize_person_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def normalize_hospital_name(value: str) -> str:
    """공백 제거 + 상호·요양기관명 등 라벨성 접두어 제거."""
    collapsed = re.sub(r"\s+", "", str(value or "")).strip()
    if not collapsed:
        return ""
    upper = collapsed.upper()
    if upper in _PLACEHOLDER_HOSPITALS or "TEST" in upper:
        return ""
    for prefix in sorted(_HOSPITAL_PREFIX_LABELS, key=len, reverse=True):
        if collapsed.startswith(prefix) and len(collapsed) > len(prefix) + 3:
            collapsed = collapsed[len(prefix) :]
    return collapsed.strip()


def _visit_year_max() -> int:
    return datetime.date.today().year + 1


def _collapse_text_spaces(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def fuzzy_name_in_text(name: str, text: str) -> bool:
    """고객명 OCR 띄어쓰기 변형(김 도 무 등) 허용."""
    norm_name = normalize_person_name(name)
    if len(norm_name) < 2:
        return False
    if norm_name in _collapse_text_spaces(text):
        return True
    spaced_pattern = r"\s*".join(re.escape(ch) for ch in norm_name)
    return bool(re.search(spaced_pattern, str(text or "")))


def _name_matches_target(target_name: str, ocr_name: str, ocr_text: str) -> bool:
    tn = normalize_person_name(target_name)
    if not tn:
        return False
    on = normalize_person_name(ocr_name)
    if on and on == tn:
        return True
    return fuzzy_name_in_text(tn, ocr_text)


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


def _mask_sensitive_in_preview(text: str) -> str:
    masked = str(text or "")

    def _rrn_repl(match: re.Match[str]) -> str:
        return mask_rrn(match.group(0))

    def _phone_repl(match: re.Match[str]) -> str:
        return mask_phone_number(match.group(0))

    masked = _RE_RRN.sub(_rrn_repl, masked)
    masked = _RE_PHONE.sub(_phone_repl, masked)
    masked = _RE_PHONE_PLAIN.sub(_phone_repl, masked)
    return masked


def _sanitize_hospital_hint(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if cleaned.upper() in _PLACEHOLDER_HOSPITALS:
        return ""
    return cleaned


def _tesseract_candidate_paths() -> list[Path]:
    """Windows 기본 설치 경로 + PATH."""
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Programs" / "Tesseract-OCR" / "tesseract.exe"
        )
    return candidates


def _find_tesseract_executable() -> str:
    which_path = shutil.which("tesseract")
    if which_path:
        resolved = Path(which_path).resolve()
        if resolved.is_file():
            return str(resolved)
    for candidate in _tesseract_candidate_paths():
        if candidate.is_file():
            return str(candidate.resolve())
    return ""


def _configure_tesseract() -> tuple[str, str]:
    """tesseract.exe 탐색 후 pytesseract.tesseract_cmd 설정."""
    path = _find_tesseract_executable()
    if not path:
        return "", ""
    version = ""
    try:
        import pytesseract  # type: ignore[import-untyped]

        pytesseract.pytesseract.tesseract_cmd = path
    except ImportError:
        logger.warning("pytesseract not installed; tesseract at %s", path)
        return path, ""
    try:
        out = subprocess.check_output(
            [path, "--version"],
            text=True,
            errors="ignore",
            timeout=10,
        )
        version = str(out or "").strip().split("\n")[0]
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("tesseract --version failed: %s", exc)
    return path, version


def _env_strong_ocr_enabled() -> bool:
    return os.getenv("PRINT_RECEIVER_OCR_STRONG", "").strip() in ("1", "true", "yes")


def _ocr_timed_out(deadline: float) -> bool:
    return time.monotonic() >= deadline


def _read_pdf_text_layer(pdf_path: Path, *, max_pages: int) -> tuple[str, int]:
    page_count = 0
    limit = max(1, int(max_pages))
    try:
        from pypdf import PdfReader  # type: ignore[import-untyped]

        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)
        chunks: list[str] = []
        for page in reader.pages[:limit]:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks), page_count
    except Exception as exc:
        logger.debug("pdf text layer failed: %s", exc)
    try:
        import fitz  # type: ignore[import-untyped]

        doc = fitz.open(str(pdf_path))
        page_count = doc.page_count
        chunks: list[str] = []
        for idx in range(min(page_count, limit)):
            chunks.append(doc.load_page(idx).get_text("text") or "")
        doc.close()
        return "\n".join(chunks), page_count
    except Exception:
        return "", page_count


def _image_variants_strong(img: Any) -> list[tuple[str, Any]]:
    from PIL import Image, ImageEnhance, ImageOps  # type: ignore[import-untyped]

    variants: list[tuple[str, Any]] = [("original", img)]
    gray = ImageOps.grayscale(img).convert("RGB")
    variants.append(("grayscale", gray))
    w, h = img.size
    if w * h < 16_000_000:
        scaled = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
        variants.append(("scale_2x", scaled))
    g = ImageOps.grayscale(img)
    bw = g.point(lambda x: 255 if x > 140 else 0).convert("RGB")
    variants.append(("binarize", bw))
    sharp = ImageEnhance.Contrast(img).enhance(1.8)
    sharp = ImageEnhance.Sharpness(sharp).enhance(2.0)
    variants.append(("contrast_sharp", sharp))
    return variants


def _tesseract_on_image(
    img: Any,
    tried_langs: list[str],
    *,
    langs: tuple[str, ...] = ("kor+eng", "eng"),
    allow_default: bool = False,
) -> tuple[str, str | None]:
    import pytesseract  # type: ignore[import-untyped]

    for lang in langs:
        try:
            text = (
                pytesseract.image_to_string(img, lang=lang)
                if lang
                else pytesseract.image_to_string(img)
            )
            tried_langs.append(lang or "default")
            if str(text or "").strip():
                return str(text), lang or "default"
        except Exception as exc:
            tried_langs.append(f"{lang or 'default'}:err")
            logger.debug("tesseract lang=%s failed: %s", lang, exc)
    if allow_default:
        try:
            text = pytesseract.image_to_string(img)
            tried_langs.append("default")
            if str(text or "").strip():
                return str(text), "default"
        except Exception as exc:
            tried_langs.append("default:err")
            logger.debug("tesseract default failed: %s", exc)
    return "", None


def _render_pdf_pages(pdf_path: Path, dpi: int, *, max_pages: int) -> list[Any]:
    import fitz  # type: ignore[import-untyped]
    from PIL import Image  # type: ignore[import-untyped]

    images: list[Any] = []
    doc = fitz.open(str(pdf_path))
    try:
        limit = min(doc.page_count, max(1, int(max_pages)))
        for idx in range(limit):
            page = doc.load_page(idx)
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    finally:
        doc.close()
    return images


def _ocr_page_fast(img: Any, tried_langs: list[str], deadline: float) -> str:
    from PIL import ImageOps  # type: ignore[import-untyped]

    if _ocr_timed_out(deadline):
        return ""
    gray = ImageOps.grayscale(img).convert("RGB")
    text, _ = _tesseract_on_image(
        gray, tried_langs, langs=("kor+eng", "eng"), allow_default=False
    )
    if text.strip():
        return text
    if _ocr_timed_out(deadline):
        return ""
    text, _ = _tesseract_on_image(
        img, tried_langs, langs=("kor+eng", "eng"), allow_default=False
    )
    return text


def _run_fast_ocr_on_pdf(
    pdf_path: Path,
    *,
    max_pages: int,
    dpi: int,
    tried_langs: list[str],
    deadline: float,
) -> tuple[str, list[int], str, int, bool]:
    """200DPI · 앞 N페이지 · grayscale → (실패 시) 원본, kor+eng/eng만."""
    ocr_chunks: list[str] = []
    page_lengths: list[int] = []
    timed_out = False

    try:
        page_images = _render_pdf_pages(pdf_path, dpi, max_pages=max_pages)
    except Exception as exc:
        logger.warning("fast ocr render failed: %s", exc)
        return "", [], "tesseract_fast", dpi, timed_out

    for img in page_images:
        if _ocr_timed_out(deadline):
            timed_out = True
            break
        page_text = _ocr_page_fast(img, tried_langs, deadline)
        page_lengths.append(len(page_text.strip()))
        if page_text.strip():
            ocr_chunks.append(page_text)
        if _ocr_timed_out(deadline):
            timed_out = True
            break

    return "\n\n".join(ocr_chunks), page_lengths, "tesseract_fast", dpi, timed_out


def _run_strong_ocr_on_pdf(
    pdf_path: Path,
    *,
    max_pages: int,
    tried_langs: list[str],
    deadline: float,
    full_preprocess: bool = True,
) -> tuple[str, list[int], str, int, bool]:
    """강한 OCR: 300/200 DPI · 다중 전처리 · lang 기본값 포함."""
    ocr_chunks: list[str] = []
    page_lengths: list[int] = []
    used_dpi = 0
    timed_out = False

    for dpi in _STRONG_DPIS:
        if _ocr_timed_out(deadline):
            timed_out = True
            break
        try:
            page_images = _render_pdf_pages(pdf_path, dpi, max_pages=max_pages)
            used_dpi = dpi
        except Exception as exc:
            logger.warning("strong ocr render dpi=%s failed: %s", dpi, exc)
            continue
        if not page_images:
            continue
        use_all_variants = bool(full_preprocess) or _env_strong_ocr_enabled()
        for img in page_images:
            if _ocr_timed_out(deadline):
                timed_out = True
                break
            page_text_parts: list[str] = []
            if use_all_variants:
                variants = _image_variants_strong(img)
            else:
                from PIL import ImageOps  # type: ignore[import-untyped]

                gray = ImageOps.grayscale(img).convert("RGB")
                variants = [("grayscale", gray), ("original", img)]
            for _variant_name, variant_img in variants:
                if _ocr_timed_out(deadline):
                    timed_out = True
                    break
                text, _ = _tesseract_on_image(
                    variant_img,
                    tried_langs,
                    langs=("kor+eng", "eng"),
                    allow_default=True,
                )
                if text.strip():
                    page_text_parts.append(text)
            page_text = "\n".join(page_text_parts)
            page_lengths.append(len(page_text.strip()))
            if page_text.strip():
                ocr_chunks.append(page_text)
        if "".join(ocr_chunks).strip():
            break
        if timed_out:
            break

    return "\n\n".join(ocr_chunks), page_lengths, "tesseract_strong", used_dpi, timed_out


def _extract_document_text(
    pdf_path: Path,
    *,
    strong_ocr: bool = False,
) -> tuple[str, dict[str, Any]]:
    """PDF 텍스트 레이어 → 부족 시 빠른/강한 Tesseract OCR."""
    use_strong = bool(strong_ocr)
    max_pages = _MAX_OCR_PAGES_STRONG if use_strong else _MAX_OCR_PAGES_FAST
    deadline = time.monotonic() + _OCR_TIMEOUT_SECONDS

    debug: dict[str, Any] = {
        "pdf_path": str(pdf_path),
        "file_exists": pdf_path.is_file(),
        "file_size": pdf_path.stat().st_size if pdf_path.is_file() else 0,
        "page_count": 0,
        "pdf_text_len": 0,
        "ocr_text_len": 0,
        "extraction_source": "",
        "used_dpi": 0,
        "max_pages": max_pages,
        "ocr_mode": "strong" if use_strong else "fast",
        "ocr_timeout_seconds": _OCR_TIMEOUT_SECONDS,
        "timed_out": False,
        "tesseract_path": "",
        "tesseract_version": "",
        "tried_langs": [],
        "page_ocr_lengths": [],
        "ocr_error_message": "",
    }
    tess_path, tess_ver = _configure_tesseract()
    debug["tesseract_path"] = tess_path
    debug["tesseract_version"] = tess_ver

    if not pdf_path.is_file():
        debug["ocr_error_message"] = "pdf file missing"
        return "", debug

    pdf_text, page_count = _read_pdf_text_layer(pdf_path, max_pages=max_pages)
    debug["page_count"] = page_count
    debug["pdf_text_len"] = len(pdf_text.strip())

    ocr_text = ""
    if len(pdf_text.strip()) >= _MIN_PDF_TEXT_LAYER:
        debug["extraction_source"] = "pdf_text_layer"
        debug["ocr_text_len"] = 0
        combined = pdf_text
    else:
        if not tess_path:
            debug["ocr_error_message"] = _TESSERACT_NOT_FOUND_MESSAGE
            debug["extraction_source"] = "pdf_text_layer_only"
            combined = pdf_text
        else:
            try:
                if use_strong:
                    ocr_text, page_lengths, source, used_dpi, timed_out = (
                        _run_strong_ocr_on_pdf(
                            pdf_path,
                            max_pages=max_pages,
                            tried_langs=debug["tried_langs"],
                            deadline=deadline,
                        )
                    )
                else:
                    ocr_text, page_lengths, source, used_dpi, timed_out = (
                        _run_fast_ocr_on_pdf(
                            pdf_path,
                            max_pages=max_pages,
                            dpi=_FAST_DPI,
                            tried_langs=debug["tried_langs"],
                            deadline=deadline,
                        )
                    )
                debug["page_ocr_lengths"] = page_lengths
                debug["extraction_source"] = source
                debug["used_dpi"] = used_dpi
                debug["ocr_text_len"] = len(ocr_text.strip())
                debug["timed_out"] = timed_out
                if timed_out:
                    debug["ocr_error_message"] = _OCR_TIMEOUT_MESSAGE
                if not ocr_text.strip() and pdf_text.strip():
                    debug["extraction_source"] = "pdf_text_layer+ocr_empty"
                combined = f"{pdf_text}\n{ocr_text}".strip()
            except Exception as exc:
                debug["ocr_error_message"] = str(exc)
                logger.exception("ocr failed path=%s mode=%s", pdf_path, debug["ocr_mode"])
                combined = pdf_text

    if not combined.strip():
        debug["ocr_error_message"] = debug.get("ocr_error_message") or _OCR_TEXT_EMPTY_MESSAGE

    return combined, debug


def _date_str(y: str, m: str, d: str) -> str:
    return f"{y}-{str(m).zfill(2)}-{str(d).zfill(2)}"


def _is_valid_calendar_date(y: str, m: str, d: str) -> bool:
    try:
        yi, mi, di = int(y), int(m), int(d)
    except ValueError:
        return False
    if not (_YEAR_MIN <= yi <= _YEAR_MAX):
        return False
    if not (1 <= mi <= 12 and 1 <= di <= 31):
        return False
    import calendar

    return di <= calendar.monthrange(yi, mi)[1]


def _forbidden_date_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    source = str(text or "")
    for match in _RE_RRN.finditer(source):
        spans.append((match.start(), match.end()))
    for match in _RE_PHONE.finditer(source):
        spans.append((match.start(), match.end()))
    for match in _RE_PHONE_PLAIN.finditer(source):
        spans.append((match.start(), match.end()))
    for match in _RE_BIZ_NUMBER.finditer(source):
        spans.append((match.start(), match.end()))
    return spans


def _span_overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for s, e in spans:
        if start < e and end > s:
            return True
    return False


def _normalize_date_match(
    y: str, m: str, d: str, *, raw: str, reason: str = ""
) -> dict[str, Any] | None:
    if not _is_valid_calendar_date(y, m, d):
        return None
    return {"value": _date_str(y, m, d), "raw": raw, "reason": reason}


def _collect_raw_date_candidates(text: str) -> list[dict[str, Any]]:
    source = str(text or "")
    forbidden = _forbidden_date_spans(source)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(candidate: dict[str, Any] | None, start: int, end: int) -> None:
        if not candidate:
            return
        if _span_overlaps(start, end, forbidden):
            return
        val = str(candidate["value"])
        if val in seen:
            return
        seen.add(val)
        candidates.append(candidate)

    for match in _RE_VISIT_DATE.finditer(source):
        _add(
            _normalize_date_match(
                match.group(1),
                match.group(2),
                match.group(3),
                raw=match.group(0),
                reason="pattern",
            ),
            match.start(),
            match.end(),
        )
    for match in _RE_VISIT_DATE_KR.finditer(source):
        _add(
            _normalize_date_match(
                match.group(1),
                match.group(2),
                match.group(3),
                raw=match.group(0),
                reason="pattern_kr",
            ),
            match.start(),
            match.end(),
        )
    for match in _RE_VISIT_DATE_COMPACT.finditer(source):
        tail = source[match.end() : match.end() + 2]
        if tail.startswith("-") and len(tail) > 1 and tail[1].isdigit():
            continue
        _add(
            _normalize_date_match(
                match.group(1),
                match.group(2),
                match.group(3),
                raw=match.group(0),
                reason="compact",
            ),
            match.start(),
            match.end(),
        )
    return candidates


def _visit_year_in_range(year: int) -> bool:
    return _VISIT_YEAR_MIN <= year <= _visit_year_max()


def _reject_date_candidates(
    raw_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in raw_candidates:
        val = str(item.get("value") or "")
        parts = val.split("-")
        if len(parts) != 3 or not _is_valid_calendar_date(parts[0], parts[1], parts[2]):
            rejected.append({**item, "reject_reason": "invalid_range"})
            continue
        year = int(parts[0])
        if not _visit_year_in_range(year):
            rejected.append({**item, "reject_reason": "year_out_of_visit_range"})
            continue
        accepted.append(item)
    return accepted, rejected


def _resolve_conflicting_visit_years(
    accepted: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """동일 월·일에 2005/2025 등이 함께 있으면 최신 연도(진료 범위 내)만 유지."""
    by_mmdd: dict[str, list[dict[str, Any]]] = {}
    for item in accepted:
        val = str(item.get("value") or "")
        parts = val.split("-")
        if len(parts) != 3:
            continue
        by_mmdd.setdefault(f"{parts[1]}-{parts[2]}", []).append(item)

    kept: list[dict[str, Any]] = []
    extra_rejected: list[dict[str, Any]] = []
    for group in by_mmdd.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        best = max(group, key=lambda c: int(str(c.get("value") or "0")[:4]))
        kept.append(best)
        for item in group:
            if item is not best:
                extra_rejected.append(
                    {**item, "reject_reason": "superseded_by_later_year_in_document"}
                )
    return kept, extra_rejected


def _extract_visit_dates(
    text: str,
    *,
    exclude: set[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    exclude = exclude or set()
    raw_all = _collect_raw_date_candidates(text)
    accepted, rejected = _reject_date_candidates(raw_all)
    accepted, conflict_rejected = _resolve_conflicting_visit_years(accepted)
    rejected = rejected + conflict_rejected
    labeled: list[str] = []
    source = str(text or "")

    for match in _RE_VISIT_LABEL_DATE.finditer(source):
        chunk = match.group(1)
        for item in _collect_raw_date_candidates(chunk):
            val = str(item.get("value") or "")
            if val and val not in exclude:
                labeled.append(val)

    labeled = list(dict.fromkeys(labeled))
    unlabeled = [
        str(c["value"])
        for c in accepted
        if str(c.get("value") or "") not in labeled and str(c.get("value") or "") not in exclude
    ]
    unlabeled = list(dict.fromkeys(unlabeled))[:_MAX_UNLABELED_VISIT_DATES]
    filtered = list(dict.fromkeys(labeled + unlabeled))
    return filtered, raw_all, accepted, [str(r.get("value") or r.get("raw") or "") for r in rejected]


def _file_date_candidate_from_filename(filename: str) -> str:
    name_only = str(filename or "")
    raw = _collect_raw_date_candidates(name_only)
    accepted, _ = _reject_date_candidates(raw)
    return str(accepted[0]["value"]) if accepted else ""


def _parse_amount_token(raw: str) -> int | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    if value <= 0 or value > 100_000_000:
        return None
    return value


def _amount_forbidden_spans(text: str) -> list[tuple[int, int]]:
    spans = _forbidden_date_spans(text)
    source = str(text or "")
    for match in _RE_VISIT_DATE.finditer(source):
        spans.append((match.start(), match.end()))
    for match in _RE_VISIT_DATE_KR.finditer(source):
        spans.append((match.start(), match.end()))
    for match in _RE_VISIT_DATE_COMPACT.finditer(source):
        spans.append((match.start(), match.end()))
    return spans


def _collect_loose_amount_candidates(text: str) -> list[dict[str, Any]]:
    source = str(text or "")
    forbidden = _amount_forbidden_spans(source)
    found: list[dict[str, Any]] = []
    seen_values: set[int] = set()

    def _add(raw: str, label: str, start: int, end: int) -> None:
        if _span_overlaps(start, end, forbidden):
            return
        amount = _parse_amount_token(raw)
        if amount is None or amount in seen_values:
            return
        seen_values.add(amount)
        found.append({"label": label, "raw": raw, "value": amount})

    for match in _RE_AMOUNT_WON.finditer(source):
        _add(match.group(1), "원단위", match.start(), match.end())
    for match in _RE_AMOUNT_COMMA.finditer(source):
        _add(match.group(1), "쉼표숫자", match.start(), match.end())
    for match in _RE_AMOUNT_PLAIN.finditer(source):
        _add(match.group(1), "숫자만", match.start(), match.end())
    return found


def _amount_near_priority_label(
    text: str, candidates: list[dict[str, Any]]
) -> tuple[int | None, int | None, str]:
    """환자부담·본인부담·납부·수납·합계 근처 금액 우선."""
    source = str(text or "")
    priority_values: list[tuple[int, str, int]] = []
    for label_key in _AMOUNT_PRIORITY_LABELS:
        for match in re.finditer(re.escape(label_key), source):
            window = source[match.end() : match.end() + 80]
            for cand in candidates:
                raw = str(cand.get("raw") or "")
                if raw and raw in window:
                    priority_values.append(
                        (int(cand["value"]), label_key, match.start())
                    )
    if not priority_values:
        return None, None, ""
    priority_values.sort(key=lambda item: item[2])
    total_hint = next(
        (v for v, lbl, _ in priority_values if lbl in ("합계", "환자부담")),
        None,
    )
    self_hint = next(
        (v for v, lbl, _ in priority_values if lbl in ("본인부담", "납부", "수납")),
        None,
    )
    if total_hint is not None and self_hint is not None:
        return total_hint, self_hint, "priority_labels"
    if total_hint is not None:
        return total_hint, None, "priority_label_total"
    first_val = priority_values[0][0]
    second_val = priority_values[1][0] if len(priority_values) > 1 else None
    return first_val, second_val, "priority_label_first"


def _extract_amounts_from_text(
    text: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], str]:
    source = str(text or "")
    raw_candidates: list[dict[str, Any]] = []
    amounts: dict[str, Any] = {
        "total_amount": None,
        "self_pay_amount": None,
        "paid_amount": None,
        "candidates": [],
        "confirmation_required": False,
    }
    selected_reason = ""

    for label, raw in _RE_AMOUNT_LABELED.findall(source):
        amount = _parse_amount_token(raw)
        if amount is None:
            continue
        entry = {"label": label, "raw": raw, "value": amount, "source": "labeled"}
        raw_candidates.append(entry)
        lowered = str(label).replace(" ", "")
        if "본인" in lowered or "환자부담" in lowered:
            amounts["self_pay_amount"] = amount
        elif "납부" in lowered or "수납" in lowered:
            amounts["paid_amount"] = amount
        elif "카드" in lowered or "현금" in lowered:
            if amounts["paid_amount"] is None:
                amounts["paid_amount"] = amount
        elif "총" in lowered or "합계" in lowered or "진료비" in lowered:
            amounts["total_amount"] = amount

    loose_candidates = _collect_loose_amount_candidates(source)
    for entry in loose_candidates:
        if not any(c.get("value") == entry["value"] for c in raw_candidates):
            raw_candidates.append(entry)

    all_values = sorted(
        {int(c["value"]) for c in raw_candidates if c.get("value") is not None},
        reverse=True,
    )
    amounts["candidates"] = all_values

    priority_total, priority_self, priority_reason = _amount_near_priority_label(
        source, raw_candidates
    )
    if priority_total is not None:
        if amounts["total_amount"] is None:
            amounts["total_amount"] = priority_total
        if amounts["self_pay_amount"] is None and priority_self is not None:
            amounts["self_pay_amount"] = priority_self
        selected_reason = priority_reason or "priority_labels"

    if amounts["total_amount"] is None and all_values:
        amounts["total_amount"] = all_values[0]
        selected_reason = selected_reason or "max_candidate"

    if amounts["self_pay_amount"] is None and len(all_values) >= 2:
        for value in all_values[1:]:
            if value != amounts["total_amount"]:
                amounts["self_pay_amount"] = value
                if not selected_reason:
                    selected_reason = "second_largest_candidate"
                break

    if amounts["paid_amount"] is None and amounts["self_pay_amount"] is not None:
        amounts["paid_amount"] = amounts["self_pay_amount"]

    ambiguous = bool(
        len(all_values) >= 3
        and amounts["total_amount"] is not None
        and not str(selected_reason).startswith("priority_label")
        and amounts.get("self_pay_amount") is None
    )
    if ambiguous:
        amounts["confirmation_required"] = True
        selected_reason = selected_reason or "ambiguous_candidates"

    if amounts["confirmation_required"] and amounts["total_amount"] is None and all_values:
        amounts["total_amount"] = all_values[0]

    candidate_mode, display_message = _amount_candidate_display_mode(amounts)
    amounts["candidate_mode"] = candidate_mode
    if candidate_mode:
        amounts["display_message"] = display_message

    return amounts, raw_candidates, dict(amounts), selected_reason


def _amount_values_close(left: int, right: int) -> bool:
    if left <= 0 or right <= 0:
        return False
    diff = abs(left - right)
    return diff <= max(_AMOUNT_SIMILAR_ABS_TOL, int(max(left, right) * _AMOUNT_SIMILAR_REL_TOL))


def _amount_candidate_display_mode(amounts: dict[str, Any]) -> tuple[bool, str]:
    """총액·본인부담·납부가 유사하거나 후보가 애매하면 후보 표시."""
    candidates = [int(v) for v in (amounts.get("candidates") or []) if v]
    picked = [
        int(v)
        for v in (
            amounts.get("total_amount"),
            amounts.get("self_pay_amount"),
            amounts.get("paid_amount"),
        )
        if v
    ]
    if len(picked) >= 2 and all(
        _amount_values_close(picked[0], value) for value in picked[1:]
    ):
        return True, "OCR 추출 금액"
    if len(candidates) >= 2 and _amount_values_close(candidates[0], candidates[1]):
        return True, "금액 후보"
    if amounts.get("confirmation_required"):
        return True, "금액 후보"
    return False, ""


def _is_blocked_name_candidate(name: str) -> bool:
    n = str(name or "").strip()
    if len(n) < 2 or len(n) > 4:
        return True
    if n in _NAME_BLOCKLIST_EXACT:
        return True
    for token in _NAME_BLOCKLIST_SUBSTR:
        if token in n:
            return True
    if any(ch.isdigit() for ch in n):
        return True
    if n.endswith(("원", "증", "서", "표", "료", "금")):
        return True
    return False


def _registered_customer_names(
    flow_store: dict[str, Any] | None = None,
) -> set[str]:
    names: set[str] = set()
    for target in _enrich_match_targets_with_flow(
        list_customer_match_targets(), flow_store
    ):
        n = normalize_person_name(str(target.get("name") or ""))
        if n and not _is_blocked_name_candidate(n):
            names.add(n)
    return names


def _extract_patient_name(
    text: str,
    *,
    registered_names: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    raw_candidates: list[dict[str, Any]] = []
    source = str(text or "")
    fuzzy_matched = ""

    for match in _RE_NAME_LABEL.finditer(source):
        candidate = match.group(1).strip()
        raw_candidates.append(
            {"source": "label", "label": match.group(0), "value": candidate}
        )
        if not _is_blocked_name_candidate(candidate):
            return candidate, raw_candidates, fuzzy_matched

    for reg_name in sorted(registered_names or set(), key=len, reverse=True):
        if _is_blocked_name_candidate(reg_name):
            continue
        if fuzzy_name_in_text(reg_name, source):
            raw_candidates.append({"source": "registered_fuzzy", "value": reg_name})
            fuzzy_matched = reg_name
            return reg_name, raw_candidates, fuzzy_matched

    return "", raw_candidates, fuzzy_matched


def _hospital_name_valid(normalized: str) -> bool:
    if len(normalized) < 4:
        return False
    if "TEST" in normalized.upper():
        return False
    if normalized.upper() in _PLACEHOLDER_HOSPITALS:
        return False
    return any(normalized.endswith(suffix) for suffix in _HOSPITAL_SUFFIX_ENDINGS)


def _extract_hospital_fuzzy_candidates(text: str) -> list[str]:
    source = str(text or "")
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        normalized = normalize_hospital_name(raw)
        if not _hospital_name_valid(normalized):
            return
        if not _sanitize_hospital_hint(normalized):
            return
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    for pattern in (_RE_HOSPITAL_SUFFIX, _RE_HOSPITAL_SUFFIX_LEGACY):
        for match in pattern.finditer(source):
            _add(match.group(1).strip())

    collapsed = _collapse_text_spaces(source)
    for suffix in _HOSPITAL_SUFFIX_ENDINGS:
        idx = 0
        while True:
            pos = collapsed.find(suffix, idx)
            if pos < 0:
                break
            start = max(0, pos - 30)
            chunk = collapsed[start : pos + len(suffix)]
            for match in re.finditer(
                rf"([가-힣]{{2,30}}{re.escape(suffix)})", chunk
            ):
                _add(match.group(1))
            idx = pos + len(suffix)

    match = _RE_HOSPITAL_LABEL.search(source)
    if match:
        _add(match.group(1).strip())
    return _dedupe_hospital_candidates(candidates)


def _dedupe_hospital_candidates(candidates: list[str]) -> list[str]:
    """짧은 부분 문자열(예: 서울삼성내과) 제거 후 긴 병원명 우선."""
    unique = list(dict.fromkeys(candidates))
    pruned: list[str] = []
    for name in sorted(unique, key=len, reverse=True):
        if any(name != other and name in other for other in unique):
            continue
        pruned.append(name)
    return pruned


def _extract_hospital_name(text: str, hospital_hint: str = "") -> tuple[str, list[str]]:
    hint = _sanitize_hospital_hint(hospital_hint)
    if hint:
        normalized = normalize_hospital_name(hint)
        if normalized:
            return normalized, [normalized]
        return "", []
    candidates = _extract_hospital_fuzzy_candidates(text)
    if not candidates:
        return "", []
    best = max(
        candidates,
        key=lambda n: (len(n), n.endswith("의원") or n.endswith("병원")),
    )
    return best, candidates


def _extract_phone_raw(text: str) -> str:
    match = _RE_PHONE.search(text)
    if match:
        return "".join(match.groups())
    plain = _RE_PHONE_PLAIN.search(text)
    if plain:
        return plain.group(1)
    return ""


def _extract_rrn_raw(text: str) -> str:
    match = _RE_RRN.search(text)
    if match:
        return match.group(1) + match.group(2)
    return ""


def _infer_document_type(filename: str, text: str) -> tuple[str, bool]:
    combined = f"{filename}\n{text}"
    if "영수증" in combined:
        return "진료비영수증", True
    if "처방" in combined:
        return "처방전", True
    if "세부" in combined or "내역" in combined:
        return "진료비세부내역서", True
    if "진단" in combined:
        return "진단서", True
    return _DEFAULT_DOC_TYPE, False


def _amounts_populated(amounts: dict[str, Any]) -> bool:
    if not isinstance(amounts, dict):
        return False
    if amounts.get("candidate_mode") or amounts.get("confirmation_required"):
        return bool(amounts.get("candidates") or amounts.get("total_amount"))
    return any(amounts.get(k) for k in ("total_amount", "self_pay_amount", "paid_amount"))


def _ocr_missing_fields(ocr_block: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not str(ocr_block.get("patient_name") or "").strip():
        missing.append("patient_name")
    if not str(ocr_block.get("hospital_name") or "").strip():
        missing.append("hospital_name")
    if not list(ocr_block.get("visit_dates") or []):
        missing.append("visit_dates")
    amounts = ocr_block.get("amounts") if isinstance(ocr_block.get("amounts"), dict) else {}
    if not _amounts_populated(amounts):
        missing.append("amounts")
    return missing


def _resolve_ocr_status(
    ocr_block: dict[str, Any],
    *,
    extraction_failed: bool,
    timed_out: bool = False,
) -> str:
    if extraction_failed:
        return "failed"
    if timed_out or _ocr_missing_fields(ocr_block):
        return "completed_partial"
    return "completed"


def extract_ocr_from_pdf(
    pdf_path: str | Path,
    *,
    filename: str = "",
    hospital_name: str = "",
    document_type_candidate: str = "",
    strong_ocr: bool = False,
    flow_store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PDF에서 OCR 항목 추출(빠른/강한 모드)."""
    path = Path(pdf_path)
    text, debug = _extract_document_text(path, strong_ocr=strong_ocr)
    file_date = _file_date_candidate_from_filename(filename)
    exclude_dates = {file_date} if file_date else set()

    registered = _registered_customer_names(flow_store)
    patient_name, raw_name_candidates, customer_name_fuzzy_matched = _extract_patient_name(
        text, registered_names=registered
    )
    hospital, hospital_fuzzy_candidates = _extract_hospital_name(text, hospital_name)
    visit_dates, raw_date_candidates, _accepted_dates, rejected_dates = (
        _extract_visit_dates(text, exclude=exclude_dates)
    )
    amounts, raw_amount_candidates, filtered_amounts, selected_amount_reason = (
        _extract_amounts_from_text(text)
    )
    normalized_text_preview = _mask_sensitive_in_preview(
        re.sub(r"\s+", " ", text.strip())[:_PREVIEW_MAX_LEN]
    )
    phone_raw = _extract_phone_raw(text)
    rrn_raw = _extract_rrn_raw(text)

    debug["raw_name_candidates"] = raw_name_candidates
    debug["raw_date_candidates"] = raw_date_candidates
    debug["filtered_visit_dates"] = visit_dates
    debug["raw_amount_candidates"] = raw_amount_candidates
    debug["filtered_amounts"] = filtered_amounts
    debug["rejected_date_candidates"] = rejected_dates
    debug["normalized_text_preview"] = normalized_text_preview
    debug["customer_name_fuzzy_matched"] = customer_name_fuzzy_matched or "—"
    debug["hospital_fuzzy_candidates"] = hospital_fuzzy_candidates
    debug["amount_candidates"] = amounts.get("candidates") or []
    debug["selected_amount_reason"] = selected_amount_reason or "—"
    debug["rejected_dates"] = rejected_dates

    doc_type = str(document_type_candidate or "").strip()
    inferred = False
    if not doc_type or doc_type == _DEFAULT_DOC_TYPE:
        doc_type, inferred = _infer_document_type(filename, text)

    combined_len = len(text.strip())
    timed_out = bool(debug.get("timed_out"))
    extraction_failed = combined_len == 0

    preview_raw = _mask_sensitive_in_preview(text[:_PREVIEW_MAX_LEN])
    debug["text_preview"] = preview_raw
    if extraction_failed:
        debug["ocr_error_message"] = debug.get("ocr_error_message") or _OCR_TEXT_EMPTY_MESSAGE

    debug_message = (
        f"mode={debug.get('ocr_mode')}, "
        f"pdf_text_len={debug.get('pdf_text_len')}, "
        f"ocr_text_len={debug.get('ocr_text_len')}, "
        f"source={debug.get('extraction_source')}, "
        f"dpi={debug.get('used_dpi')}, "
        f"pages={debug.get('max_pages')}, "
        f"timeout={'Y' if timed_out else 'N'}, "
        f"name={'Y' if patient_name else 'N'}, "
        f"hospital={'Y' if hospital else 'N'}, "
        f"dates={len(visit_dates)}, "
        f"amounts={'Y' if _amounts_populated(amounts) else 'N'}, "
        f"phone={'Y' if phone_raw else 'N'}, "
        f"rrn={'Y' if rrn_raw else 'N'}"
    )

    return {
        "patient_name": patient_name,
        "hospital_name": hospital,
        "visit_dates": visit_dates,
        "amounts": amounts,
        "phone_number_raw": phone_raw,
        "rrn_raw": rrn_raw,
        "document_type_candidate": doc_type,
        "document_type_inferred": inferred,
        "file_date_candidate": file_date,
        "_text": text,
        "_text_preview": preview_raw,
        "_debug": debug,
        "_debug_message": debug_message,
        "_extraction_failed": extraction_failed,
        "_timed_out": timed_out,
    }


def build_ocr_metadata_block(ocr_raw: dict[str, Any]) -> dict[str, Any]:
    """저장용 OCR 블록(원문 주민·전화 미저장)."""
    amounts_in = ocr_raw.get("amounts") if isinstance(ocr_raw.get("amounts"), dict) else {}
    phone_raw = str(ocr_raw.get("phone_number_raw") or "")
    rrn_raw = str(ocr_raw.get("rrn_raw") or "")
    debug = dict(ocr_raw.get("_debug") or {})

    amounts_block: dict[str, Any] = {
        "total_amount": amounts_in.get("total_amount"),
        "self_pay_amount": amounts_in.get("self_pay_amount"),
        "paid_amount": amounts_in.get("paid_amount"),
        "candidates": list(amounts_in.get("candidates") or []),
        "confirmation_required": bool(amounts_in.get("confirmation_required")),
    }
    if amounts_in.get("display_message"):
        amounts_block["display_message"] = str(amounts_in.get("display_message"))
    elif amounts_in.get("confirmation_required"):
        amounts_block["display_message"] = "금액 후보"
    amounts_block["candidate_mode"] = bool(amounts_in.get("candidate_mode"))

    block: dict[str, Any] = {
        "patient_name": str(ocr_raw.get("patient_name") or "").strip(),
        "hospital_name": _sanitize_hospital_hint(str(ocr_raw.get("hospital_name") or "")),
        "visit_dates": list(ocr_raw.get("visit_dates") or []),
        "file_date_candidate": str(ocr_raw.get("file_date_candidate") or "").strip(),
        "amounts": amounts_block,
        "phone_number_masked": mask_phone_number(phone_raw) if phone_raw else "",
        "rrn_masked": mask_rrn(rrn_raw) if rrn_raw else "",
        "text_preview": str(ocr_raw.get("_text_preview") or debug.get("text_preview") or "")[
            :_PREVIEW_MAX_LEN
        ],
        "debug_message": str(ocr_raw.get("_debug_message") or "").strip(),
        "debug": debug,
    }
    missing = _ocr_missing_fields(block)
    if missing:
        block["debug_message"] = (
            f"{block['debug_message']}; missing={','.join(missing)}".strip("; ")
        )
    if ocr_raw.get("_extraction_failed"):
        tess_err = str(debug.get("ocr_error_message") or "").strip()
        block["error_message"] = tess_err or _OCR_TEXT_EMPTY_MESSAGE
        debug["ocr_error_message"] = block["error_message"]
        block["debug"] = debug
    return block


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
) -> tuple[bool, bool, bool]:
    """(일치, 생년월일만/부분, 전체 13자리 일치)."""
    identity_hash = str(target.get("identity_hash") or "")
    identity_digits = str(target.get("identity_digits") or "")
    if len(ocr_rrn_digits) >= 13 and identity_hash:
        try:
            if _identity_hash(ocr_rrn_digits) == identity_hash:
                return True, False, True
        except PersistentStoreConfigError:
            return False, False, False
    if len(ocr_rrn_digits) >= 13 and len(identity_digits) >= 13:
        if ocr_rrn_digits == identity_digits:
            return True, False, True
    if len(ocr_rrn_digits) >= 6 and len(identity_digits) >= 6:
        if ocr_rrn_digits[:6] == identity_digits[:6]:
            return True, True, False
        if len(ocr_rrn_digits) >= 7 and ocr_rrn_digits[:7] == identity_digits[:7]:
            return True, True, False
    return False, False, False


def _resolve_ocr_match_status(
    matched_fields: list[str],
    *,
    rrn_full: bool,
) -> str:
    has_name = "이름" in matched_fields
    has_phone = "전화번호" in matched_fields
    has_rrn_full = "주민번호" in matched_fields or rrn_full
    has_rrn_partial = "주민번호(생년월일)" in matched_fields

    if has_rrn_full:
        return _MATCH_STATUS_AUTO
    if has_name and has_phone:
        return _MATCH_STATUS_AUTO
    if has_name and has_rrn_full:
        return _MATCH_STATUS_AUTO
    if has_phone and has_rrn_full:
        return _MATCH_STATUS_AUTO
    if has_name or has_phone or has_rrn_partial:
        return _MATCH_STATUS_REVIEW
    return _MATCH_STATUS_UNMATCHED


def _match_basis_display_for_fields(
    matched_fields: list[str],
    *,
    rrn_full_only: bool,
) -> str:
    if rrn_full_only and matched_fields == ["주민번호"]:
        return "주민번호 일치"
    return format_match_basis(matched_fields)


def match_customer_for_ocr(
    ocr_raw: dict[str, Any],
    *,
    flow_store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """주민 전체·이름+전화·이름+주민·전화+주민 → auto. 이름/전화 단독·생년월일만 → review."""
    targets = _enrich_match_targets_with_flow(list_customer_match_targets(), flow_store)
    ocr_name = normalize_person_name(str(ocr_raw.get("patient_name") or ""))
    ocr_text = str(ocr_raw.get("_text") or "")
    ocr_phone = normalize_phone_digits(str(ocr_raw.get("phone_number_raw") or ""))
    ocr_rrn = normalize_rrn_digits(str(ocr_raw.get("rrn_raw") or ""))

    best: dict[str, Any] = {
        "match_score": 0,
        "matched_fields": [],
        "match_status": _MATCH_STATUS_UNMATCHED,
        "matched_customer_key": None,
        "matched_customer_name": "",
        "rrn_full_match": False,
        "match_basis_display": "—",
    }

    hash_available = is_search_hash_secret_configured()

    for target in targets:
        matched_fields: list[str] = []
        score = 0
        rrn_full = False

        target_name = str(target.get("name") or "")
        if _name_matches_target(target_name, ocr_name, ocr_text):
            matched_fields.append("이름")
            score += 1

        if hash_available and ocr_phone and len(ocr_phone) >= 10:
            try:
                if _phone_hash(ocr_phone) == str(target.get("phone_hash") or ""):
                    matched_fields.append("전화번호")
                    score += 1
            except PersistentStoreConfigError:
                pass

        rrn_matched, rrn_partial, rrn_full = _score_rrn_field(ocr_rrn, target)
        if rrn_full:
            if "주민번호" not in matched_fields:
                matched_fields.append("주민번호")
            score += 1
        elif rrn_matched and rrn_partial:
            matched_fields.append("주민번호(생년월일)")
            score += 1

        status = _resolve_ocr_match_status(matched_fields, rrn_full=rrn_full)
        rrn_full_only = rrn_full and matched_fields == ["주민번호"]
        basis_display = _match_basis_display_for_fields(
            matched_fields, rrn_full_only=rrn_full_only
        )

        if score > int(best.get("match_score") or 0) or (
            score == int(best.get("match_score") or 0)
            and status == _MATCH_STATUS_AUTO
            and best.get("match_status") != _MATCH_STATUS_AUTO
        ):
            candidate_name = str(target.get("name") or "")
            best = {
                "match_score": score,
                "matched_fields": matched_fields,
                "match_status": status,
                "matched_customer_key": (
                    target.get("customer_key")
                    if status == _MATCH_STATUS_AUTO
                    else None
                ),
                "matched_customer_name": (
                    candidate_name if status == _MATCH_STATUS_AUTO else ""
                ),
                "review_candidate_name": "",
                "rrn_full_match": rrn_full,
                "match_basis_display": basis_display,
            }

    return best


def format_match_basis(matched_fields: list[str]) -> str:
    if not matched_fields:
        return "—"
    return "+".join(matched_fields) + " 일치"


def _merge_document_metadata(
    document_id: int,
    extra_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    existing = get_received_document_by_id(document_id)
    base: dict[str, Any] = {}
    if existing:
        if isinstance(existing.get("metadata"), dict):
            base = dict(existing["metadata"])
        else:
            base = _parse_received_document_metadata(existing.get("metadata_json"))
    merged = dict(base)
    if extra_metadata:
        merged.update(extra_metadata)
    return merged


def _mark_ocr_failed(
    document_id: int,
    *,
    error_message: str,
    metadata: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    meta = dict(metadata or {})
    ocr_block = meta.get("ocr") if isinstance(meta.get("ocr"), dict) else {}
    ocr_block["error_message"] = str(error_message or _OCR_TEXT_EMPTY_MESSAGE).strip()
    if debug:
        ocr_block["debug"] = debug
        ocr_block["debug_message"] = str(debug.get("ocr_error_message") or error_message)
    meta["ocr"] = ocr_block
    update_operator_received_document_ocr(
        document_id,
        metadata_json=meta,
        ocr_status="failed",
        customer_key=None,
        linked_customer_name="",
    )
    return get_received_document_by_id(document_id)


def apply_received_pdf_ocr_and_match(
    document_id: int,
    *,
    pdf_path: str | Path,
    filename: str = "",
    hospital_name: str = "",
    document_type_candidate: str = "",
    flow_store: dict[str, Any] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    strong_ocr: bool = False,
) -> dict[str, Any] | None:
    doc_id = int(document_id or 0)
    if doc_id <= 0:
        return None

    metadata = _merge_document_metadata(doc_id, extra_metadata)
    path = Path(pdf_path)
    if not path.is_file():
        debug = {"pdf_path": str(path), "file_exists": False, "ocr_error_message": "file missing"}
        return _mark_ocr_failed(
            doc_id,
            error_message=f"PDF 파일을 찾을 수 없습니다: {path}",
            metadata=metadata,
            debug=debug,
        )

    try:
        hospital_hint = _sanitize_hospital_hint(hospital_name)
        ocr_raw = extract_ocr_from_pdf(
            path,
            filename=filename or path.name,
            hospital_name=hospital_hint,
            document_type_candidate=document_type_candidate,
            strong_ocr=bool(strong_ocr),
            flow_store=flow_store,
        )
        extraction_failed = bool(ocr_raw.get("_extraction_failed"))
        timed_out = bool(ocr_raw.get("_timed_out"))
        ocr_block = build_ocr_metadata_block(ocr_raw)
        if not extraction_failed:
            ocr_block.pop("error_message", None)

        if extraction_failed:
            match = {
                "match_score": 0,
                "matched_fields": [],
                "match_status": _MATCH_STATUS_UNMATCHED,
                "matched_customer_key": None,
                "review_candidate_name": "",
            }
            ocr_status = "failed"
            customer_key = None
            linked_name = ""
        else:
            match = match_customer_for_ocr(ocr_raw, flow_store=flow_store)
            ocr_status = _resolve_ocr_status(
                ocr_block,
                extraction_failed=False,
                timed_out=timed_out,
            )
            customer_key = match.get("matched_customer_key")
            linked_name = ""
            if match.get("match_status") == _MATCH_STATUS_AUTO:
                linked_name = str(match.get("matched_customer_name") or "")
                if match.get("rrn_full_match") and not str(
                    ocr_block.get("patient_name") or ""
                ).strip():
                    ocr_block["patient_name"] = linked_name

        metadata["ocr"] = ocr_block
        metadata["match"] = {
            "match_score": match.get("match_score"),
            "matched_fields": match.get("matched_fields"),
            "match_status": match.get("match_status"),
            "matched_customer_key": match.get("matched_customer_key"),
            "review_candidate_name": match.get("review_candidate_name"),
            "match_basis_display": match.get("match_basis_display"),
            "rrn_full_match": bool(match.get("rrn_full_match")),
        }
        if ocr_raw.get("document_type_inferred"):
            metadata["document_type_inferred"] = True
        if hospital_hint and not _sanitize_hospital_hint(str(metadata.get("hospital_name") or "")):
            metadata["hospital_name"] = hospital_hint

        doc_type_update = str(ocr_raw.get("document_type_candidate") or "").strip()
        update_operator_received_document_ocr(
            doc_id,
            metadata_json=metadata,
            ocr_status=ocr_status,
            customer_key=str(customer_key) if customer_key else None,
            linked_customer_name=linked_name,
            document_type_candidate=doc_type_update or None,
        )
        return get_received_document_by_id(doc_id)
    except Exception as exc:
        logger.exception("apply_received_pdf_ocr_and_match failed doc_id=%s", doc_id)
        return _mark_ocr_failed(
            doc_id,
            error_message=str(exc) or "OCR 처리 실패",
            metadata=metadata,
        )


def run_ocr_for_received_document(
    document_id: int,
    *,
    flow_store: dict[str, Any] | None = None,
    strong_ocr: bool = False,
) -> dict[str, Any] | None:
    doc = get_received_document_by_id(int(document_id))
    if not doc:
        return None
    meta = _parse_received_document_metadata(doc.get("metadata_json"))
    if isinstance(doc.get("metadata"), dict):
        meta = {**meta, **doc["metadata"]}
    file_path = str(doc.get("file_path") or "").strip()
    if not file_path:
        return _mark_ocr_failed(
            int(document_id),
            error_message="저장된 PDF 경로가 없습니다.",
            metadata=meta,
        )
    return apply_received_pdf_ocr_and_match(
        int(document_id),
        pdf_path=file_path,
        filename=str(meta.get("original_filename") or doc.get("document_title") or ""),
        hospital_name=_sanitize_hospital_hint(str(meta.get("hospital_name") or "")),
        document_type_candidate=str(doc.get("document_type_candidate") or ""),
        flow_store=flow_store,
        extra_metadata=meta,
        strong_ocr=strong_ocr,
    )


def is_ocr_auto_matched(doc: dict[str, Any]) -> bool:
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    if not meta:
        meta = _parse_received_document_metadata(doc.get("metadata_json"))
    match = meta.get("match") if isinstance(meta.get("match"), dict) else {}
    return (
        str(match.get("match_status") or "") == _MATCH_STATUS_AUTO
        and str(doc.get("ocr_status") or "").strip().lower() == "completed"
    )
