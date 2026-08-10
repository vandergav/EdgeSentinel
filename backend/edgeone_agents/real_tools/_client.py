"""
Minimal Tencent Cloud API (v3 / "TC3-HMAC-SHA256") signed request client for
the EdgeOne ("teo") service, used by every module in real_tools/.

Implemented by hand against Tencent's public signature docs
(https://www.tencentcloud.com/document/product/1080/38769) rather than the
official SDK, so the whole auth path is visible and auditable in one place.
For production hardening, consider swapping this for the official
`tencentcloud-sdk-python` package instead — same signature under the hood.

Credentials are read from the environment (see config.py / .env.example):
    TENCENTCLOUD_SECRET_ID
    TENCENTCLOUD_SECRET_KEY
    TENCENTCLOUD_TOKEN        (optional — only for temporary/STS credentials)
    EDGEONE_ENDPOINT          (default: teo.intl.tencentcloudapi.com)
    EDGEONE_API_VERSION       (default: 2022-09-01)
    EDGEONE_REGION            (optional — EdgeOne is a global product; most
                               actions don't need this, but it's supported
                               for the ones that do)

NOTE: this repo's api_catalog.py / .go reference material mixes Tencent's
international EdgeOne docs (product 1145, teo.intl.tencentcloudapi.com) and
mainland docs (product 1552, teo.tencentcloudapi.com). Field names are
almost identical between the two, but if you're on the mainland site,
double check exact parameters against https://cloud.tencent.com/document/product/1552
and set EDGEONE_ENDPOINT=teo.tencentcloudapi.com.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Optional

import requests

SERVICE = "teo"
DEFAULT_ENDPOINT = "teo.intl.tencentcloudapi.com"
DEFAULT_API_VERSION = "2022-09-01"
ALGORITHM = "TC3-HMAC-SHA256"

logger = logging.getLogger(__name__)


def _log_event(event: str, level: int, *, exc_info: bool = False, **fields: Any) -> None:
    """Emit a JSON log line even when the process uses Python's default formatter."""
    logger.log(
        level,
        json.dumps({"event": event, **fields}, sort_keys=True, default=str),
        exc_info=exc_info,
    )


class TencentCloudApiError(RuntimeError):
    """Raised when the EdgeOne API itself returns an error envelope."""

    def __init__(self, code: str, message: str, request_id: Optional[str] = None):
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"[{code}] {message} (RequestId={request_id})")


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _derive_signing_key(secret_key: str, date: str, service: str) -> bytes:
    k_date = _hmac_sha256(f"TC3{secret_key}".encode("utf-8"), date)
    k_service = _hmac_sha256(k_date, service)
    k_signing = _hmac_sha256(k_service, "tc3_request")
    return k_signing


def call_tencent_api(
    action: str,
    params: dict[str, Any],
    *,
    service: str,
    endpoint: str,
    version: str,
    region: Optional[str] = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Signs and sends one Tencent Cloud API v3 request, returns ``Response``.

    Kept here so application services can use a Tencent API without creating a
    second signer outside ``edgeone_agents``. Callers must provide their
    product service, endpoint, and API version explicitly.
    """
    secret_id = os.environ.get("TENCENTCLOUD_SECRET_ID")
    secret_key = os.environ.get("TENCENTCLOUD_SECRET_KEY")
    token = os.environ.get("TENCENTCLOUD_TOKEN")
    if not secret_id or not secret_key:
        raise EnvironmentError(
            "TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY are not set. "
            "Copy .env.example to .env and fill in your Tencent Cloud API keys."
        )

    host = endpoint
    api_version = version
    region = region or None

    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    payload = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    request_fingerprint = _sha256_hex(payload)[:16]
    log_context = {
        "service": service,
        "action": action,
        "endpoint": host,
        "api_version": api_version,
        "region": region,
        "parameter_keys": sorted(params),
        "request_fingerprint": request_fingerprint,
    }

    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = _sha256_hex(payload)
    canonical_request = "\n".join([
        "POST", "/", "", canonical_headers, signed_headers, hashed_payload,
    ])
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join([
        ALGORITHM, str(timestamp), credential_scope, _sha256_hex(canonical_request),
    ])
    signing_key = _derive_signing_key(secret_key, date, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"{ALGORITHM} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": api_version,
    }
    if region:
        headers["X-TC-Region"] = region
    if token:
        headers["X-TC-Token"] = token

    started_at = time.perf_counter()
    _log_event("tencent_api.request_started", logging.INFO, **log_context)
    try:
        resp = requests.post(f"https://{host}/", headers=headers, data=payload.encode("utf-8"), timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        _log_event("tencent_api.request_failed", logging.ERROR, exc_info=True, **{**log_context, "duration_ms": round((time.perf_counter() - started_at) * 1000), "error_type": type(exc).__name__, "http_status": getattr(exc.response, "status_code", None)})
        raise
    except ValueError as exc:
        _log_event("tencent_api.invalid_json_response", logging.ERROR, exc_info=True, **{**log_context, "duration_ms": round((time.perf_counter() - started_at) * 1000), "error_type": type(exc).__name__, "http_status": resp.status_code})
        raise

    envelope = body.get("Response", {})
    if "Error" in envelope:
        err = envelope["Error"]
        _log_event("tencent_api.api_error", logging.WARNING, **{**log_context, "duration_ms": round((time.perf_counter() - started_at) * 1000), "http_status": resp.status_code, "request_id": envelope.get("RequestId"), "error_code": err.get("Code", "UnknownError")})
        raise TencentCloudApiError(err.get("Code", "UnknownError"), err.get("Message", ""), envelope.get("RequestId"))
    _log_event("tencent_api.request_succeeded", logging.INFO, **{**log_context, "duration_ms": round((time.perf_counter() - started_at) * 1000), "http_status": resp.status_code, "request_id": envelope.get("RequestId")})
    return envelope


def call_edgeone_api(
    action: str,
    params: dict[str, Any],
    *,
    version: Optional[str] = None,
    endpoint: Optional[str] = None,
    region: Optional[str] = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Signs and sends one Tencent Cloud EdgeOne API request, returns the parsed `Response`.

    Args:
        action: The Tencent API action name, e.g. "DescribeZones".
        params: The action's request body (already in Tencent's JSON shape).
        version: Overrides EDGEONE_API_VERSION.
        endpoint: Overrides EDGEONE_ENDPOINT.
        region: Optional X-TC-Region header; most EdgeOne actions don't need it.
        timeout: HTTP timeout in seconds.

    Returns:
        The `Response` object from the API's JSON envelope (i.e. already
        unwrapped from {"Response": {...}}).

    Raises:
        TencentCloudApiError: if the API responded with an Error envelope.
        EnvironmentError: if credentials aren't configured.
    """
    return call_tencent_api(
        action,
        params,
        service=SERVICE,
        endpoint=endpoint or os.environ.get("EDGEONE_ENDPOINT", DEFAULT_ENDPOINT),
        version=version or os.environ.get("EDGEONE_API_VERSION", DEFAULT_API_VERSION),
        region=region or os.environ.get("EDGEONE_REGION") or None,
        timeout=timeout,
    )


def clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drops None values so optional tool args don't get sent as JSON nulls."""
    return {k: v for k, v in params.items() if v is not None}


def tencent_api(name: str):
    """Decorator that tags a tool function with the Tencent API action it implements,
    so agent_builder.py can exclude that action from the category's stub/catalog tools."""

    def decorator(fn):
        fn._tencent_api = name
        return fn

    return decorator
