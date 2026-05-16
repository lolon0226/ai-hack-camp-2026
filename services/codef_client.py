# -*- coding: utf-8 -*-
"""CODEF API 클라이언트 — 토큰 발급·응답 파싱·심평원 1·2차 요청."""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import unquote, unquote_plus

import requests

CODEF_TOKEN_URL = "https://oauth.codef.io/oauth/token"
DEFAULT_CODEF_BASE_URL = "https://development.codef.io"
DEFAULT_HIRA_MEDICAL_PATH = "/v1/kr/public/hw/hira-list/my-medical-information"
CODEF_SUCCESS_CODE = "CF-00000"
CODEF_PASSWORD_FIELD = "password"


class CodefClientError(Exception):
    """민감정보를 포함하지 않는 CODEF 클라이언트 오류."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes")


def is_codef_public_key_configured() -> bool:
    return bool((os.getenv("CODEF_PUBLIC_KEY") or "").strip())


def codef_password_encryption_debug() -> dict[str, Any]:
    """DEBUG용 — 비밀번호/키 원문은 포함하지 않음."""
    configured = is_codef_public_key_configured()
    return {
        "password_encrypted": configured,
        "public_key_configured": configured,
        "password_field_name": CODEF_PASSWORD_FIELD,
    }


def _load_rsa_public_key(public_key_raw: str) -> Any:
    from Crypto.PublicKey import RSA

    key_text = public_key_raw.strip()
    if "BEGIN" in key_text:
        return RSA.import_key(key_text.encode("utf-8"))
    try:
        der = base64.b64decode(key_text)
    except (ValueError, TypeError) as exc:
        raise CodefClientError(
            "CODEF_PUBLIC_KEY 형식이 올바르지 않습니다.",
            code="CODEF_PASSWORD_ENCRYPTION_ERROR",
        ) from exc
    return RSA.import_key(der)


def encrypt_codef_password(plain_password: str) -> str:
    """CODEF 요청용 비밀번호 RSA(PKCS#1 v1.5) 암호화 → base64."""
    if not plain_password:
        raise CodefClientError(
            "암호화할 비밀번호가 비어 있습니다.",
            code="CODEF_PASSWORD_ENCRYPTION_ERROR",
        )
    public_key_raw = (os.getenv("CODEF_PUBLIC_KEY") or "").strip()
    if not public_key_raw:
        raise CodefClientError(
            "CODEF_PUBLIC_KEY가 설정되지 않았습니다.",
            code="CODEF_PASSWORD_ENCRYPTION_ERROR",
        )
    try:
        from Crypto.Cipher import PKCS1_v1_5 as PKCS1

        key_pub = _load_rsa_public_key(public_key_raw)
        cipher = PKCS1.new(key_pub)
        encrypted = cipher.encrypt(plain_password.encode("utf-8"))
        if encrypted is None:
            raise CodefClientError(
                "CODEF 비밀번호 암호화에 실패했습니다.",
                code="CODEF_PASSWORD_ENCRYPTION_ERROR",
            )
        return base64.b64encode(encrypted).decode("utf-8")
    except CodefClientError:
        raise
    except Exception as exc:
        raise CodefClientError(
            "CODEF 비밀번호 암호화에 실패했습니다.",
            code="CODEF_PASSWORD_ENCRYPTION_ERROR",
        ) from exc


CODEF_CALL_DEBUG_KEYS = (
    "codef_base_url",
    "codef_use_demo",
    "codef_effective_client_id_masked",
    "codef_effective_client_id_source",
    "codef_endpoint",
    "codef_api_group",
    "codef_token_cached",
    "codef_token_client_id_masked",
)

_token_cache: dict[str, str] = {}


def codef_base_url() -> str:
    return (os.getenv("CODEF_BASE_URL") or DEFAULT_CODEF_BASE_URL).rstrip("/")


def mask_codef_client_id(client_id: str) -> str:
    """client_id 마스킹 — 앞 8자 + ... + 뒤 4자."""
    value = (client_id or "").strip()
    if not value:
        return "—"
    if len(value) <= 12:
        return value[:4] + "..." if len(value) > 4 else "****"
    return f"{value[:8]}...{value[-4:]}"


def codef_effective_client_id_source() -> str:
    return "CODEF_DEMO_CLIENT_ID" if _env_truthy("CODEF_USE_DEMO") else "CODEF_CLIENT_ID"


def _codef_credentials() -> tuple[str, str]:
    if _env_truthy("CODEF_USE_DEMO"):
        client_id = (os.getenv("CODEF_DEMO_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("CODEF_DEMO_CLIENT_SECRET") or "").strip()
    else:
        client_id = (os.getenv("CODEF_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("CODEF_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise CodefClientError("CODEF 클라이언트 자격 증명이 설정되지 않았습니다.")
    return client_id, client_secret


def _invalidate_codef_token_cache() -> None:
    _token_cache.clear()


def _codef_token_cache_valid(client_id: str, base_url: str) -> bool:
    return bool(
        _token_cache.get("token")
        and _token_cache.get("client_id") == client_id
        and _token_cache.get("base_url") == base_url
    )


def build_codef_call_debug(api_group: str, endpoint: str) -> dict[str, Any]:
    """CODEF API 호출 직전 DEBUG 메타(시크릿·토큰 원문 없음)."""
    client_id, _ = _codef_credentials()
    base_url = codef_base_url()
    cached = _codef_token_cache_valid(client_id, base_url)
    cached_client_id = str(_token_cache.get("client_id") or "") if cached else ""
    return {
        "codef_base_url": base_url,
        "codef_use_demo": _env_truthy("CODEF_USE_DEMO"),
        "codef_effective_client_id_masked": mask_codef_client_id(client_id),
        "codef_effective_client_id_source": codef_effective_client_id_source(),
        "codef_endpoint": (endpoint or "").strip() or "—",
        "codef_api_group": (api_group or "").strip() or "—",
        "codef_token_cached": cached,
        "codef_token_client_id_masked": (
            mask_codef_client_id(cached_client_id) if cached_client_id else None
        ),
    }


def pick_codef_call_debug(*sources: dict[str, Any] | None) -> dict[str, Any]:
    """여러 debug dict에서 CODEF credential 필드를 병합."""
    merged: dict[str, Any] = {}
    for src in sources:
        if not isinstance(src, dict):
            continue
        for key in CODEF_CALL_DEBUG_KEYS:
            if key in src and src[key] is not None:
                merged[key] = src[key]
    return merged


def parse_codef_response(response: requests.Response) -> Any:
    """CODEF 응답(JSON 또는 URL-encoded JSON) 파싱."""
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        pass

    text = response.text or ""
    for loader in (
        lambda t: json.loads(t),
        lambda t: json.loads(unquote(t)),
        lambda t: json.loads(unquote_plus(t)),
    ):
        try:
            return loader(text)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    raise CodefClientError("CODEF 응답을 JSON으로 파싱할 수 없습니다.")


def _fetch_codef_access_token(client_id: str, client_secret: str) -> str:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    try:
        response = requests.post(
            CODEF_TOKEN_URL,
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "client_credentials", "scope": "read"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise CodefClientError("CODEF 토큰 서버에 연결할 수 없습니다.") from exc

    if response.status_code >= 400:
        raise CodefClientError(
            f"CODEF 토큰 발급에 실패했습니다. (HTTP {response.status_code})"
        )

    try:
        parsed = parse_codef_response(response)
    except CodefClientError as exc:
        raise CodefClientError("CODEF 토큰 응답 형식이 올바르지 않습니다.") from exc

    if not isinstance(parsed, dict):
        raise CodefClientError("CODEF 토큰 응답 형식이 올바르지 않습니다.")

    token = parsed.get("access_token")
    if not token or not isinstance(token, str):
        raise CodefClientError("CODEF access_token을 받지 못했습니다.")
    return token


def get_codef_access_token() -> str:
    """CODEF OAuth access_token 발급(캐시, client_id·base_url 변경 시 무효화)."""
    client_id, client_secret = _codef_credentials()
    base_url = codef_base_url()
    if not _codef_token_cache_valid(client_id, base_url):
        _invalidate_codef_token_cache()
        token = _fetch_codef_access_token(client_id, client_secret)
        _token_cache["token"] = token
        _token_cache["client_id"] = client_id
        _token_cache["base_url"] = base_url
        return token
    return str(_token_cache["token"])


def _digits_only(value: str, *, max_len: int | None = None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if max_len is not None:
        return digits[:max_len]
    return digits


def build_hira_medical_payload(customer: dict[str, Any], flow_id: str) -> dict[str, str]:
    """심평원 내 진료정보열람 1차 요청 payload."""
    name = (customer.get("name") or "").strip()
    identity = _digits_only(str(customer.get("identity") or ""), max_len=13)
    phone = _digits_only(str(customer.get("phone") or ""))
    if not name or len(identity) != 13 or not phone:
        raise CodefClientError("고객 정보(이름·주민번호·휴대폰)가 올바르지 않습니다.")

    end = date.today()
    start = end - timedelta(days=5 * 365)

    return {
        "organization": "0020",
        "loginType": "5",
        "loginTypeLevel": "1",
        "userName": name,
        "identity": identity,
        "phoneNo": phone,
        "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
        "id": flow_id,
        "type": "1",
        "secureNoYN": "0",
    }


def _extract_result_data(parsed: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(parsed, dict):
        return {}, {}

    result = parsed.get("result")
    data = parsed.get("data")
    if isinstance(result, dict) and isinstance(data, dict):
        return result, data

    if isinstance(result, dict):
        return result, data if isinstance(data, dict) else {}

    # 일부 응답은 최상위에 code/message
    if "code" in parsed:
        return {
            "code": parsed.get("code"),
            "message": parsed.get("message", ""),
        }, parsed.get("data") if isinstance(parsed.get("data"), dict) else {}

    return {}, parsed if isinstance(parsed, dict) else {}


def _truthy_flag(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "y", "yes")
    return False


def _pick_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


_DISPERSED_TWO_WAY_REQUIRED = ("jobIndex", "threadIndex", "jti", "twoWayTimestamp")


def _assemble_two_way_from_data(data: dict[str, Any]) -> dict[str, Any] | None:
    """data에 낱개로 내려온 2차 인증 필드를 twoWayInfo 객체로 조립."""
    if not all(key in data and data.get(key) is not None for key in _DISPERSED_TWO_WAY_REQUIRED):
        return None
    assembled: dict[str, Any] = {
        "jobIndex": data.get("jobIndex"),
        "threadIndex": data.get("threadIndex"),
        "jti": data.get("jti"),
        "twoWayTimestamp": data.get("twoWayTimestamp"),
    }
    if "extraInfo" in data and data.get("extraInfo") is not None:
        assembled["extraInfo"] = data.get("extraInfo")
    return assembled


def extract_two_way_info(parsed: Any) -> dict[str, Any]:
    """
    CODEF 1차 응답에서 2차 인증 정보를 여러 경로에서 추출한다.
    twoWayInfo 원문은 로그/화면에 출력하지 않는다.
    """
    if not isinstance(parsed, dict):
        return {
            "continue2Way": False,
            "method": "",
            "twoWayInfo": None,
            "twoWayInfo_found": False,
            "root_keys": [],
            "data_keys": [],
        }

    root_keys = sorted(str(k) for k in parsed.keys())
    data = _pick_mapping(parsed.get("data"))
    data_keys = sorted(str(k) for k in data.keys())

    two_way: Any = None
    for container in (data, parsed):
        if not isinstance(container, dict):
            continue
        for key in ("twoWayInfo", "resTwoWayInfo"):
            candidate = container.get(key)
            if candidate is None:
                continue
            if isinstance(candidate, dict) and candidate:
                two_way = candidate
                break
            if isinstance(candidate, str) and candidate.strip():
                two_way = candidate
                break
            if candidate not in ("", [], {}):
                two_way = candidate
                break
        if two_way is not None:
            break

    if two_way is None and data:
        assembled = _assemble_two_way_from_data(data)
        if assembled is not None:
            two_way = assembled

    if two_way is None and isinstance(parsed, dict):
        assembled_root = _assemble_two_way_from_data(parsed)
        if assembled_root is not None:
            two_way = assembled_root

    two_way_found = two_way is not None and two_way not in ("", [], {})

    continue2_way = False
    for container in (data, parsed):
        if isinstance(container, dict) and _truthy_flag(container.get("continue2Way")):
            continue2_way = True
            break

    method = ""
    for container in (data, parsed):
        if isinstance(container, dict):
            raw_method = container.get("method")
            if raw_method is not None and str(raw_method).strip():
                method = str(raw_method).strip()
                break

    return {
        "continue2Way": continue2_way,
        "method": method,
        "twoWayInfo": two_way if two_way_found else None,
        "twoWayInfo_found": two_way_found,
        "root_keys": root_keys,
        "data_keys": data_keys,
    }


def is_hira_auth_waiting(
    parsed: Any,
    result: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> bool:
    """CF-03002 또는 2차 인증 대기 신호."""
    if parsed is None:
        parsed = {"result": result or {}, "data": data or {}}
    result_obj, data_obj = _extract_result_data(parsed)
    extracted = extract_two_way_info(parsed)
    code = str(result_obj.get("code") or "")
    if code == "CF-03002":
        return True
    if extracted["continue2Way"]:
        return True
    if extracted["method"] == "simpleAuth":
        return True
    if extracted["twoWayInfo_found"]:
        return True
    if _truthy_flag(data_obj.get("continue2Way")):
        return True
    if str(data_obj.get("method") or "") == "simpleAuth":
        return True
    return False


def user_message_for_codef_failure(result_code: str, result_message: str) -> str:
    """화면용 오류 메시지(민감정보 없음)."""
    code = result_code or ""
    if code == "CF-12200":
        return "진료내역 조회 요청에 실패했습니다. 입력 정보와 CODEF 설정을 확인한 뒤 다시 시도해 주세요."
    if result_message:
        return f"진료내역 조회에 실패했습니다. ({code})"
    return "진료내역 조회에 실패했습니다. 잠시 후 다시 시도해 주세요."


def user_message_for_second_failure(result_code: str, result_message: str) -> str:
    """2차 인증·수신 실패 시 화면용 메시지."""
    code = result_code or ""
    msg = (result_message or "").strip()
    if code == "CF-03002":
        return "카카오 인증이 아직 완료되지 않았습니다. 휴대폰에서 인증을 완료한 뒤 다시 시도해 주세요."
    if msg and len(msg) < 200:
        return f"진료내역 조회에 실패했습니다. {msg}"
    return user_message_for_codef_failure(code, msg)


def _hira_medical_path() -> str:
    path = (os.getenv("CODEF_HIRA_MEDICAL_PATH") or DEFAULT_HIRA_MEDICAL_PATH).strip()
    if not path.startswith("/"):
        path = "/" + path
    return path


def _hira_medical_url() -> str:
    return f"{codef_base_url()}{_hira_medical_path()}"


def _as_record_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def build_hira_medical_second_payload(
    first_payload: dict[str, Any],
    two_way_info: Any,
) -> dict[str, Any]:
    """심평원 내 진료정보열람 2차 요청 payload."""
    second: dict[str, Any] = dict(first_payload)
    second["simpleAuth"] = "1"
    second["is2Way"] = True
    second["twoWayInfo"] = two_way_info
    return second


def extract_hira_medical_lists(parsed: Any) -> dict[str, Any]:
    """CODEF 응답에서 진료내역 리스트 추출."""
    data: dict[str, Any] = {}
    if isinstance(parsed, dict):
        inner = parsed.get("data")
        if isinstance(inner, dict):
            data = inner
        else:
            data = parsed

    basic = _as_record_list(data.get("resBasicTreatList"))
    detail = _as_record_list(data.get("resDetailTreatList"))
    prescribe = _as_record_list(data.get("resPrescribeDrugList"))

    return {
        "basic": basic,
        "detail": detail,
        "prescribe": prescribe,
        "counts": {
            "basic": len(basic),
            "detail": len(detail),
            "prescribe": len(prescribe),
        },
    }


def is_hira_second_success(result_code: str, result: dict[str, Any], data: dict[str, Any]) -> bool:
    """2차 요청 성공 여부(CF-00000, 추가 인증 대기 아님)."""
    if is_hira_auth_waiting(result, data):
        return False
    return result_code == CODEF_SUCCESS_CODE


def _post_hira_medical_api(payload: dict[str, Any]) -> dict[str, Any]:
    """심평원 진료정보열람 POST 공통 처리."""
    url = _hira_medical_url()
    codef_call_debug = build_codef_call_debug("hira", _hira_medical_path())
    token = get_codef_access_token()
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise CodefClientError("CODEF API 서버에 연결할 수 없습니다.") from exc

    try:
        parsed = parse_codef_response(response)
    except CodefClientError as exc:
        raise CodefClientError(
            f"CODEF API 응답을 해석할 수 없습니다. (HTTP {response.status_code})"
        ) from exc

    result, data = _extract_result_data(parsed)
    result_code = str(result.get("code") or "")
    result_message = str(result.get("message") or "")
    return {
        "parsed": parsed,
        "result": result,
        "data": data,
        "status_code": response.status_code,
        "result_code": result_code,
        "result_message": result_message,
        "codef_call_debug": codef_call_debug,
    }


def post_hira_medical_second(
    first_payload: dict[str, Any],
    two_way_info: Any,
) -> dict[str, Any]:
    """
    심평원 내 진료정보열람 2차 POST.
    반환: ok, parsed, status_code, result_code, result_message
    """
    payload = build_hira_medical_second_payload(first_payload, two_way_info)
    try:
        api = _post_hira_medical_api(payload)
    except CodefClientError as exc:
        return {
            "ok": False,
            "parsed": None,
            "status_code": 0,
            "result_code": exc.code or "CLIENT_ERROR",
            "result_message": exc.message,
        }

    result = api.get("result") or {}
    data = api.get("data") or {}
    result_code = str(api.get("result_code") or "")
    result_message = str(api.get("result_message") or "")
    status_code = int(api.get("status_code") or 0)

    if status_code >= 400:
        return {
            "ok": False,
            "parsed": api.get("parsed"),
            "status_code": status_code,
            "result_code": result_code or f"HTTP_{status_code}",
            "result_message": result_message or "CODEF 통신 중 오류가 발생했습니다.",
        }

    ok = is_hira_second_success(result_code, result, data)
    return {
        "ok": ok,
        "parsed": api.get("parsed"),
        "status_code": status_code,
        "result_code": result_code,
        "result_message": result_message,
    }


def post_hira_medical_first(payload: dict[str, str]) -> dict[str, Any]:
    """
    심평원 내 진료정보열람 1차 POST.
    반환: result, data, parsed(원문 dict, 로그/화면 출력 금지), http_status
    """
    api = _post_hira_medical_api(payload)
    return {
        "result": api.get("result") or {},
        "data": api.get("data") or {},
        "parsed": api.get("parsed"),
        "http_status": api.get("status_code"),
    }
