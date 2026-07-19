from __future__ import annotations

import json
import logging
from ipaddress import IPv4Address
from unittest.mock import Mock

import pytest

from public_access.ddns import (
    DdnsConfig,
    discover_public_ipv4,
    fetch_public_ip_source,
    reconcile_voice_record,
)
from public_access.dnspod import DnsRecord


CONFIG = DdnsConfig(
    domain="example.com", domain_id=12345678, subdomain="voice", ttl=600
)


def test_config_reads_normalized_environment(monkeypatch):
    monkeypatch.setenv("DNSPOD_DOMAIN", "example.com")
    monkeypatch.setenv("DNSPOD_DOMAIN_ID", "12345678")
    monkeypatch.setenv("DNSPOD_SUBDOMAIN", "voice")
    assert DdnsConfig.from_environment() == CONFIG


def test_production_fetch_disables_proxy_inheritance_and_redirects(monkeypatch):
    response = Mock(text="8.8.8.8")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.return_value = response
    constructor = Mock(return_value=client)
    monkeypatch.setattr("public_access.ddns.httpx.Client", constructor)

    assert fetch_public_ip_source("https://example.test") == "8.8.8.8"
    constructor.assert_called_once_with(
        timeout=5.0, trust_env=False, follow_redirects=False
    )
    response.raise_for_status.assert_called_once_with()


def test_public_ip_requires_two_matching_global_answers():
    replies = {"a": "8.8.8.8\n", "b": "8.8.8.8", "c": "1.1.1.1"}
    assert discover_public_ipv4(lambda url: replies[url], ("a", "b", "c")) == IPv4Address(
        "8.8.8.8"
    )


@pytest.mark.parametrize(
    "replies",
    [
        {"a": "192.0.2.1", "b": "192.0.2.1", "c": "8.8.8.8"},
        {"a": "8.8.8.8", "b": "1.1.1.1", "c": "9.9.9.9"},
        {"a": "not-an-ip", "b": "", "c": "8.8.8.8"},
    ],
)
def test_public_ip_refuses_private_or_no_consensus(replies):
    with pytest.raises(RuntimeError, match="consensus"):
        discover_public_ipv4(lambda url: replies[url], ("a", "b", "c"))


def test_public_ip_tolerates_one_source_failure():
    def fetch(url: str) -> str:
        if url == "a":
            raise RuntimeError("offline")
        return "8.8.4.4"

    assert discover_public_ipv4(fetch, ("a", "b", "c")) == IPv4Address("8.8.4.4")


def test_missing_record_is_created_once(caplog):
    client = Mock()
    client.describe_domain.return_value = 12345678
    client.list_records.return_value = []
    client.create_record.return_value = 7

    with caplog.at_level(logging.INFO, logger="public_access.ddns"):
        result = reconcile_voice_record(client, CONFIG, IPv4Address("8.8.8.8"))

    client.create_record.assert_called_once_with(
        "example.com", "voice", "A", "8.8.8.8", line="默认", ttl=600
    )
    assert result.action == "created"
    event = json.loads(caplog.records[0].message)
    assert event == {
        "event": "ddns_reconcile",
        "action": "created",
        "public_ip": "8.8.8.8",
        "record_id": 7,
    }


def test_matching_record_is_noop():
    client = Mock()
    client.describe_domain.return_value = 12345678
    client.list_records.return_value = [
        DnsRecord(7, "voice", "A", "默认", "8.8.8.8", 600)
    ]
    assert reconcile_voice_record(client, CONFIG, IPv4Address("8.8.8.8")).action == (
        "unchanged"
    )
    client.modify_dynamic_dns.assert_not_called()


def test_stale_record_is_updated():
    client = Mock()
    client.describe_domain.return_value = 12345678
    client.list_records.return_value = [
        DnsRecord(7, "voice", "A", "默认", "1.1.1.1", 600)
    ]
    result = reconcile_voice_record(client, CONFIG, IPv4Address("8.8.8.8"))
    client.modify_dynamic_dns.assert_called_once_with(
        "example.com", 7, "voice", "8.8.8.8", line="默认"
    )
    assert result.action == "updated"


def test_multiple_default_a_records_are_never_modified():
    client = Mock()
    client.describe_domain.return_value = 12345678
    client.list_records.return_value = [
        DnsRecord(7, "voice", "A", "默认", "1.1.1.1", 600),
        DnsRecord(8, "voice", "A", "默认", "8.8.8.8", 600),
    ]
    with pytest.raises(RuntimeError, match="exactly one"):
        reconcile_voice_record(client, CONFIG, IPv4Address("8.8.8.8"))
    client.modify_dynamic_dns.assert_not_called()


def test_domain_id_mismatch_refuses_all_writes():
    client = Mock()
    client.describe_domain.return_value = 1
    with pytest.raises(RuntimeError, match="DomainId"):
        reconcile_voice_record(client, CONFIG, IPv4Address("8.8.8.8"))
    client.list_records.assert_not_called()
    client.create_record.assert_not_called()
