from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx


_ENDPOINT = "https://dnspod.tencentcloudapi.com"
_HOST = "dnspod.tencentcloudapi.com"
_SERVICE = "dnspod"
_VERSION = "2021-03-23"
_ALGORITHM = "TC3-HMAC-SHA256"
_RETRY_DELAYS = (1, 3, 9)
_TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_ALLOWED_ACTIONS = frozenset(
    {
        "DescribeRecord",
        "DescribeDomain",
        "DescribeRecordList",
        "CreateRecord",
        "DeleteRecord",
        "ModifyDynamicDNS",
    }
)


@dataclass(frozen=True, slots=True)
class DnsPodCredentials:
    secret_id: str
    secret_key: str

    @classmethod
    def from_environment(cls) -> DnsPodCredentials:
        secret_id = os.environ.get("DNSPOD_SECRET_ID", "")
        secret_key = os.environ.get("DNSPOD_SECRET_KEY", "")
        if not re.fullmatch(r"AKID[A-Za-z0-9]+", secret_id) or not re.fullmatch(
            r"[A-Za-z0-9]+", secret_key
        ):
            raise ValueError(
                "DNSPod credentials must be present as normalized CAM values"
            )
        return cls(secret_id=secret_id, secret_key=secret_key)


@dataclass(frozen=True, slots=True)
class DnsRecord:
    record_id: int
    name: str
    record_type: str
    line: str
    value: str
    ttl: int


class DnsPodError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"DNSPod {code}: {message}")


class DnsPodClient:
    def __init__(
        self,
        credentials: DnsPodCredentials,
        *,
        transport: Callable[[httpx.Request], httpx.Response] | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._credentials = credentials
        self._clock = clock
        self._sleeper = sleeper
        self._http_client: httpx.Client | None = None
        if transport is None:
            self._http_client = httpx.Client(timeout=15.0)
            self._transport = self._http_client.send
        else:
            self._transport = transport

    def describe_domain(self, domain: str) -> int:
        payload = self._call("DescribeDomain", {"Domain": domain})
        return int(payload["DomainInfo"]["DomainId"])

    def list_records(
        self, domain: str, subdomain: str, record_type: str
    ) -> list[DnsRecord]:
        try:
            payload = self._call(
                "DescribeRecordList",
                {
                    "Domain": domain,
                    "Subdomain": subdomain,
                    "RecordType": record_type,
                },
            )
        except DnsPodError as exc:
            if exc.code == "ResourceNotFound.NoDataOfRecord":
                return []
            raise
        return [self._record(item) for item in payload.get("RecordList", [])]

    def create_record(
        self,
        domain: str,
        subdomain: str,
        record_type: str,
        value: str,
        *,
        line: str = "默认",
        ttl: int = 600,
    ) -> int:
        payload = self._call(
            "CreateRecord",
            {
                "Domain": domain,
                "SubDomain": subdomain,
                "RecordType": record_type,
                "RecordLine": line,
                "Value": value,
                "TTL": ttl,
            },
        )
        return int(payload["RecordId"])

    def modify_dynamic_dns(
        self,
        domain: str,
        record_id: int,
        subdomain: str,
        value: str,
        *,
        line: str = "默认",
    ) -> int:
        payload = self._call(
            "ModifyDynamicDNS",
            {
                "Domain": domain,
                "RecordId": record_id,
                "SubDomain": subdomain,
                "RecordLine": line,
                "Value": value,
            },
        )
        return int(payload.get("RecordId", record_id))

    def delete_record(self, domain: str, record_id: int) -> None:
        self._call("DeleteRecord", {"Domain": domain, "RecordId": record_id})

    def describe_record(self, domain: str, record_id: int) -> DnsRecord:
        payload = self._call(
            "DescribeRecord", {"Domain": domain, "RecordId": record_id}
        )
        item = payload["RecordInfo"]
        return DnsRecord(
            record_id=int(item["Id"]),
            name=str(item["SubDomain"]),
            record_type=str(item["RecordType"]),
            line=str(item["RecordLine"]),
            value=str(item["Value"]),
            ttl=int(item["TTL"]),
        )

    def _call(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if action not in _ALLOWED_ACTIONS:
            raise DnsPodError("ActionNotAllowed", f"action {action} is not allowed")

        payload = json.dumps(
            parameters, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        last_error: DnsPodError | None = None

        for attempt in range(len(_RETRY_DELAYS) + 1):
            request = self._signed_request(action, payload)
            try:
                response = self._transport(request)
            except httpx.TransportError:
                last_error = DnsPodError("TransportError", "request transport failed")
                if attempt < len(_RETRY_DELAYS):
                    self._sleeper(_RETRY_DELAYS[attempt])
                    continue
                raise last_error

            if response.status_code in _TRANSIENT_HTTP_STATUSES:
                last_error = DnsPodError(
                    "HttpStatus", f"transient HTTP status {response.status_code}"
                )
                if attempt < len(_RETRY_DELAYS):
                    self._sleeper(_RETRY_DELAYS[attempt])
                    continue
                raise last_error
            if not 200 <= response.status_code < 300:
                raise DnsPodError(
                    "HttpStatus", f"unexpected HTTP status {response.status_code}"
                )

            try:
                envelope = response.json()["Response"]
            except (KeyError, TypeError, ValueError):
                raise DnsPodError("InvalidResponse", "invalid API response") from None

            error = envelope.get("Error")
            if error:
                code = str(error.get("Code", "UnknownError"))
                message = str(error.get("Message", "API request failed"))
                last_error = DnsPodError(code, message)
                if code == "RequestLimitExceeded" and attempt < len(_RETRY_DELAYS):
                    self._sleeper(_RETRY_DELAYS[attempt])
                    continue
                raise last_error
            return envelope

        raise last_error or DnsPodError("UnknownError", "API request failed")

    def _signed_request(self, action: str, payload: bytes) -> httpx.Request:
        timestamp = int(self._clock())
        date = datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")
        content_type = "application/json; charset=utf-8"
        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{_HOST}\n"
            f"x-tc-action:{action.lower()}\n"
        )
        signed_headers = "content-type;host;x-tc-action"
        hashed_payload = hashlib.sha256(payload).hexdigest()
        canonical_request = (
            "POST\n/\n\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{hashed_payload}"
        )
        credential_scope = f"{date}/{_SERVICE}/tc3_request"
        string_to_sign = (
            f"{_ALGORITHM}\n{timestamp}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )
        secret_date = _hmac_sha256(
            ("TC3" + self._credentials.secret_key).encode("utf-8"), date
        )
        secret_service = _hmac_sha256(secret_date, _SERVICE)
        secret_signing = _hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        authorization = (
            f"{_ALGORITHM} Credential={self._credentials.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return httpx.Request(
            "POST",
            _ENDPOINT,
            content=payload,
            headers={
                "Authorization": authorization,
                "Content-Type": content_type,
                "Host": _HOST,
                "X-TC-Action": action,
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Version": _VERSION,
            },
        )

    @staticmethod
    def _record(item: dict[str, Any]) -> DnsRecord:
        return DnsRecord(
            record_id=int(item["RecordId"]),
            name=str(item["Name"]),
            record_type=str(item["Type"]),
            line=str(item["Line"]),
            value=str(item["Value"]),
            ttl=int(item["TTL"]),
        )


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
