from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from public_access import acme
from public_access.acme import (
    AcmeChallenge,
    authoritative_txt_visible,
    cleanup_challenge,
    create_challenge,
    wait_for_authoritative_txt,
)
from public_access.dnspod import DnsRecord


def challenge(tmp_path: Path) -> AcmeChallenge:
    return AcmeChallenge(
        certbot_domain="voice.example.com",
        validation="validation-token",
        root_domain="example.com",
        state_dir=tmp_path,
    )


def test_auth_creates_only_expected_txt_and_writes_private_state(tmp_path):
    client = Mock()
    client.create_record.return_value = 77
    state = create_challenge(
        client, challenge(tmp_path), propagation_check=lambda *_: True
    )
    client.create_record.assert_called_once_with(
        "example.com",
        "_acme-challenge.voice",
        "TXT",
        "validation-token",
        line="默认",
        ttl=600,
    )
    assert state.read_text().strip() == "77"
    assert state.stat().st_mode & 0o777 == 0o600


def test_auth_rejects_any_other_certificate_name(tmp_path):
    item = challenge(tmp_path)
    item = AcmeChallenge(
        "other.example.com", item.validation, item.root_domain, item.state_dir
    )
    with pytest.raises(ValueError, match="voice.example.com"):
        create_challenge(Mock(), item, propagation_check=lambda *_: True)


def test_auth_rejects_empty_validation(tmp_path):
    item = challenge(tmp_path)
    item = AcmeChallenge(item.certbot_domain, "", item.root_domain, item.state_dir)
    with pytest.raises(ValueError, match="validation"):
        create_challenge(Mock(), item, propagation_check=lambda *_: True)


def test_failed_propagation_deletes_created_record_and_state(tmp_path):
    client = Mock()
    client.create_record.return_value = 77
    with pytest.raises(TimeoutError):
        create_challenge(
            client, challenge(tmp_path), propagation_check=lambda *_: False
        )
    client.delete_record.assert_called_once_with("example.com", 77)
    assert list(tmp_path.glob("*.state")) == []


def test_cleanup_deletes_only_record_id_saved_by_auth(tmp_path):
    client = Mock()
    client.describe_record.return_value = DnsRecord(
        77, "_acme-challenge.voice", "TXT", "默认", "validation-token", 600
    )
    state = tmp_path / "challenge.state"
    state.write_text("77\n")
    cleanup_challenge(client, "example.com", state)
    client.describe_record.assert_called_once_with("example.com", 77)
    client.delete_record.assert_called_once_with("example.com", 77)
    assert not state.exists()


def test_cleanup_refuses_record_outside_acme_name(tmp_path):
    client = Mock()
    client.describe_record.return_value = DnsRecord(
        77, "voice", "A", "默认", "8.8.8.8", 600
    )
    state = tmp_path / "challenge.state"
    state.write_text("77\n")
    with pytest.raises(ValueError, match="ACME TXT"):
        cleanup_challenge(client, "example.com", state)
    client.delete_record.assert_not_called()
    assert state.exists()


def test_cleanup_missing_state_is_noop(tmp_path):
    client = Mock()
    cleanup_challenge(client, "example.com", tmp_path / "missing.state")
    client.delete_record.assert_not_called()


@pytest.mark.parametrize("contents", ["0\n", "-1\n", "not-a-number\n"])
def test_cleanup_rejects_invalid_record_id(tmp_path, contents):
    state = tmp_path / "challenge.state"
    state.write_text(contents)
    client = Mock()
    with pytest.raises(ValueError, match="record ID"):
        cleanup_challenge(client, "example.com", state)
    client.delete_record.assert_not_called()
    assert state.exists()


def test_authoritative_check_accepts_only_exact_txt_value(monkeypatch):
    dig = Mock(
        side_effect=[
            ["ns1.example.test."],
            ['"wrong-validation-token"', '"validation-token"'],
        ]
    )
    monkeypatch.setattr(acme, "_dig", dig)
    assert authoritative_txt_visible(
        "_acme-challenge.voice.example.com", "validation-token"
    )
    assert dig.call_args_list == [
        (("+short", "NS", "example.com"),),
        (
            (
                "+short",
                "TXT",
                "_acme-challenge.voice.example.com",
                "@ns1.example.test.",
            ),
        ),
    ]


def test_wait_for_txt_polls_at_five_second_intervals(monkeypatch):
    visible = Mock(side_effect=[False, True])
    sleeper = Mock()
    monkeypatch.setattr(acme, "authoritative_txt_visible", visible)
    monkeypatch.setattr(acme.time, "sleep", sleeper)
    wait_for_authoritative_txt("record.example", "expected", timeout_seconds=180)
    sleeper.assert_called_once_with(5)
