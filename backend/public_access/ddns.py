from __future__ import annotations

import json
import logging
import os
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, ip_address

import httpx

from public_access.dnspod import DnsPodClient


PUBLIC_IP_SOURCES = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DdnsConfig:
    domain: str
    domain_id: int
    subdomain: str
    ttl: int = 600

    @classmethod
    def from_environment(cls) -> DdnsConfig:
        domain = os.environ.get("DNSPOD_DOMAIN", "")
        domain_id_text = os.environ.get("DNSPOD_DOMAIN_ID", "")
        subdomain = os.environ.get("DNSPOD_SUBDOMAIN", "")
        ttl_text = os.environ.get("DNSPOD_TTL", "600")
        try:
            domain_id = int(domain_id_text)
            ttl = int(ttl_text)
        except ValueError:
            raise ValueError("DNSPod domain ID and TTL must be integers") from None
        if not domain or not subdomain or domain_id <= 0 or not 1 <= ttl <= 604800:
            raise ValueError("normalized DNSPod domain configuration is required")
        return cls(
            domain=domain, domain_id=domain_id, subdomain=subdomain, ttl=ttl
        )


@dataclass(frozen=True, slots=True)
class DdnsResult:
    action: str
    public_ip: IPv4Address
    record_id: int


def fetch_public_ip_source(url: str) -> str:
    with httpx.Client(
        timeout=5.0, trust_env=False, follow_redirects=False
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def discover_public_ipv4(
    fetch: Callable[[str], str], sources: Sequence[str]
) -> IPv4Address:
    if len(sources) < 3:
        raise RuntimeError("public IPv4 consensus requires at least three sources")

    valid: list[IPv4Address] = []
    for source in sources:
        try:
            candidate = ip_address(fetch(source).strip())
        except Exception:
            continue
        if isinstance(candidate, IPv4Address) and candidate.is_global:
            valid.append(candidate)

    counts = Counter(valid)
    matches = [address for address, count in counts.items() if count >= 2]
    if len(matches) != 1:
        raise RuntimeError("no reliable public IPv4 consensus")
    return matches[0]


def reconcile_voice_record(
    client: DnsPodClient, config: DdnsConfig, public_ip: IPv4Address
) -> DdnsResult:
    actual_domain_id = client.describe_domain(config.domain)
    if actual_domain_id != config.domain_id:
        raise RuntimeError(
            f"DNSPod DomainId mismatch: expected {config.domain_id}, got {actual_domain_id}"
        )

    records = [
        record
        for record in client.list_records(config.domain, config.subdomain, "A")
        if record.line == "默认"
    ]
    if len(records) > 1:
        raise RuntimeError("expected exactly one default-line A record")

    value = str(public_ip)
    if not records:
        record_id = client.create_record(
            config.domain,
            config.subdomain,
            "A",
            value,
            line="默认",
            ttl=config.ttl,
        )
        action = "created"
    else:
        record_id = records[0].record_id
        if records[0].value == value:
            action = "unchanged"
        else:
            client.modify_dynamic_dns(
                config.domain,
                record_id,
                config.subdomain,
                value,
                line="默认",
            )
            action = "updated"

    result = DdnsResult(action=action, public_ip=public_ip, record_id=record_id)
    _LOGGER.info(
        json.dumps(
            {
                "event": "ddns_reconcile",
                "action": result.action,
                "public_ip": str(result.public_ip),
                "record_id": result.record_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return result
