# -*- coding: utf-8 -*-
"""신용정보원(내보험다보여) CODEF contract-info API."""
from __future__ import annotations

import os
import re
from typing import Any

import requests

from services.codef_client import (
    CODEF_PASSWORD_FIELD,
    CodefClientError,
    _as_record_list,
    _extract_result_data,
    _truthy_flag,
    codef_password_encryption_debug,
    encrypt_codef_password,
    extract_two_way_info,
    get_codef_access_token,
    is_codef_public_key_configured,
    parse_codef_response,
)

CODEF_SUCCESS_CODE = "CF-00000"

DEFAULT_CODEF_BASE_URL = "https://development.codef.io"
DEFAULT_CREDIT4U_CONTRACT_INFO_PATH = "/v1/kr/insurance/0001/credit4u/contract-info"
DEFAULT_CREDIT4U_REGISTER_PATH = "/v1/kr/insurance/0001/credit4u/register"

# 기존 신용정보원 계정 입력이 필요한 CODEF 응답(CF-09002 제외)
CREDIT4U_EXISTING_ACCOUNT_RESULT_CODES = frozenset(
    {
        "CF-12200",
    }
)

_TELECOM_CODE_MAP: dict[str, str] = {
    "SKT": "0",
    "KT": "1",
    "LGU+": "2",
    "알뜰폰 SKT": "0",
    "알뜰폰 KT": "1",
    "알뜰폰 LGU+": "2",
}

# 심평원(HIRA) 전용 필드 — credit4u payload에 포함 금지
_HIRA_ONLY_PAYLOAD_KEYS = frozenset(
    {
        "loginType",
        "loginTypeLevel",
        "startDate",
        "endDate",
        "type",
        "secureNoYN",
    }
)

# 심평원·보험사 등 타 API 기관코드 — credit4u에 자동 주입 금지
_FORBIDDEN_CREDIT4U_ORGANIZATION = frozenset(
    {
        "0020",  # 심평원 HIRA
        "0101",
        "0102",
        "0103",
        "0104",
        "0105",
    }
)


def credit4u_contract_info_path() -> str:
    return (
        os.getenv("CODEF_CREDIT4U_CONTRACT_INFO_PATH")
        or os.getenv("CODEF_INSURANCE_CONTRACT_PATH")
        or DEFAULT_CREDIT4U_CONTRACT_INFO_PATH
    ).strip()


def is_credit4u_contract_info_configured() -> bool:
    return bool(credit4u_contract_info_path())


def credit4u_register_path() -> str:
    return (
        os.getenv("CODEF_CREDIT4U_REGISTER_PATH")
        or DEFAULT_CREDIT4U_REGISTER_PATH
    ).strip()


def credit4u_register_url() -> str:
    path = credit4u_register_path()
    if not path:
        raise CodefClientError("CODEF_CREDIT4U_REGISTER_PATH가 설정되지 않았습니다.")
    base_url = (os.getenv("CODEF_BASE_URL") or DEFAULT_CODEF_BASE_URL).rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def credit4u_contract_info_url() -> str:
    path = credit4u_contract_info_path()
    if not path:
        raise CodefClientError("CODEF_CREDIT4U_CONTRACT_INFO_PATH가 설정되지 않았습니다.")
    base_url = (os.getenv("CODEF_BASE_URL") or DEFAULT_CODEF_BASE_URL).rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _digits_only(value: str, *, max_len: int | None = None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if max_len is not None:
        return digits[:max_len]
    return digits


def _has_nonempty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _telecom_code(customer: dict[str, Any]) -> str:
    label = str(customer.get("telecom") or "").strip()
    if label in _TELECOM_CODE_MAP:
        return _TELECOM_CODE_MAP[label]
    return "0"


def credit4u_organization() -> str:
    """신용정보원 credit4u 기관코드(기본 0001, HIRA·보험사 코드 금지)."""
    org = (os.getenv("CREDIT4U_ORGANIZATION") or "0001").strip() or "0001"
    if org in _FORBIDDEN_CREDIT4U_ORGANIZATION:
        raise CodefClientError(
            "CREDIT4U_ORGANIZATION에 타 기관(심평원·보험사) 코드를 사용할 수 없습니다.",
            code="CLIENT_ERROR",
        )
    legacy = (os.getenv("CODEF_ORGANIZATION") or "").strip()
    if legacy and org == legacy:
        raise CodefClientError(
            "CODEF_ORGANIZATION 공용값은 credit4u에 사용할 수 없습니다.",
            code="CLIENT_ERROR",
        )
    return org


def is_credit4u_existing_account_required(result_code: str) -> bool:
    return (result_code or "").strip() in CREDIT4U_EXISTING_ACCOUNT_RESULT_CODES


def sanitize_credit4u_two_way_info(two_way_info: dict[str, Any]) -> dict[str, Any]:
    """twoWayInfo에서 심평원·공용 organization 등 제거."""
    cleaned: dict[str, Any] = {}
    for key, value in two_way_info.items():
        if key in _HIRA_ONLY_PAYLOAD_KEYS or key == "organization":
            continue
        cleaned[key] = value
    return cleaned


def _finalize_credit4u_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """credit4u contract-info payload 정리(HIRA 필드 제거·organization=0001 등)."""
    for key in _HIRA_ONLY_PAYLOAD_KEYS:
        payload.pop(key, None)
    payload.pop("organization", None)
    payload["organization"] = credit4u_organization()

    two_way = payload.get("twoWayInfo")
    if isinstance(two_way, dict):
        payload["twoWayInfo"] = sanitize_credit4u_two_way_info(two_way)

    _finalize_credit4u_payload._last_organization_source = "credit4u_env_or_default"  # type: ignore[attr-defined]
    return payload


def get_credit4u_organization_source() -> str:
    return getattr(_finalize_credit4u_payload, "_last_organization_source", "credit4u_env_or_default")


def credit4u_payload_debug(payload: dict[str, Any]) -> dict[str, Any]:
    """DEBUG용 — 키 목록·organization 존재 여부만(값 원문 없음)."""
    keys = sorted(str(k) for k in payload.keys())
    org_present = "organization" in payload
    two_way = payload.get("twoWayInfo")
    if isinstance(two_way, dict) and "organization" in two_way:
        org_present = True
    return {
        "credit4u_payload_keys": ", ".join(keys) if keys else "—",
        "organization_in_payload": org_present,
        "organization_source": get_credit4u_organization_source(),
        "credit4u_endpoint": credit4u_contract_info_path(),
    }


def build_credit4u_contract_info_payload(
    customer: dict[str, Any],
    credentials: dict[str, Any],
) -> dict[str, str]:
    """contract-info 1차 요청 payload(민감값 로그 출력 금지)."""
    name = str(customer.get("name") or "").strip()
    identity = _digits_only(str(customer.get("identity") or ""), max_len=13)
    phone = _digits_only(str(customer.get("phone") or ""))
    user_id = str(credentials.get("id") or "").strip()
    plain_password = str(credentials.get("password") or "").strip()
    if not name or len(identity) != 13 or not phone:
        raise CodefClientError("고객 정보(이름·주민번호·휴대폰)가 올바르지 않습니다.")
    if not user_id or not plain_password:
        raise CodefClientError("신용정보원 조회용 계정정보가 준비되지 않았습니다.")

    encrypted_password = encrypt_codef_password(plain_password)

    payload: dict[str, Any] = {
        "userName": name,
        "identity": identity,
        "phoneNo": phone,
        "telecom": _telecom_code(customer),
        "id": user_id,
        CODEF_PASSWORD_FIELD: encrypted_password,
    }
    return _finalize_credit4u_payload(payload)


def is_credit4u_secure_no_required(
    result_code: str,
    data: dict[str, Any],
    extracted: dict[str, Any],
) -> bool:
    """CF-03002 + secureNo + reqSecureNo + continue2Way."""
    if result_code != "CF-03002":
        return False
    method = str(data.get("method") or extracted.get("method") or "").strip()
    if method != "secureNo":
        return False
    req_secure_no = data.get("reqSecureNo")
    if req_secure_no is None or str(req_secure_no).strip() == "":
        return False
    continue2_way = data.get("continue2Way")
    if continue2_way is None:
        continue2_way = extracted.get("continue2Way")
    return _truthy_flag(continue2_way)


def build_credit4u_contract_info_second_payload(
    customer: dict[str, Any],
    credentials: dict[str, Any],
    secure_no: str,
    two_way_info: Any,
) -> dict[str, Any]:
    """contract-info 2차 요청 payload."""
    payload: dict[str, Any] = dict(
        build_credit4u_contract_info_payload(customer, credentials)
    )
    secure_value = str(secure_no or "").strip()
    if not secure_value:
        raise CodefClientError("보안문자를 입력해 주세요.")
    if not isinstance(two_way_info, dict) or not two_way_info:
        raise CodefClientError("2차 인증 정보(twoWayInfo)가 없습니다.")
    payload["secureNo"] = secure_value
    payload["is2Way"] = True
    payload["twoWayInfo"] = sanitize_credit4u_two_way_info(two_way_info)
    return _finalize_credit4u_payload(payload)


def apply_encrypted_password_to_payload(
    payload: dict[str, Any],
    credentials: dict[str, Any],
) -> None:
    """회원가입 등 CODEF 요청에 평문 password 대신 RSA 암호문을 넣을 때 사용."""
    plain_password = str(credentials.get("password") or "").strip()
    payload[CODEF_PASSWORD_FIELD] = encrypt_codef_password(plain_password)


def extract_credit4u_insurance_records(data: Any) -> list[dict[str, Any]]:
    """CODEF contract-info 응답 data에서 보험가입이력 리스트 방어적 추출."""
    if not isinstance(data, dict):
        return []
    for key in (
        "resInsuranceList",
        "resContractList",
        "resInsuList",
        "insuranceList",
        "contractList",
        "resInsuranceContractList",
        "list",
    ):
        records = _as_record_list(data.get(key))
        if records:
            return [row for row in records if isinstance(row, dict)]
    nested = data.get("resInsuranceContract")
    if isinstance(nested, dict):
        return [nested]
    if isinstance(nested, list):
        return [row for row in nested if isinstance(row, dict)]
    return []


def is_credit4u_contract_info_success(result_code: str, data: dict[str, Any]) -> bool:
    """CF-00000 또는 보험가입이력 데이터 수신."""
    if result_code == CODEF_SUCCESS_CODE:
        return True
    return bool(extract_credit4u_insurance_records(data))


def credit4u_register_payload_debug(payload: dict[str, Any]) -> dict[str, Any]:
    """DEBUG용 — register payload 키 목록만."""
    keys = sorted(str(k) for k in payload.keys())
    return {
        "register_endpoint": credit4u_register_path(),
        "register_payload_keys": ", ".join(keys) if keys else "—",
    }


def extract_register_extra_info(data: dict[str, Any]) -> dict[str, Any]:
    """register 응답 data.extraInfo 추출."""
    extra = data.get("extraInfo")
    if isinstance(extra, dict):
        return dict(extra)
    return {}


def register_req_secure_no(data: dict[str, Any], extra_info: dict[str, Any]) -> Any:
    for container in (extra_info, data):
        if not isinstance(container, dict):
            continue
        value = container.get("reqSecureNo")
        if _has_nonempty_value(value):
            return value
    return None


def resolve_register_stage(
    extra_info: dict[str, Any],
    *,
    data: dict[str, Any] | None = None,
    extracted: dict[str, Any] | None = None,
) -> str:
    """extraInfo·method 기준 register 후속 단계."""
    data = data or {}
    extracted = extracted or {}
    method = str(data.get("method") or extracted.get("method") or "").strip().lower()

    if _has_nonempty_value(extra_info.get("reqSecureNo")):
        return "register_secure_no_required"
    if _has_nonempty_value(extra_info.get("reqSMSAuthNo")):
        return "register_sms_required"
    if _has_nonempty_value(extra_info.get("commSimpleAuth")):
        return "register_sms_required"
    if "sms" in method:
        return "register_sms_required"
    if any(
        _has_nonempty_value(extra_info.get(key))
        for key in ("reqUserId", "reqUserPass", "reqEmail")
    ):
        return "register_signup_info_required"
    if _has_nonempty_value(extra_info.get("reqEmailAuthNo")):
        return "register_email_auth_required"
    return "register_continue_pending"


def is_credit4u_register_completed(result_code: str, data: dict[str, Any]) -> bool:
    """회원가입 완료(CF-00000 + resRegistrationStatus=1)."""
    if result_code != CODEF_SUCCESS_CODE:
        return False
    status = str(data.get("resRegistrationStatus") or "").strip()
    return status == "1"


def is_credit4u_register_followup_continue(
    result_code: str,
    data: dict[str, Any],
    extracted: dict[str, Any],
) -> bool:
    """register 2차 이후 추가 인증 단계 필요."""
    if is_credit4u_register_completed(result_code, data):
        return True
    if result_code == "CF-03002":
        continue2_way = data.get("continue2Way")
        if continue2_way is None:
            continue2_way = extracted.get("continue2Way")
        if _truthy_flag(continue2_way):
            return True
    extra_info = extract_register_extra_info(data)
    if resolve_register_stage(extra_info, data=data, extracted=extracted) != "register_continue_pending":
        return True
    return False


_REGISTER_SIGNUP_RETRY_CODES = frozenset(
    {"CF-12824", "CF-12825", "CF-12826", "CF-12827", "CF-13341", "CF-13343"}
)
_REGISTER_EMAIL_RETRY_CODES = frozenset({"CF-13342"})


def is_register_signup_retry_code(result_code: str) -> bool:
    return (result_code or "").strip() in _REGISTER_SIGNUP_RETRY_CODES


def is_register_email_retry_code(result_code: str) -> bool:
    return (result_code or "").strip() in _REGISTER_EMAIL_RETRY_CODES


def build_credit4u_register_second_payload(
    first_payload: dict[str, Any],
    two_way_info: Any,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """register 2차 이상 payload(1차 payload + is2Way + twoWayInfo + values)."""
    if not isinstance(first_payload, dict) or not first_payload:
        raise CodefClientError("register 1차 payload가 없습니다.")
    if not isinstance(two_way_info, dict) or not two_way_info:
        raise CodefClientError("2차 인증 정보(twoWayInfo)가 없습니다.")

    payload: dict[str, Any] = dict(first_payload)
    vals = values if isinstance(values, dict) else {}

    secure_no = str(vals.get("secureNo") or "").strip()
    if secure_no:
        payload["secureNo"] = secure_no
    payload["secureNoRefresh"] = str(vals.get("secureNoRefresh") or "0").strip() or "0"
    payload["is2Way"] = True
    payload["twoWayInfo"] = sanitize_credit4u_two_way_info(two_way_info)

    for key in (
        "smsAuthNo",
        "simpleAuth",
        "id",
        "email",
        "emailAuthNo",
    ):
        value = vals.get(key)
        if _has_nonempty_value(value):
            payload[key] = str(value).strip()

    plain_password = str(vals.get("password") or "").strip()
    if plain_password:
        apply_encrypted_password_to_payload(payload, {"password": plain_password})

    return payload


def is_credit4u_register_continue_required(
    result_code: str,
    data: dict[str, Any],
    extracted: dict[str, Any],
) -> bool:
    """CF-03002 + continue2Way."""
    if result_code != "CF-03002":
        return False
    continue2_way = data.get("continue2Way")
    if continue2_way is None:
        continue2_way = extracted.get("continue2Way")
    return _truthy_flag(continue2_way)


def build_credit4u_register_first_payload(
    customer: dict[str, Any],
    _credentials: dict[str, Any],
) -> dict[str, Any]:
    """회원가입 1차 payload(type=1: id/password/email 빈 값)."""
    name = str(customer.get("name") or "").strip()
    identity = _digits_only(str(customer.get("identity") or ""), max_len=13)
    phone = _digits_only(str(customer.get("phone") or ""))
    if not name or len(identity) != 13 or not phone:
        raise CodefClientError("고객 정보(이름·주민번호·휴대폰)가 올바르지 않습니다.")

    register_type = (os.getenv("CREDIT4U_APPLICATION_TYPE") or "1").strip() or "1"
    return {
        "organization": credit4u_organization(),
        "userName": name,
        "identity": identity,
        "telecom": _telecom_code(customer),
        "phoneNo": phone,
        "timeout": (os.getenv("CREDIT4U_TIMEOUT") or "160").strip() or "160",
        "emailTimeout": (os.getenv("CREDIT4U_EMAIL_TIMEOUT") or "180").strip() or "180",
        "authMethod": (os.getenv("CREDIT4U_AUTH_METHOD") or "0").strip() or "0",
        "type": register_type,
        "identityEncYn": (os.getenv("CREDIT4U_IDENTITY_ENC_YN") or "N").strip() or "N",
        "id": "",
        "password": "",
        "email": "",
    }


def register_response_debug(
    payload: dict[str, Any],
    extra_info: dict[str, Any],
    extracted: dict[str, Any],
) -> dict[str, Any]:
    """DEBUG용 — register 응답 메타(민감값 제외)."""
    extra_keys = sorted(str(k) for k in extra_info.keys()) if extra_info else []
    return {
        **credit4u_register_payload_debug(payload),
        "register_extra_info_keys": ", ".join(extra_keys) if extra_keys else "—",
        "register_two_way_info_saved": bool(extracted.get("twoWayInfo_found")),
    }


def user_message_for_credit4u_failure(result_code: str, result_message: str) -> str:
    code = (result_code or "").strip()
    if code == "CF-09002":
        return (
            "보험가입이력 조회 요청 형식을 확인해야 합니다. "
            "신용정보원 API의 기관코드 또는 요청 경로 설정을 점검해 주세요."
        )
    if code == "CF-12824":
        return "신용정보원 아이디 형식이 올바르지 않습니다. 아이디는 6~12자의 영문·숫자만 사용할 수 있습니다."
    if code == "CF-12825":
        return "신용정보원 아이디를 다시 확인해 주세요."
    if code == "CF-12826":
        return "신용정보원 비밀번호 형식이 올바르지 않습니다."
    if code == "CF-12827":
        return "신용정보원 비밀번호를 다시 확인해 주세요."
    if code == "CF-13341":
        return "회원가입 이메일 정보를 확인해 주세요."
    if code == "CF-13342":
        return "회원가입에 사용할 이메일 주소를 확인해 주세요."
    if code == "CF-13343":
        return "이메일 인증 정보를 확인해 주세요."
    if code == "CF-12200":
        return "이미 신용정보원에 가입된 고객입니다. 기존 아이디와 비밀번호를 입력해 주세요."
    if code.startswith("CF-"):
        return result_message or "보험가입이력 조회에 실패했습니다."
    return result_message or "보험가입이력 조회에 실패했습니다."


def post_credit4u_contract_info_first(
    flow_id: str,
    customer: dict[str, Any],
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """
    신용정보원 contract-info 1차 POST.
    반환: ok, parsed, result, data, status_code, result_code, result_message, extracted
    """
    del flow_id  # noqa: ARG001
    payload: dict[str, Any] = {}
    try:
        payload = build_credit4u_contract_info_payload(customer, credentials)
        url = credit4u_contract_info_url()
        token = get_codef_access_token()
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
    except CodefClientError as exc:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "status_code": 0,
            "result_code": exc.code or "CLIENT_ERROR",
            "result_message": exc.message,
            "extracted": {},
            "payload_debug": credit4u_payload_debug(payload),
        }
    except requests.RequestException:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "status_code": 0,
            "result_code": "CLIENT_ERROR",
            "result_message": "CODEF API 서버에 연결할 수 없습니다.",
            "extracted": {},
            "payload_debug": credit4u_payload_debug(payload),
        }

    try:
        parsed = parse_codef_response(response)
    except CodefClientError:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "status_code": response.status_code,
            "result_code": "PARSE_ERROR",
            "result_message": "CODEF 응답을 해석하지 못했습니다.",
            "extracted": {},
            "payload_debug": credit4u_payload_debug(payload),
        }

    result, data = _extract_result_data(parsed)
    result_code = str(result.get("code") or "")
    result_message = str(result.get("message") or "")
    extracted = extract_two_way_info(parsed)
    status_code = int(response.status_code or 0)
    payload_debug = credit4u_payload_debug(payload)

    if status_code >= 400:
        return {
            "ok": False,
            "parsed": parsed,
            "result": result,
            "data": data,
            "status_code": status_code,
            "result_code": result_code or f"HTTP_{status_code}",
            "result_message": result_message or "CODEF 통신 중 오류가 발생했습니다.",
            "extracted": extracted,
            "payload_debug": payload_debug,
        }

    secure_no = is_credit4u_secure_no_required(result_code, data, extracted)
    return {
        "ok": secure_no,
        "parsed": parsed,
        "result": result,
        "data": data,
        "status_code": status_code,
        "result_code": result_code,
        "result_message": result_message,
        "extracted": extracted,
        "secure_no_required": secure_no,
        "payload_debug": payload_debug,
    }


def post_credit4u_contract_info_second(
    customer: dict[str, Any],
    credentials: dict[str, Any],
    secure_no: str,
    two_way_info: Any,
) -> dict[str, Any]:
    """
    신용정보원 contract-info 2차 POST.
    반환: ok, parsed, result, data, status_code, result_code, result_message
    """
    payload: dict[str, Any] = {}
    try:
        payload = build_credit4u_contract_info_second_payload(
            customer,
            credentials,
            secure_no,
            two_way_info,
        )
        url = credit4u_contract_info_url()
        token = get_codef_access_token()
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
    except CodefClientError as exc:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "status_code": 0,
            "result_code": exc.code or "CLIENT_ERROR",
            "result_message": exc.message,
            "payload_debug": credit4u_payload_debug(payload),
        }
    except requests.RequestException:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "status_code": 0,
            "result_code": "CLIENT_ERROR",
            "result_message": "CODEF API 서버에 연결할 수 없습니다.",
            "payload_debug": credit4u_payload_debug(payload),
        }

    try:
        parsed = parse_codef_response(response)
    except CodefClientError:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "status_code": response.status_code,
            "result_code": "PARSE_ERROR",
            "result_message": "CODEF 응답을 해석하지 못했습니다.",
            "payload_debug": credit4u_payload_debug(payload),
        }

    result, data = _extract_result_data(parsed)
    result_code = str(result.get("code") or "")
    result_message = str(result.get("message") or "")
    status_code = int(response.status_code or 0)
    payload_debug = credit4u_payload_debug(payload)

    if status_code >= 400:
        return {
            "ok": False,
            "parsed": parsed,
            "result": result,
            "data": data,
            "status_code": status_code,
            "result_code": result_code or f"HTTP_{status_code}",
            "result_message": result_message or "CODEF 통신 중 오류가 발생했습니다.",
            "payload_debug": payload_debug,
        }

    ok = is_credit4u_contract_info_success(result_code, data)
    return {
        "ok": ok,
        "parsed": parsed,
        "result": result,
        "data": data,
        "status_code": status_code,
        "result_code": result_code,
        "result_message": result_message,
        "payload_debug": payload_debug,
    }


def post_credit4u_register_first(
    customer: dict[str, Any],
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """
    신용정보원 register 1차 POST.
    반환: ok, parsed, result, data, extra_info, extracted, status_code,
          result_code, result_message, register_debug
    """
    payload: dict[str, Any] = {}
    try:
        payload = build_credit4u_register_first_payload(customer, credentials)
        url = credit4u_register_url()
        token = get_codef_access_token()
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
    except CodefClientError as exc:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "extra_info": {},
            "extracted": {},
            "status_code": 0,
            "result_code": exc.code or "CLIENT_ERROR",
            "result_message": exc.message,
            "register_debug": credit4u_register_payload_debug(payload),
            "first_payload": payload,
        }
    except requests.RequestException:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "extra_info": {},
            "extracted": {},
            "status_code": 0,
            "result_code": "CLIENT_ERROR",
            "result_message": "CODEF API 서버에 연결할 수 없습니다.",
            "register_debug": credit4u_register_payload_debug(payload),
            "first_payload": payload,
        }

    try:
        parsed = parse_codef_response(response)
    except CodefClientError:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "extra_info": {},
            "extracted": {},
            "status_code": response.status_code,
            "result_code": "PARSE_ERROR",
            "result_message": "CODEF 응답을 해석하지 못했습니다.",
            "register_debug": register_response_debug(payload, {}, {}),
            "first_payload": payload,
        }

    result, data = _extract_result_data(parsed)
    result_code = str(result.get("code") or "")
    result_message = str(result.get("message") or "")
    extracted = extract_two_way_info(parsed)
    extra_info = extract_register_extra_info(data)
    status_code = int(response.status_code or 0)
    reg_debug = register_response_debug(payload, extra_info, extracted)

    if status_code >= 400:
        return {
            "ok": False,
            "parsed": parsed,
            "result": result,
            "data": data,
            "extra_info": extra_info,
            "extracted": extracted,
            "status_code": status_code,
            "result_code": result_code or f"HTTP_{status_code}",
            "result_message": result_message or "CODEF 통신 중 오류가 발생했습니다.",
            "register_debug": reg_debug,
            "first_payload": payload,
        }

    ok = is_credit4u_register_continue_required(result_code, data, extracted)
    return {
        "ok": ok,
        "parsed": parsed,
        "result": result,
        "data": data,
        "extra_info": extra_info,
        "extracted": extracted,
        "status_code": status_code,
        "result_code": result_code,
        "result_message": result_message,
        "register_debug": reg_debug,
        "first_payload": payload,
    }


def post_credit4u_register_second(
    first_payload: dict[str, Any],
    two_way_info: Any,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    신용정보원 register 2차 이상 POST.
    반환: ok, parsed, result, data, extra_info, extracted, status_code,
          result_code, result_message, register_debug, completed
    """
    payload: dict[str, Any] = {}
    try:
        payload = build_credit4u_register_second_payload(
            first_payload,
            two_way_info,
            values,
        )
        url = credit4u_register_url()
        token = get_codef_access_token()
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
    except CodefClientError as exc:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "extra_info": {},
            "extracted": {},
            "status_code": 0,
            "result_code": exc.code or "CLIENT_ERROR",
            "result_message": exc.message,
            "register_debug": credit4u_register_payload_debug(payload),
            "completed": False,
        }
    except requests.RequestException:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "extra_info": {},
            "extracted": {},
            "status_code": 0,
            "result_code": "CLIENT_ERROR",
            "result_message": "CODEF API 서버에 연결할 수 없습니다.",
            "register_debug": credit4u_register_payload_debug(payload),
            "completed": False,
        }

    try:
        parsed = parse_codef_response(response)
    except CodefClientError:
        return {
            "ok": False,
            "parsed": None,
            "result": {},
            "data": {},
            "extra_info": {},
            "extracted": {},
            "status_code": response.status_code,
            "result_code": "PARSE_ERROR",
            "result_message": "CODEF 응답을 해석하지 못했습니다.",
            "register_debug": register_response_debug(payload, {}, {}),
            "completed": False,
        }

    result, data = _extract_result_data(parsed)
    result_code = str(result.get("code") or "")
    result_message = str(result.get("message") or "")
    extracted = extract_two_way_info(parsed)
    extra_info = extract_register_extra_info(data)
    status_code = int(response.status_code or 0)
    reg_debug = register_response_debug(payload, extra_info, extracted)
    completed = is_credit4u_register_completed(result_code, data)

    if status_code >= 400:
        return {
            "ok": False,
            "parsed": parsed,
            "result": result,
            "data": data,
            "extra_info": extra_info,
            "extracted": extracted,
            "status_code": status_code,
            "result_code": result_code or f"HTTP_{status_code}",
            "result_message": result_message or "CODEF 통신 중 오류가 발생했습니다.",
            "register_debug": reg_debug,
            "completed": False,
        }

    ok = completed or is_credit4u_register_followup_continue(
        result_code, data, extracted
    )
    return {
        "ok": ok,
        "parsed": parsed,
        "result": result,
        "data": data,
        "extra_info": extra_info,
        "extracted": extracted,
        "status_code": status_code,
        "result_code": result_code,
        "result_message": result_message,
        "register_debug": reg_debug,
        "completed": completed,
    }
