# -*- coding: utf-8 -*-
"""신용정보원(내보험다보여) CODEF contract-info API."""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Literal

from services.credit4u_identity import get_credit4u_secret

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

CREDIT4U_ALREADY_REGISTERED_CODE = "CF-12069"
CREDIT4U_REGISTER_REQUIRED_CODE = "CF-12832"
CREDIT4U_REGISTER_TIMEOUT_RETRY_CODE = "CF-01004"
DEFAULT_CREDIT4U_HTTP_TIMEOUT = 300.0
DEFAULT_CREDIT4U_ADDITIONAL_AUTH_TIMEOUT = 270.0
DEFAULT_CREDIT4U_EMAIL_TIMEOUT = 180.0
DEFAULT_CREDIT4U_REGISTER_PAYLOAD_TIMEOUT = "160"

RegisterSecondPurpose = Literal["secure_no", "sms", "signup_info", "email_auth"]

REGISTER_BASE_PAYLOAD_KEYS = frozenset(
    {
        "organization",
        "userName",
        "identity",
        "telecom",
        "phoneNo",
        "timeout",
        "emailTimeout",
        "authMethod",
        "type",
        "identityEncYn",
        "checkParamUUID",
    }
)

REGISTER_STEP_FIELD_KEYS = frozenset(
    {
        "secureNo",
        "secureNoRefresh",
        "smsAuthNo",
        "simpleAuth",
        "id",
        "password",
        "email",
        "emailAuthNo",
        "is2Way",
        "twoWayInfo",
    }
)

ALLOWED_CREDIT4U_EMAIL_DOMAINS = frozenset(
    {
        "naver.com",
        "hanmail.net",
        "daum.net",
        "nate.com",
        "korea.kr",
        "kcredit.or.kr",
        "korea.com",
        "yahoo.com",
        "goe.go.kr",
        "chol.com",
        "sen.go.kr",
        "gyo6.net",
        "jnu.ac.kr",
        "kakao.com",
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


def is_credit4u_already_registered(result_code: str) -> bool:
    """이미 가입된 주민등록번호(CF-12069) — 회원가입 재시도 금지."""
    return (result_code or "").strip() == CREDIT4U_ALREADY_REGISTERED_CODE


def is_credit4u_register_required(result_code: str) -> bool:
    return (result_code or "").strip() == CREDIT4U_REGISTER_REQUIRED_CODE


def _parse_timeout_seconds(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def credit4u_http_timeout_seconds() -> float:
    """contract-info·register 1차 등 장시간 CODEF 요청."""
    return _parse_timeout_seconds("CREDIT4U_TIMEOUT", DEFAULT_CREDIT4U_HTTP_TIMEOUT)


def credit4u_additional_auth_timeout_seconds() -> float:
    """보안문자·SMS 등 추가인증(2차) 요청."""
    return _parse_timeout_seconds(
        "CREDIT4U_ADDITIONAL_AUTH_TIMEOUT",
        DEFAULT_CREDIT4U_ADDITIONAL_AUTH_TIMEOUT,
    )


def credit4u_email_timeout_payload_value() -> str:
    """register payload emailTimeout 필드."""
    return str(int(_parse_timeout_seconds("CREDIT4U_EMAIL_TIMEOUT", DEFAULT_CREDIT4U_EMAIL_TIMEOUT)))


def credit4u_register_payload_timeout_value() -> str:
    """register payload timeout(문서: 160 또는 170 문자열, HTTP timeout과 별도)."""
    raw = (os.getenv("CREDIT4U_REGISTER_PAYLOAD_TIMEOUT") or "160").strip()
    if raw in ("160", "170"):
        return raw
    return DEFAULT_CREDIT4U_REGISTER_PAYLOAD_TIMEOUT


def generate_check_param_uuid(flow_id: str, customer: dict[str, Any]) -> str:
    """
    type=1 register용 checkParamUUID(정확히 20자, 영문·숫자).
    flow_id·고객·secret 기반 결정론적 생성(원문 로그 금지).
    """
    fid = str(flow_id or "").strip()
    name = str(customer.get("name") or "").strip()
    identity = "".join(c for c in str(customer.get("identity") or "") if c.isdigit())
    phone = "".join(c for c in str(customer.get("phone") or "") if c.isdigit())
    secret = get_credit4u_secret() or ""
    raw = f"{fid}|{name}|{identity}|{phone}|{secret}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    token = digest[:20]
    if len(token) < 20:
        token = (digest * 2)[:20]
    return token


def ensure_credit4u_check_param_uuid(entry: dict[str, Any], flow_id: str) -> str:
    """FLOW에 저장된 checkParamUUID 반환(있으면 재생성하지 않음)."""
    existing = str(entry.get("credit4u_check_param_uuid") or "").strip()
    if len(existing) == 20 and existing.isalnum():
        return existing
    customer = entry.get("customer") if isinstance(entry.get("customer"), dict) else {}
    value = generate_check_param_uuid(flow_id, customer)
    entry["credit4u_check_param_uuid"] = value
    return value


def is_credit4u_email_domain_enforcement_enabled() -> bool:
    """운영 정책에 따라 이메일 도메인 사전 검증 on/off."""
    flag = (os.getenv("CREDIT4U_EMAIL_DOMAIN_ENFORCE") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def extract_credit4u_email_domain(email: str) -> str:
    value = (email or "").strip().lower()
    if "@" not in value:
        return ""
    return value.rsplit("@", 1)[-1].strip()


def is_credit4u_email_domain_allowed(email: str) -> bool:
    if not is_credit4u_email_domain_enforcement_enabled():
        return True
    domain = extract_credit4u_email_domain(email)
    if not domain:
        return False
    return domain in ALLOWED_CREDIT4U_EMAIL_DOMAINS


def allowed_credit4u_email_domains_display() -> str:
    return ", ".join(sorted(ALLOWED_CREDIT4U_EMAIL_DOMAINS))


def validate_credit4u_email_for_register(email: str) -> str | None:
    """
    회원가입 이메일 사전 검증.
    통과 시 None, 실패 시 사용자 안내 문구.
    """
    value = (email or "").strip()
    if not value or "@" not in value:
        return "이메일 주소를 입력해 주세요."
    if not is_credit4u_email_domain_allowed(value):
        return (
            "신용정보원에서 허용하는 이메일 도메인을 입력해 주세요. "
            "naver.com 또는 kakao.com 사용을 권장합니다."
        )
    return None


def is_credit4u_register_timeout_retryable(result_code: str) -> bool:
    return (result_code or "").strip() == CREDIT4U_REGISTER_TIMEOUT_RETRY_CODE


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


def register_payload_field_flags(payload: dict[str, Any]) -> dict[str, str]:
    """payload 필드 존재 여부만(원문·암호문 없음)."""
    check = str(payload.get("checkParamUUID") or "").strip()
    pw = str(payload.get(CODEF_PASSWORD_FIELD) or "").strip()
    return {
        "checkParamUUID_present": "예" if len(check) == 20 and check.isalnum() else "아니오",
        "checkParamUUID_length": len(check) if check else 0,
        "payload_has_checkParamUUID": "예" if check else "아니오",
        "payload_has_id": "예" if _has_nonempty_value(payload.get("id")) else "아니오",
        "payload_has_password": "예" if pw else "아니오",
        "payload_has_email": "예" if _has_nonempty_value(payload.get("email")) else "아니오",
        "payload_has_secureNo": "예" if _has_nonempty_value(payload.get("secureNo")) else "아니오",
        "payload_has_secureNoRefresh": (
            "예" if "secureNoRefresh" in payload else "아니오"
        ),
        "payload_has_smsAuthNo": (
            "예" if _has_nonempty_value(payload.get("smsAuthNo")) else "아니오"
        ),
        "payload_has_emailAuthNo": (
            "예" if _has_nonempty_value(payload.get("emailAuthNo")) else "아니오"
        ),
        "password_encrypted": (
            "예"
            if pw and is_codef_public_key_configured()
            else "아니오"
        ),
        "twoWayInfo_saved": (
            "예"
            if _truthy_flag(payload.get("is2Way"))
            and isinstance(payload.get("twoWayInfo"), dict)
            and bool(payload.get("twoWayInfo"))
            else "아니오"
        ),
    }


def credit4u_register_payload_debug(
    payload: dict[str, Any],
    *,
    purpose: str = "",
) -> dict[str, Any]:
    """DEBUG용 — register payload 키·필드 플래그(민감값 제외)."""
    keys = sorted(str(k) for k in payload.keys())
    flags = register_payload_field_flags(payload)
    return {
        "register_endpoint": credit4u_register_path(),
        "register_payload_keys": ", ".join(keys) if keys else "—",
        "register_payload_purpose": purpose or "—",
        **flags,
    }


def new_register_signup_timing_debug() -> dict[str, Any]:
    """register-signup-info CODEF 대기 계측(DEBUG, 민감값 없음)."""
    return {
        "register_signup_info_post_entered": "아니오",
        "codef_register_signup_request_started": "아니오",
        "codef_register_signup_request_finished": "아니오",
        "credit4u_elapsed_seconds": "—",
        "credit4u_http_timeout_seconds": "—",
        "credit4u_timeout_source": "—",
        "payload_has_id": "—",
        "payload_has_password": "—",
        "payload_has_email": "—",
        "password_encrypted": "—",
        "twoWayInfo_saved": "—",
    }


def populate_register_signup_payload_flags(
    signup_timing: dict[str, Any],
    payload: dict[str, Any],
    values: dict[str, Any] | None = None,
) -> None:
    """payload·values 존재 여부만 기록(원문 값 없음)."""
    del values  # noqa: ARG001 — password_encrypted는 payload 기준
    flags = register_payload_field_flags(payload)
    signup_timing.update(flags)


def _prepare_register_signup_timing_before_codef_post(
    signup_timing: dict[str, Any] | None,
    *,
    payload: dict[str, Any],
    values: dict[str, Any] | None,
    http_timeout: float,
) -> None:
    """CODEF register POST 직전 payload 존재 여부만 기록(원문 없음)."""
    if signup_timing is None:
        return
    populate_register_signup_payload_flags(signup_timing, payload, values)
    signup_timing["credit4u_http_timeout_seconds"] = http_timeout


def _finalize_register_signup_timing(
    signup_timing: dict[str, Any] | None,
    *,
    payload: dict[str, Any],
    values: dict[str, Any] | None,
    elapsed: float | None,
    http_post_started: bool,
    http_post_finished: bool,
    timeout_source: str,
    http_timeout: float,
) -> None:
    if signup_timing is None:
        return
    populate_register_signup_payload_flags(signup_timing, payload, values)
    signup_timing["credit4u_http_timeout_seconds"] = http_timeout
    if http_post_started:
        signup_timing["codef_register_signup_request_started"] = "예"
    if http_post_finished:
        signup_timing["codef_register_signup_request_finished"] = "예"
    else:
        signup_timing["codef_register_signup_request_finished"] = "아니오"
    if elapsed is not None:
        signup_timing["credit4u_elapsed_seconds"] = round(elapsed, 3)
    signup_timing["credit4u_timeout_source"] = timeout_source


def extract_register_extra_info(data: dict[str, Any]) -> dict[str, Any]:
    """register 응답 data.extraInfo 추출."""
    extra = data.get("extraInfo")
    if isinstance(extra, dict):
        return dict(extra)
    return {}


def extra_info_has_request_key(extra_info: dict[str, Any], key: str) -> bool:
    """extraInfo에 필드 요청 플래그 키가 있으면 True(값이 비어 있어도)."""
    return key in extra_info


def extra_info_requests_signup_info(extra_info: dict[str, Any]) -> bool:
    return any(
        extra_info_has_request_key(extra_info, key)
        for key in ("reqUserId", "reqUserPass", "reqEmail")
    )


def extra_info_requests_user_id_only(extra_info: dict[str, Any]) -> bool:
    """extraInfo에 reqUserId만 요청(재입력)."""
    return (
        extra_info_has_request_key(extra_info, "reqUserId")
        and not extra_info_has_request_key(extra_info, "reqUserPass")
        and not extra_info_has_request_key(extra_info, "reqEmail")
    )


def extra_info_requests_password_only(extra_info: dict[str, Any]) -> bool:
    return (
        extra_info_has_request_key(extra_info, "reqUserPass")
        and not extra_info_has_request_key(extra_info, "reqUserId")
        and not extra_info_has_request_key(extra_info, "reqEmail")
    )


def extract_register_extra_info_fields(extra_info: dict[str, Any]) -> dict[str, str]:
    """extraInfo code/message/errorMessage(민감정보 아님)."""
    if not isinstance(extra_info, dict):
        return {"code": "", "message": "", "errorMessage": ""}
    return {
        "code": str(extra_info.get("code") or "").strip(),
        "message": str(extra_info.get("message") or "").strip(),
        "errorMessage": str(extra_info.get("errorMessage") or "").strip(),
    }


def store_register_extra_info_on_entry(
    entry: dict[str, Any],
    extra_info: dict[str, Any],
) -> None:
    fields = extract_register_extra_info_fields(extra_info)
    entry["credit4u_register_extra_code"] = fields["code"]
    entry["credit4u_register_extra_message"] = fields["message"]
    entry["credit4u_register_error_message"] = fields["errorMessage"]


def resolve_signup_required_fields(extra_info: dict[str, Any]) -> list[str]:
    """extraInfo 재입력 요청 필드 목록(id/password/email)."""
    required: list[str] = []
    if extra_info_has_request_key(extra_info, "reqUserId"):
        required.append("id")
    if extra_info_has_request_key(extra_info, "reqUserPass"):
        required.append("password")
    if extra_info_has_request_key(extra_info, "reqEmail"):
        required.append("email")
    return required


def register_extra_reason_for_display(extra_info: dict[str, Any]) -> str:
    """화면 표시용 신용정보원 요청 사유(민감정보 아님)."""
    fields = extract_register_extra_info_fields(extra_info)
    return fields["message"] or fields["errorMessage"]


def is_register_password_retry_code(result_code: str) -> bool:
    return (result_code or "").strip() in _REGISTER_PASSWORD_RETRY_CODES


def extra_info_requests_sms(extra_info: dict[str, Any]) -> bool:
    return any(
        extra_info_has_request_key(extra_info, key)
        for key in ("reqSMSAuthNo", "commSimpleAuth")
    )


def extra_info_requests_email_auth(extra_info: dict[str, Any]) -> bool:
    return extra_info_has_request_key(extra_info, "reqEmailAuthNo")


def register_req_secure_no(data: dict[str, Any], extra_info: dict[str, Any]) -> Any:
    for container in (extra_info, data):
        if not isinstance(container, dict):
            continue
        value = container.get("reqSecureNo")
        if _has_nonempty_value(value):
            return value
    return None


def resolve_register_stage_from_followup(
    result_code: str,
    data: dict[str, Any],
    extra_info: dict[str, Any],
    extracted: dict[str, Any] | None = None,
) -> str:
    """register 2차 이상 응답 extraInfo 우선순위 분기."""
    extracted = extracted or {}
    method = str(data.get("method") or extracted.get("method") or "").strip().lower()
    continue2_way = data.get("continue2Way")
    if continue2_way is None:
        continue2_way = extracted.get("continue2Way")

    if is_credit4u_register_completed(result_code, data):
        return "register_completed"
    if extra_info_requests_signup_info(extra_info):
        return "register_signup_info_required"
    if extra_info_requests_email_auth(extra_info):
        return "register_email_auth_required"
    if extra_info_requests_sms(extra_info) or "sms" in method:
        return "register_sms_required"
    if register_req_secure_no(data, extra_info) or extra_info_has_request_key(
        extra_info, "reqSecureNo"
    ):
        return "register_secure_no_required"
    if result_code == "CF-03002" and _truthy_flag(continue2_way):
        return "register_continue_pending"
    return "register_continue_pending"


def register_signup_retry_message(
    result_code: str,
    extra_info: dict[str, Any],
) -> str:
    """회원가입 정보 재입력 안내(extraInfo·result code 반영)."""
    fields = extract_register_extra_info_fields(extra_info)
    code = fields["code"] or (result_code or "").strip()
    extra_text = register_extra_reason_for_display(extra_info)
    required = resolve_signup_required_fields(extra_info)

    if code and code.startswith("CF-"):
        base = user_message_for_credit4u_failure(code, extra_text)
        if extra_text and extra_text not in base:
            base = f"{base} 신용정보원 요청 사유: {extra_text}"
        elif extra_text:
            base = f"{base} (신용정보원 요청 사유: {extra_text})"
        if required:
            return base
        return base

    parts: list[str] = []
    if "id" in required and len(required) == 1:
        parts.append(
            "신용정보원에서 아이디 재입력을 요청했습니다. 다른 아이디로 다시 시도해 주세요."
        )
    elif "id" in required:
        parts.append("신용정보원에서 아이디 재입력을 요청했습니다.")
    if "password" in required:
        parts.append(
            "신용정보원에서 비밀번호 재입력을 요청했습니다. 비밀번호를 다시 생성해 주세요."
        )
    if "email" in required:
        parts.append(
            "신용정보원에서 이메일 재입력을 요청했습니다. 허용 도메인의 이메일을 입력해 주세요."
        )
    if not parts:
        parts.append("회원가입에 사용할 아이디, 비밀번호, 이메일 정보가 필요합니다.")

    message = " ".join(parts)
    if extra_text:
        return f"{message} 신용정보원 요청 사유: {extra_text}"
    return message


def register_followup_stage_message(stage: str, *, sms_retry: bool = False) -> str:
    if stage == "register_sms_required":
        if sms_retry:
            return "SMS 인증번호를 다시 입력해 주세요."
        return "휴대폰 SMS 인증번호를 입력해 주세요."
    messages = {
        "register_secure_no_required": "회원가입을 위한 보안문자 입력이 필요합니다.",
        "register_signup_info_required": (
            "회원가입에 사용할 아이디, 비밀번호, 이메일 정보가 필요합니다."
        ),
        "register_email_auth_required": "이메일 인증번호를 입력해 주세요.",
        "register_completed": "신용정보원 회원가입이 완료되었습니다.",
    }
    return messages.get(stage, "회원가입 절차를 계속 진행합니다.")


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
    if extra_info_requests_signup_info(extra_info):
        return "register_signup_info_required"
    if extra_info_requests_email_auth(extra_info):
        return "register_email_auth_required"
    if extra_info_requests_sms(extra_info) or "sms" in method:
        return "register_sms_required"
    if register_req_secure_no(data, extra_info) or extra_info_has_request_key(
        extra_info, "reqSecureNo"
    ):
        return "register_secure_no_required"
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
    stage = resolve_register_stage_from_followup(
        result_code, data, extra_info, extracted
    )
    if stage != "register_continue_pending":
        return True
    return False


_REGISTER_SIGNUP_RETRY_CODES = frozenset(
    {
        "CF-12824",
        "CF-12825",
        "CF-12826",
        "CF-12827",
        "CF-13341",
        "CF-13343",
        "CF-13349",
    }
)
_REGISTER_SIGNUP_AUTO_RETRY_CODES = frozenset(
    {
        "CF-12824",
        "CF-12825",
        "CF-12826",
        "CF-12827",
        "CF-13349",
    }
)
_REGISTER_SIGNUP_EMAIL_MANUAL_RETRY_CODES = frozenset(
    {
        "CF-13341",
        "CF-13342",
        "CF-13343",
    }
)
_REGISTER_PASSWORD_RETRY_CODES = frozenset({"CF-12826", "CF-12827"})
_REGISTER_EMAIL_RETRY_CODES = frozenset({"CF-13342"})


def is_register_signup_retry_code(result_code: str) -> bool:
    return (result_code or "").strip() in _REGISTER_SIGNUP_RETRY_CODES


def is_register_signup_auto_retry_code(result_code: str) -> bool:
    """ID·비밀번호 자동 재생성·재제출 대상 코드."""
    return (result_code or "").strip() in _REGISTER_SIGNUP_AUTO_RETRY_CODES


def is_register_signup_email_manual_code(result_code: str) -> bool:
    """이메일 수동 재입력 대상 코드."""
    return (result_code or "").strip() in _REGISTER_SIGNUP_EMAIL_MANUAL_RETRY_CODES


def signup_auto_retry_reason_label(result_code: str) -> str:
    code = (result_code or "").strip()
    labels = {
        "CF-13349": "id_duplicate",
        "CF-12824": "id_length",
        "CF-12825": "id_format",
        "CF-12826": "password_length",
        "CF-12827": "password_format",
    }
    return labels.get(code, code or "unknown")


def is_register_email_retry_code(result_code: str) -> bool:
    return (result_code or "").strip() in _REGISTER_EMAIL_RETRY_CODES


def _register_base_from_first_payload(first_payload: dict[str, Any]) -> dict[str, Any]:
    """1차 기본 필드 + checkParamUUID 유지(단계별 필드 제외)."""
    base = {
        k: first_payload[k]
        for k in REGISTER_BASE_PAYLOAD_KEYS
        if k in first_payload
    }
    check = str(first_payload.get("checkParamUUID") or base.get("checkParamUUID") or "").strip()
    if len(check) != 20 or not check.isalnum():
        raise CodefClientError("checkParamUUID(20자)가 register 1차 payload에 없습니다.")
    base["checkParamUUID"] = check
    return base


def build_credit4u_register_second_payload(
    first_payload: dict[str, Any],
    two_way_info: Any,
    values: dict[str, Any] | None = None,
    *,
    purpose: RegisterSecondPurpose,
) -> dict[str, Any]:
    """register 2차 이상 payload — purpose별 허용 필드만 포함."""
    if not isinstance(first_payload, dict) or not first_payload:
        raise CodefClientError("register 1차 payload가 없습니다.")
    if not isinstance(two_way_info, dict) or not two_way_info:
        raise CodefClientError("2차 인증 정보(twoWayInfo)가 없습니다.")
    if purpose not in ("secure_no", "sms", "signup_info", "email_auth"):
        raise CodefClientError("register 추가요청 purpose가 올바르지 않습니다.")

    payload: dict[str, Any] = _register_base_from_first_payload(first_payload)
    vals = values if isinstance(values, dict) else {}

    for key in (
        "secureNo",
        "secureNoRefresh",
        "smsAuthNo",
        "simpleAuth",
        "id",
        "password",
        "email",
        "emailAuthNo",
        "is2Way",
        "twoWayInfo",
    ):
        payload.pop(key, None)

    if purpose == "secure_no":
        secure_no = str(vals.get("secureNo") or "").strip()
        if not secure_no:
            raise CodefClientError("보안문자를 입력해 주세요.")
        payload["secureNo"] = secure_no
        payload["secureNoRefresh"] = str(vals.get("secureNoRefresh") or "0").strip() or "0"
    elif purpose == "sms":
        sms_auth = str(vals.get("smsAuthNo") or "").strip()
        simple_auth = str(vals.get("simpleAuth") or "").strip()
        if sms_auth:
            payload["smsAuthNo"] = sms_auth
        elif simple_auth:
            payload["simpleAuth"] = simple_auth
        else:
            raise CodefClientError("SMS 인증번호를 입력해 주세요.")
    elif purpose == "signup_info":
        user_id = str(vals.get("id") or "").strip()
        plain_password = str(vals.get("password") or "").strip()
        email = str(vals.get("email") or "").strip()
        if not user_id:
            raise CodefClientError("회원가입 아이디가 없습니다.")
        if not plain_password:
            raise CodefClientError("회원가입 비밀번호가 없습니다.")
        if not email:
            raise CodefClientError("회원가입 이메일이 없습니다.")
        payload["id"] = user_id
        payload["email"] = email
        apply_encrypted_password_to_payload(payload, {"password": plain_password})
    elif purpose == "email_auth":
        email_auth = str(vals.get("emailAuthNo") or "").strip()
        if not email_auth:
            raise CodefClientError("이메일 인증번호를 입력해 주세요.")
        payload["emailAuthNo"] = email_auth

    payload["is2Way"] = True
    payload["twoWayInfo"] = sanitize_credit4u_two_way_info(two_way_info)
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
    check_param_uuid: str,
) -> dict[str, Any]:
    """회원가입 1차 payload(type=1: checkParamUUID 필수, id/password/email 빈 값)."""
    name = str(customer.get("name") or "").strip()
    identity = _digits_only(str(customer.get("identity") or ""), max_len=13)
    phone = _digits_only(str(customer.get("phone") or ""))
    if not name or len(identity) != 13 or not phone:
        raise CodefClientError("고객 정보(이름·주민번호·휴대폰)가 올바르지 않습니다.")

    register_type = (os.getenv("CREDIT4U_APPLICATION_TYPE") or "1").strip() or "1"
    check = str(check_param_uuid or "").strip()
    if len(check) != 20 or not check.isalnum():
        raise CodefClientError("checkParamUUID(20자)가 올바르지 않습니다.")
    return {
        "organization": credit4u_organization(),
        "userName": name,
        "identity": identity,
        "telecom": _telecom_code(customer),
        "phoneNo": phone,
        "timeout": credit4u_register_payload_timeout_value(),
        "emailTimeout": credit4u_email_timeout_payload_value(),
        "authMethod": (os.getenv("CREDIT4U_AUTH_METHOD") or "0").strip() or "0",
        "type": register_type,
        "identityEncYn": (os.getenv("CREDIT4U_IDENTITY_ENC_YN") or "N").strip() or "N",
        "checkParamUUID": check,
        "id": "",
        "password": "",
        "email": "",
    }


def register_response_debug(
    payload: dict[str, Any],
    extra_info: dict[str, Any],
    extracted: dict[str, Any],
    *,
    purpose: str = "",
) -> dict[str, Any]:
    """DEBUG용 — register 응답 메타(민감값 제외)."""
    extra_keys = sorted(str(k) for k in extra_info.keys()) if extra_info else []
    fields = extract_register_extra_info_fields(extra_info)
    return {
        **credit4u_register_payload_debug(payload, purpose=purpose),
        "register_extra_info_keys": ", ".join(extra_keys) if extra_keys else "—",
        "register_extra_code": fields["code"] or "—",
        "register_extra_message": fields["message"] or "—",
        "register_error_message": fields["errorMessage"] or "—",
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
        return "아이디 자릿수 오류입니다. 6~12자 영문·숫자 아이디로 다시 생성합니다."
    if code == "CF-12825":
        return (
            "아이디 형식 오류입니다. 첫 글자는 영문이고 특수문자는 사용할 수 없습니다."
        )
    if code == "CF-13349":
        return "이미 등록된 아이디입니다. 다른 아이디를 생성해 주세요."
    if code == "CF-12826":
        return "비밀번호 자릿수 오류입니다. 9~20자 비밀번호로 다시 생성해 주세요."
    if code == "CF-12827":
        return (
            "비밀번호 형식 오류입니다. 영문·숫자·특수문자 조합으로 다시 생성해 주세요."
        )
    if code == "CF-13341":
        return "이미 등록된 이메일입니다. 다른 이메일 주소를 입력해 주세요."
    if code == "CF-13342":
        return (
            "이메일 형식 오류입니다. naver.com 또는 kakao.com 등 허용 도메인을 사용해 주세요."
        )
    if code == "CF-13343":
        return "잘못된 이메일입니다. 이메일 주소를 확인해 주세요."
    if code == "CF-12069":
        return "이미 가입된 주민등록번호입니다. 저장된 계정으로 보험가입이력을 조회합니다."
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
            timeout=credit4u_http_timeout_seconds(),
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
            timeout=credit4u_additional_auth_timeout_seconds(),
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
    check_param_uuid: str,
) -> dict[str, Any]:
    """
    신용정보원 register 1차 POST.
    반환: ok, parsed, result, data, extra_info, extracted, status_code,
          result_code, result_message, register_debug
    """
    payload: dict[str, Any] = {}
    try:
        payload = build_credit4u_register_first_payload(
            customer, credentials, check_param_uuid
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
            timeout=credit4u_http_timeout_seconds(),
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
            "register_debug": credit4u_register_payload_debug(
                payload, purpose="register_first"
            ),
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
            "register_debug": credit4u_register_payload_debug(
                payload, purpose="register_first"
            ),
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
            "register_debug": register_response_debug(
                payload, {}, {}, purpose="register_first"
            ),
            "first_payload": payload,
        }

    result, data = _extract_result_data(parsed)
    result_code = str(result.get("code") or "")
    result_message = str(result.get("message") or "")
    extracted = extract_two_way_info(parsed)
    extra_info = extract_register_extra_info(data)
    status_code = int(response.status_code or 0)
    reg_debug = register_response_debug(
        payload, extra_info, extracted, purpose="register_first"
    )

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
    *,
    purpose: RegisterSecondPurpose,
    signup_timing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    신용정보원 register 2차 이상 POST.
    반환: ok, parsed, result, data, extra_info, extracted, status_code,
          result_code, result_message, register_debug, completed
    signup_timing: register-signup-info 계측용(선택, in-place 갱신).
    """
    payload: dict[str, Any] = {}
    vals = values if isinstance(values, dict) else {}
    http_timeout = credit4u_additional_auth_timeout_seconds()
    http_post_started = False
    http_post_finished = False
    elapsed: float | None = None
    t0 = 0.0
    response = None

    try:
        payload = build_credit4u_register_second_payload(
            first_payload,
            two_way_info,
            values,
            purpose=purpose,
        )
        _prepare_register_signup_timing_before_codef_post(
            signup_timing,
            payload=payload,
            values=vals,
            http_timeout=http_timeout,
        )
        url = credit4u_register_url()
        token = get_codef_access_token()
        if signup_timing is not None:
            signup_timing["codef_register_signup_request_started"] = "예"
        http_post_started = True
        t0 = time.monotonic()
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=http_timeout,
        )
        elapsed = time.monotonic() - t0
        http_post_finished = True
    except CodefClientError as exc:
        if http_post_started:
            elapsed = time.monotonic() - t0
            http_post_finished = True
        _finalize_register_signup_timing(
            signup_timing,
            payload=payload,
            values=vals,
            elapsed=elapsed,
            http_post_started=http_post_started,
            http_post_finished=http_post_finished,
            timeout_source="exception",
            http_timeout=http_timeout,
        )
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
            "register_debug": credit4u_register_payload_debug(payload, purpose=purpose),
            "completed": False,
        }
    except requests.Timeout:
        if http_post_started:
            elapsed = time.monotonic() - t0
            http_post_finished = True
        _finalize_register_signup_timing(
            signup_timing,
            payload=payload,
            values=vals,
            elapsed=elapsed,
            http_post_started=http_post_started,
            http_post_finished=http_post_finished,
            timeout_source="requests_timeout",
            http_timeout=http_timeout,
        )
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
            "register_debug": credit4u_register_payload_debug(payload, purpose=purpose),
            "completed": False,
        }
    except requests.RequestException:
        if http_post_started:
            elapsed = time.monotonic() - t0
            http_post_finished = True
        _finalize_register_signup_timing(
            signup_timing,
            payload=payload,
            values=vals,
            elapsed=elapsed,
            http_post_started=http_post_started,
            http_post_finished=http_post_finished,
            timeout_source="exception",
            http_timeout=http_timeout,
        )
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
            "register_debug": credit4u_register_payload_debug(payload, purpose=purpose),
            "completed": False,
        }

    assert response is not None

    try:
        parsed = parse_codef_response(response)
    except CodefClientError:
        _finalize_register_signup_timing(
            signup_timing,
            payload=payload,
            values=vals,
            elapsed=elapsed,
            http_post_started=True,
            http_post_finished=True,
            timeout_source="exception",
            http_timeout=http_timeout,
        )
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
            "register_debug": register_response_debug(
                payload, {}, {}, purpose=purpose
            ),
            "completed": False,
        }

    result, data = _extract_result_data(parsed)
    result_code = str(result.get("code") or "")
    result_message = str(result.get("message") or "")
    extracted = extract_two_way_info(parsed)
    extra_info = extract_register_extra_info(data)
    status_code = int(response.status_code or 0)
    reg_debug = register_response_debug(
        payload, extra_info, extracted, purpose=purpose
    )
    completed = is_credit4u_register_completed(result_code, data)
    timeout_source = (
        "codef_result"
        if is_credit4u_register_timeout_retryable(result_code)
        else "success"
    )
    _finalize_register_signup_timing(
        signup_timing,
        payload=payload,
        values=vals,
        elapsed=elapsed,
        http_post_started=True,
        http_post_finished=True,
        timeout_source=timeout_source,
        http_timeout=http_timeout,
    )

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
