from __future__ import annotations

from ipaddress import IPv4Address
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from public_access import cli
from public_access.ddns import DdnsResult
from public_access.dnspod import DnsPodError


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_ddns_unit_is_oneshot_and_reads_private_environment_file():
    unit = read("deploy/claude-voice-ddns.service")
    assert "Type=oneshot" in unit
    assert "EnvironmentFile=%h/.config/claude-voice/dnspod.env" in unit
    assert "python -m public_access.cli ddns" in unit
    assert "StandardOutput=journal" in unit
    for protection in (
        "UMask=0077",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "ReadWritePaths=%h/.local/state/claude-voice",
    ):
        assert protection in unit


def test_ddns_timer_is_persistent_and_bounded():
    timer = read("deploy/claude-voice-ddns.timer")
    assert "OnBootSec=30s" in timer
    assert "OnUnitActiveSec=2min" in timer
    assert "RandomizedDelaySec=15s" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


def test_credential_script_uses_hidden_input_and_mode_0600():
    script = read("scripts/configure-dnspod-credentials.sh")
    assert script.count("read -r -s") == 2
    assert "umask 077" in script
    assert "chmod 600" in script
    assert "DNSPOD_SECRET_ID=" in script
    assert "DNSPOD_SECRET_KEY=" in script
    assert "TXCLOUD_DNS_POD_API_KEY" not in script
    assert ".bashrc" not in script


def test_validate_reports_only_allowed_domain(monkeypatch, capsys):
    client = Mock()
    client.describe_domain.return_value = 12345678
    client.list_records.return_value = []
    monkeypatch.setattr(cli, "_client_from_environment", lambda: client)

    result = cli.main(
        ["validate", "--domain", "example.com", "--domain-id", "12345678"]
    )

    assert result == 0
    assert capsys.readouterr() == (
        "domain_id=12345678 status=allowed\n",
        "",
    )
    client.list_records.assert_called_once_with("example.com", "voice", "A")


def test_ddns_unchanged_returns_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client_from_environment", Mock(return_value=Mock()))
    monkeypatch.setattr(
        cli, "discover_public_ipv4", Mock(return_value=IPv4Address("8.8.8.8"))
    )
    monkeypatch.setattr(
        cli,
        "reconcile_voice_record",
        Mock(return_value=DdnsResult("unchanged", IPv4Address("8.8.8.8"), 7)),
    )

    result = cli.main(
        [
            "ddns",
            "--domain",
            "example.com",
            "--domain-id",
            "12345678",
            "--subdomain",
            "voice",
            "--ttl",
            "600",
        ]
    )

    assert result == 0
    assert capsys.readouterr() == (
        "action=unchanged public_ip=8.8.8.8 record_id=7\n",
        "",
    )


def test_cli_errors_never_echo_secrets_or_authorization(monkeypatch, capsys):
    leaked = "DNSPOD_SECRET_KEY=secretexample Authorization=TC3-HMAC-SHA256"

    def fail():
        raise DnsPodError("AuthFailure", leaked)

    monkeypatch.setattr(cli, "_client_from_environment", fail)
    result = cli.main(
        ["validate", "--domain", "example.com", "--domain-id", "12345678"]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "error code=AuthFailure message=DNSPod request failed\n"
    assert "secretexample" not in captured.err
    assert "Authorization" not in captured.err


def test_certbot_wrappers_source_only_private_environment_file():
    for name, command in (
        ("certbot-dnspod-auth.sh", "acme-auth"),
        ("certbot-dnspod-cleanup.sh", "acme-cleanup"),
    ):
        script = read(f"scripts/{name}")
        assert "set -euo pipefail" in script
        assert "source /home/agentvoice/.config/claude-voice/dnspod.env" in script
        assert f"python -m public_access.cli {command}" in script
        assert "SecretId" not in script and "SecretKey" not in script


def test_certbot_deploy_hook_validates_before_reload():
    script = read("scripts/certbot-nginx-deploy.sh")
    assert script.index("/usr/sbin/nginx -t") < script.index(
        "/usr/bin/systemctl reload nginx"
    )


def test_nginx_exposes_only_ipv4_443_and_preserves_websocket():
    config = read("deploy/nginx/claude-voice.conf")
    assert "listen 443 ssl;" in config
    assert "listen [::]" not in config
    assert "listen 80" not in config
    assert "server_name voice.example.com;" in config
    assert "proxy_pass https://127.0.0.1:8443;" in config
    assert "proxy_ssl_verify off;" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config
    assert 'proxy_set_header Connection "upgrade";' in config
    assert "127.0.0.1:8060" not in config
    assert "127.0.0.1:8766" not in config


def test_nginx_redacts_pairing_and_limits_public_entry():
    config = read("deploy/nginx/claude-voice.conf")
    assert "access_log off;" in config
    assert "error_log /dev/null crit;" in config
    assert "limit_req zone=voice_pair" in config
    assert "limit_conn voice_ws" in config
    assert "client_max_body_size 1m;" in config
    assert "proxy_read_timeout 3600s;" in config
    assert "ssl_session_tickets off;" in config


def test_nginx_configuration_parses_with_temporary_certificate(tmp_path):
    nginx = shutil.which("nginx")
    if nginx is None:
        pytest.skip("nginx is not installed")

    key = tmp_path / "test.key"
    certificate = tmp_path / "test.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=voice.example.com",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
        ],
        check=True,
        capture_output=True,
    )
    vhost = read("deploy/nginx/claude-voice.conf")
    vhost = vhost.replace("listen 443 ssl;", "listen 10443 ssl;").replace(
        "/etc/letsencrypt/live/voice.example.com/fullchain.pem", str(certificate)
    ).replace(
        "/etc/letsencrypt/live/voice.example.com/privkey.pem", str(key)
    ).replace(
        "/var/log/nginx/claude-voice-error.log", str(tmp_path / "error.log")
    )
    conf_dir = tmp_path / "conf.d"
    conf_dir.mkdir()
    (conf_dir / "claude-voice.conf").write_text(vhost)
    nginx_conf = tmp_path / "nginx.conf"
    nginx_conf.write_text(
        f"pid {tmp_path / 'nginx.pid'};\n"
        "events {}\n"
        f"http {{ include {conf_dir / '*.conf'}; }}\n"
    )
    subprocess.run(
        [nginx, "-t", "-p", str(tmp_path), "-c", str(nginx_conf)],
        check=True,
        capture_output=True,
    )


def test_public_installer_has_only_explicit_safe_modes():
    script = read("scripts/install-public-access.sh")
    assert "set -euo pipefail" in script
    for mode in (
        "--preflight",
        "--install-ddns",
        "--issue-staging",
        "--issue-production",
        "--install-nginx",
    ):
        assert mode in script
    forbidden = (
        "secretid",
        "secretkey",
        "router password",
        "apt-get",
        "ufw disable",
        "dmz",
        "listen 80",
        "tencent vps",
    )
    lowered = script.lower()
    for value in forbidden:
        assert value not in lowered
    assert "nginx certbot dnsutils" in script


def test_public_installer_validates_nginx_before_reload():
    script = read("scripts/install-public-access.sh")
    reload_function = script[script.index("reload_nginx()") :]
    reload_function = reload_function[: reload_function.index("}\n")]
    assert reload_function.index("nginx -t") < reload_function.index(
        "systemctl reload nginx"
    )


def test_production_issuance_removes_staging_renewal_lineage():
    script = read("scripts/install-public-access.sh")
    production = script[script.index("issue_production()") :]
    production = production[: production.index("\n}\n")]
    certificate_check = 'openssl x509 -checkend 1209600 -noout -in "$certificate"'
    staging_delete = (
        'certbot delete --cert-name "$PUBLIC_DOMAIN-staging" --non-interactive'
    )
    assert certificate_check in production
    assert staging_delete in production
    assert production.index(certificate_check) < production.index(staging_delete)


def test_preflight_checks_loopback_gateway_with_certificate_hostname():
    script = read("scripts/install-public-access.sh")
    assert 'voice_hostname="$(hostname)"' in script
    assert "--noproxy '*'" in script
    assert '--resolve "$voice_hostname:8443:127.0.0.1"' in script
    assert "https://127.0.0.1:8443/health/ready" not in script


def test_public_interface_has_no_lan_only_connection_advice():
    socket = read("frontend/src/voice/socket.ts")
    app = read("frontend/src/App.tsx")
    assert "检查局域网" not in socket
    assert "局域网已连接" not in app
    assert "检查网络和服务状态后重试" in socket
    assert "语音服务已连接" in app


def test_runbook_contains_ordered_install_and_rollback_sequence():
    runbook = read("docs/public-access-runbook.md")
    commands = [
        "./scripts/configure-dnspod-credentials.sh",
        "./scripts/install-public-access.sh --preflight",
        "./scripts/install-public-access.sh --install-ddns",
        "sudo ./scripts/install-public-access.sh --issue-staging",
        "sudo ./scripts/install-public-access.sh --issue-production",
        "sudo ./scripts/install-public-access.sh --install-nginx",
    ]
    positions = [runbook.index(command) for command in commands]
    assert positions == sorted(positions)
    for required in (
        "systemctl --user status claude-voice-ddns.timer",
        "journalctl --user -u claude-voice-ddns.service",
        "systemctl status nginx certbot.timer",
        "certbot renew --dry-run",
        "先关闭两级路由器上的 TCP 443 端口映射",
    ):
        assert required in runbook


def test_readme_describes_local_stack_without_a_live_public_endpoint():
    readme = read("README.md")
    assert "voice.example.com" in readme
    assert "没有在线演示" in readme
    assert "docs/public-access-runbook.md" in readme
    assert "127.0.0.1:8060" in readme
    assert "Qwen3-TTS" in readme
    assert "局域网" in readme
