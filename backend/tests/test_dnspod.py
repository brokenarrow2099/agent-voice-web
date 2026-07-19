from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from public_access.dnspod import DnsPodClient, DnsPodCredentials, DnsPodError


def credentials() -> DnsPodCredentials:
    return DnsPodCredentials(secret_id="AKIDEXAMPLE", secret_key="secretexample")


def response(payload: dict, status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "https://dnspod.tencentcloudapi.com")
    return httpx.Response(status, request=request, json={"Response": payload})


def test_credentials_require_normalized_cam_values(monkeypatch):
    monkeypatch.setenv("DNSPOD_SECRET_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("DNSPOD_SECRET_KEY", "secretexample")
    assert DnsPodCredentials.from_environment() == credentials()


def test_credentials_reject_labels_and_missing_values(monkeypatch):
    monkeypatch.setenv("DNSPOD_SECRET_ID", "SecretId:AKIDEXAMPLE")
    monkeypatch.delenv("DNSPOD_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="normalized"):
        DnsPodCredentials.from_environment()


def test_tc3_authorization_is_deterministic_and_never_contains_secret_key():
    transport = Mock(return_value=response({"DomainInfo": {"DomainId": 12345678}}))
    client = DnsPodClient(credentials(), transport=transport, clock=lambda: 1_700_000_000)

    assert client.describe_domain("example.com") == 12345678

    request = transport.call_args.args[0]
    assert request.headers["Authorization"] == (
        "TC3-HMAC-SHA256 Credential=AKIDEXAMPLE/2023-11-14/dnspod/tc3_request, "
        "SignedHeaders=content-type;host;x-tc-action, "
        "Signature=ff18a15378776eb13a07576750b8eff0b16379e8769061db81d47e326bc41ae7"
    )
    assert "secretexample" not in request.headers["Authorization"]
    assert request.headers["X-TC-Action"] == "DescribeDomain"
    assert request.content == b'{"Domain":"example.com"}'


def test_api_error_is_redacted():
    transport = Mock(
        return_value=response(
            {"Error": {"Code": "AuthFailure.UnauthorizedOperation", "Message": "denied"}}
        )
    )
    client = DnsPodClient(credentials(), transport=transport, sleeper=lambda _: None)
    with pytest.raises(DnsPodError) as caught:
        client.describe_domain("example.com")
    assert caught.value.code == "AuthFailure.UnauthorizedOperation"
    assert "AKIDEXAMPLE" not in str(caught.value)
    assert "secretexample" not in str(caught.value)


def test_list_records_returns_typed_records():
    payload = {
        "RecordList": [
            {
                "RecordId": 7,
                "Name": "voice",
                "Type": "A",
                "Line": "默认",
                "Value": "203.0.113.9",
                "TTL": 600,
            }
        ]
    }
    transport = Mock(return_value=response(payload))
    records = DnsPodClient(credentials(), transport=transport).list_records(
        "example.com", "voice", "A"
    )
    assert [(record.record_id, record.value, record.ttl) for record in records] == [
        (7, "203.0.113.9", 600)
    ]


def test_no_record_error_becomes_empty_list():
    transport = Mock(
        return_value=response(
            {
                "Error": {
                    "Code": "ResourceNotFound.NoDataOfRecord",
                    "Message": "not found",
                }
            }
        )
    )
    assert DnsPodClient(credentials(), transport=transport).list_records(
        "example.com", "voice", "A"
    ) == []


def test_describe_record_maps_detail_response_fields():
    payload = {
        "RecordInfo": {
            "Id": 77,
            "SubDomain": "_acme-challenge.voice",
            "RecordType": "TXT",
            "RecordLine": "默认",
            "Value": "validation-token",
            "TTL": 600,
        }
    }
    transport = Mock(return_value=response(payload))
    record = DnsPodClient(credentials(), transport=transport).describe_record(
        "example.com", 77
    )
    assert (
        record.record_id,
        record.name,
        record.record_type,
        record.line,
        record.value,
        record.ttl,
    ) == (77, "_acme-challenge.voice", "TXT", "默认", "validation-token", 600)


def test_request_limit_retries_with_bounded_delays():
    limited = response(
        {"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}}
    )
    success = response({"DomainInfo": {"DomainId": 12345678}})
    transport = Mock(side_effect=[limited, limited, success])
    sleeper = Mock()

    assert (
        DnsPodClient(credentials(), transport=transport, sleeper=sleeper).describe_domain(
            "example.com"
        )
        == 12345678
    )
    assert sleeper.call_args_list == [((1,),), ((3,),)]


def test_disallowed_action_is_rejected_before_transport():
    transport = Mock()
    client = DnsPodClient(credentials(), transport=transport)
    with pytest.raises(DnsPodError, match="not allowed"):
        client._call("ModifyRecord", {})
    transport.assert_not_called()
