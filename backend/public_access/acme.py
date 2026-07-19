from __future__ import annotations

import hashlib
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from public_access.dnspod import DnsPodClient


_CERTIFICATE_DOMAIN = "voice.example.com"
_ROOT_DOMAIN = "example.com"
_TXT_SUBDOMAIN = "_acme-challenge.voice"
_TXT_FQDN = f"{_TXT_SUBDOMAIN}.{_ROOT_DOMAIN}"


@dataclass(frozen=True, slots=True)
class AcmeChallenge:
    certbot_domain: str
    validation: str
    root_domain: str
    state_dir: Path

    @property
    def state_path(self) -> Path:
        state_hash = hashlib.sha256(self.validation.encode("utf-8")).hexdigest()
        return self.state_dir / f"{state_hash}.state"


def authoritative_txt_visible(fqdn: str, expected: str) -> bool:
    nameservers = _dig("+short", "NS", _ROOT_DOMAIN)
    for nameserver in nameservers:
        answers = _dig("+short", "TXT", fqdn, f"@{nameserver}")
        if expected in {_unquote_txt(answer) for answer in answers}:
            return True
    return False


def wait_for_authoritative_txt(
    fqdn: str, expected: str, timeout_seconds: int = 180
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if authoritative_txt_visible(fqdn, expected):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("authoritative TXT propagation timed out")
        time.sleep(5)


def create_challenge(
    client: DnsPodClient,
    challenge: AcmeChallenge,
    propagation_check: Callable[[str, str], bool] = authoritative_txt_visible,
) -> Path:
    _validate_challenge(challenge)
    challenge.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    challenge.state_dir.chmod(0o700)
    state_path = challenge.state_path
    if os.path.lexists(state_path):
        raise ValueError("challenge state already exists")

    record_id = client.create_record(
        challenge.root_domain,
        _TXT_SUBDOMAIN,
        "TXT",
        challenge.validation,
        line="默认",
        ttl=600,
    )
    try:
        _write_private_state(state_path, record_id)
        if propagation_check is authoritative_txt_visible:
            wait_for_authoritative_txt(_TXT_FQDN, challenge.validation)
        elif not propagation_check(_TXT_FQDN, challenge.validation):
            raise TimeoutError("authoritative TXT propagation timed out")
    except Exception:
        try:
            client.delete_record(challenge.root_domain, record_id)
        finally:
            state_path.unlink(missing_ok=True)
        raise
    return state_path


def cleanup_challenge(
    client: DnsPodClient, root_domain: str, state_path: Path
) -> None:
    if root_domain != _ROOT_DOMAIN:
        raise ValueError(f"root domain must be {_ROOT_DOMAIN}")
    if not state_path.exists():
        return
    if state_path.is_symlink() or not state_path.is_file():
        raise ValueError("challenge state must be a regular file")
    try:
        record_id = int(state_path.read_text(encoding="utf-8").strip())
    except ValueError:
        raise ValueError("challenge state contains an invalid record ID") from None
    if record_id <= 0:
        raise ValueError("challenge state contains an invalid record ID")
    record = client.describe_record(root_domain, record_id)
    if (
        record.name != _TXT_SUBDOMAIN
        or record.record_type != "TXT"
        or record.line != "默认"
    ):
        raise ValueError("saved record ID is not the expected ACME TXT record")
    client.delete_record(root_domain, record_id)
    state_path.unlink()


def _validate_challenge(challenge: AcmeChallenge) -> None:
    if challenge.certbot_domain != _CERTIFICATE_DOMAIN:
        raise ValueError(f"certificate domain must be {_CERTIFICATE_DOMAIN}")
    if challenge.root_domain != _ROOT_DOMAIN:
        raise ValueError(f"root domain must be {_ROOT_DOMAIN}")
    if not challenge.validation:
        raise ValueError("validation token must be non-empty")


def _write_private_state(path: Path, record_id: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, f"{record_id}\n".encode("ascii"))
    finally:
        os.close(descriptor)
    path.chmod(0o600)


def _dig(*arguments: str) -> list[str]:
    try:
        completed = subprocess.run(
            ["dig", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("authoritative DNS query failed") from None
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _unquote_txt(answer: str) -> str:
    if len(answer) >= 2 and answer[0] == '"' and answer[-1] == '"':
        return answer[1:-1]
    return answer
