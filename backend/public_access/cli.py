from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from public_access.acme import AcmeChallenge, cleanup_challenge, create_challenge
from public_access.ddns import (
    PUBLIC_IP_SOURCES,
    DdnsConfig,
    discover_public_ipv4,
    fetch_public_ip_source,
    reconcile_voice_record,
)
from public_access.dnspod import DnsPodClient, DnsPodCredentials, DnsPodError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-voice-public-access")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--domain", default=os.environ.get("DNSPOD_DOMAIN"))
    validate.add_argument("--domain-id", default=os.environ.get("DNSPOD_DOMAIN_ID"))

    ddns = commands.add_parser("ddns")
    ddns.add_argument("--domain", default=os.environ.get("DNSPOD_DOMAIN"))
    ddns.add_argument("--domain-id", default=os.environ.get("DNSPOD_DOMAIN_ID"))
    ddns.add_argument("--subdomain", default=os.environ.get("DNSPOD_SUBDOMAIN"))
    ddns.add_argument("--ttl", default=os.environ.get("DNSPOD_TTL", "600"))

    commands.add_parser("acme-auth")
    commands.add_parser("acme-cleanup")
    return parser


def _client_from_environment() -> DnsPodClient:
    return DnsPodClient(DnsPodCredentials.from_environment())


def _positive_integer(value: str | None, name: str) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _run_validate(args: argparse.Namespace) -> None:
    domain = _required(args.domain, "domain")
    expected_domain_id = _positive_integer(args.domain_id, "domain ID")
    client = _client_from_environment()
    actual_domain_id = client.describe_domain(domain)
    if actual_domain_id != expected_domain_id:
        raise RuntimeError("DNSPod DomainId does not match the configured domain")
    subdomain = os.environ.get("DNSPOD_SUBDOMAIN", "voice")
    client.list_records(domain, subdomain, "A")
    print(f"domain_id={actual_domain_id} status=allowed")


def _run_ddns(args: argparse.Namespace) -> None:
    config = DdnsConfig(
        domain=_required(args.domain, "domain"),
        domain_id=_positive_integer(args.domain_id, "domain ID"),
        subdomain=_required(args.subdomain, "subdomain"),
        ttl=_positive_integer(args.ttl, "TTL"),
    )
    client = _client_from_environment()
    public_ip = discover_public_ipv4(fetch_public_ip_source, PUBLIC_IP_SOURCES)
    result = reconcile_voice_record(client, config, public_ip)
    print(
        f"action={result.action} public_ip={result.public_ip} "
        f"record_id={result.record_id}"
    )


def _challenge_from_environment() -> AcmeChallenge:
    return AcmeChallenge(
        certbot_domain=os.environ.get("CERTBOT_DOMAIN", ""),
        validation=os.environ.get("CERTBOT_VALIDATION", ""),
        root_domain=os.environ.get("DNSPOD_DOMAIN", "example.com"),
        state_dir=Path("/run/claude-voice-acme"),
    )


def _run_acme_auth() -> None:
    challenge = _challenge_from_environment()
    state_path = create_challenge(_client_from_environment(), challenge)
    print(f"status=challenge-created state={state_path.name}")


def _run_acme_cleanup() -> None:
    challenge = _challenge_from_environment()
    if not challenge.validation:
        raise ValueError("CERTBOT_VALIDATION is required")
    cleanup_challenge(
        _client_from_environment(), challenge.root_domain, challenge.state_path
    )
    print("status=challenge-cleaned")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            _run_validate(args)
        elif args.command == "ddns":
            _run_ddns(args)
        elif args.command == "acme-auth":
            _run_acme_auth()
        elif args.command == "acme-cleanup":
            _run_acme_cleanup()
        else:
            raise ValueError(f"{args.command} is not implemented")
    except DnsPodError as exc:
        print(
            f"error code={exc.code} message=DNSPod request failed",
            file=sys.stderr,
        )
        return 1
    except ValueError:
        print(
            "error code=ConfigurationError message=invalid configuration",
            file=sys.stderr,
        )
        return 1
    except RuntimeError:
        print(
            "error code=OperationFailed message=request could not be completed",
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            "error code=SystemError message=required system operation failed",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
