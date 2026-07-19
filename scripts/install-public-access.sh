#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/agentvoice/agent-voice-web"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
CREDENTIAL_FILE="/home/agentvoice/.config/claude-voice/dnspod.env"
VOICE_CA="/home/agentvoice/.config/claude-voice/certs/ca.crt"
PUBLIC_DOMAIN="voice.example.com"
PUBLIC_PACKAGES="nginx certbot dnsutils"

usage() {
    printf 'Usage: %s {--preflight|--install-ddns|--issue-staging|--issue-production|--install-nginx}\n' "$0" >&2
    exit 2
}

require_user() {
    if (( EUID == 0 )); then
        printf 'This mode must run as a regular user, without sudo.\n' >&2
        exit 1
    fi
}

require_root() {
    if (( EUID != 0 )); then
        printf 'This mode requires sudo.\n' >&2
        exit 1
    fi
}

require_package_command() {
    local command_name="$1"
    local package_name="$2"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Missing required package: %s (expected command %s).\n' "$package_name" "$command_name" >&2
        printf 'Required public-entry packages: %s\n' "$PUBLIC_PACKAGES" >&2
        exit 1
    fi
}

load_credentials() {
    if [[ ! -f "$CREDENTIAL_FILE" ]]; then
        printf 'Credential file is missing; run configure-dnspod-credentials.sh first.\n' >&2
        exit 1
    fi
    if [[ "$(stat -c '%a' "$CREDENTIAL_FILE")" != "600" ]]; then
        printf 'Credential file must have mode 600.\n' >&2
        exit 1
    fi
    set -a
    source "$CREDENTIAL_FILE"
    set +a
}

validate_dns_access() {
    load_credentials
    "$PYTHON" -m public_access.cli validate \
        --domain example.com --domain-id 12345678
}

verify_single_san() {
    local certificate="$1"
    local san_output
    local -a names
    san_output="$(openssl x509 -in "$certificate" -noout -ext subjectAltName)"
    mapfile -t names < <(grep -oE 'DNS:[^,[:space:]]+' <<<"$san_output")
    if (( ${#names[@]} != 1 )) || [[ "${names[0]}" != "DNS:$PUBLIC_DOMAIN" ]]; then
        printf 'Certificate SAN must contain only %s.\n' "$PUBLIC_DOMAIN" >&2
        exit 1
    fi
}

acme_email() {
    if [[ -z "${PUBLIC_ACCESS_ACME_EMAIL:-}" ]]; then
        read -r -p 'ACME email: ' PUBLIC_ACCESS_ACME_EMAIL
    fi
    if [[ "$PUBLIC_ACCESS_ACME_EMAIL" != *@*.* ]]; then
        printf 'A valid ACME email is required.\n' >&2
        exit 1
    fi
    export PUBLIC_ACCESS_ACME_EMAIL
}

reload_nginx() {
    nginx -t
    systemctl reload nginx
}

preflight() {
    require_user
    validate_dns_access
    local voice_hostname
    voice_hostname="$(hostname)"
    curl --noproxy '*' --fail --silent --show-error --cacert "$VOICE_CA" \
        --resolve "$voice_hostname:8443:127.0.0.1" \
        "https://$voice_hostname:8443/health/ready" >/dev/null
    curl --noproxy '*' --fail --silent --show-error \
        http://127.0.0.1:8060/health >/dev/null
    curl --noproxy '*' --fail --silent --show-error \
        http://127.0.0.1:8766/health >/dev/null

    if ! ss -H -lnt | awk '{print $4}' | grep -qx '0.0.0.0:8443'; then
        printf 'The voice gateway is not listening on 0.0.0.0:8443.\n' >&2
        exit 1
    fi
    for port in 8060 8766; do
        if ! ss -H -lnt | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
            printf 'Required local service is not listening on TCP %s.\n' "$port" >&2
            exit 1
        fi
    done
    if [[ ! -f /etc/nginx/conf.d/claude-voice.conf ]] && \
        ss -H -lnt | awk '{print $4}' | grep -Eq '(^|:)443$'; then
        printf 'TCP 443 is already owned before the public Nginx entry is installed.\n' >&2
        exit 1
    fi
    printf 'status=preflight-ok\n'
}

install_ddns() {
    require_user
    require_package_command dig dnsutils
    validate_dns_access
    install -d -m 700 "$HOME/.config/systemd/user" "$HOME/.local/state/claude-voice"
    install -m 644 "$PROJECT_ROOT/deploy/claude-voice-ddns.service" \
        "$HOME/.config/systemd/user/claude-voice-ddns.service"
    install -m 644 "$PROJECT_ROOT/deploy/claude-voice-ddns.timer" \
        "$HOME/.config/systemd/user/claude-voice-ddns.timer"
    systemd-analyze --user verify \
        "$HOME/.config/systemd/user/claude-voice-ddns.service" \
        "$HOME/.config/systemd/user/claude-voice-ddns.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now claude-voice-ddns.timer
    systemctl --user start claude-voice-ddns.service

    local public_ip nameserver answer
    public_ip="$("$PYTHON" - <<'PY'
from public_access.ddns import PUBLIC_IP_SOURCES, discover_public_ipv4, fetch_public_ip_source
print(discover_public_ipv4(fetch_public_ip_source, PUBLIC_IP_SOURCES))
PY
)"
    nameserver="$(dig +short NS example.com | head -n 1)"
    if [[ -z "$nameserver" ]]; then
        printf 'No authoritative nameserver was returned.\n' >&2
        exit 1
    fi
    answer="$(dig +short A voice.example.com "@$nameserver" | head -n 1)"
    if [[ "$answer" != "$public_ip" ]]; then
        printf 'Authoritative A record does not match the direct public IPv4 consensus.\n' >&2
        exit 1
    fi
    printf 'status=ddns-installed public_ip=%s\n' "$public_ip"
}

issue_staging() {
    require_root
    require_package_command certbot certbot
    require_package_command dig dnsutils
    acme_email
    certbot certonly --staging --manual --preferred-challenges dns \
        --manual-auth-hook "$PROJECT_ROOT/scripts/certbot-dnspod-auth.sh" \
        --manual-cleanup-hook "$PROJECT_ROOT/scripts/certbot-dnspod-cleanup.sh" \
        --cert-name "$PUBLIC_DOMAIN-staging" -d "$PUBLIC_DOMAIN" \
        --non-interactive --agree-tos -m "$PUBLIC_ACCESS_ACME_EMAIL"
    local certificate="/etc/letsencrypt/live/$PUBLIC_DOMAIN-staging/fullchain.pem"
    verify_single_san "$certificate"
    openssl x509 -checkend 0 -noout -in "$certificate"
    printf 'status=staging-certificate-valid\n'
}

issue_production() {
    require_root
    require_package_command nginx nginx
    require_package_command certbot certbot
    require_package_command dig dnsutils
    local staging="/etc/letsencrypt/live/$PUBLIC_DOMAIN-staging/fullchain.pem"
    if ! openssl x509 -checkend 0 -noout -in "$staging"; then
        printf 'A valid staging certificate is required first.\n' >&2
        exit 1
    fi
    acme_email
    certbot certonly --manual --preferred-challenges dns \
        --manual-auth-hook "$PROJECT_ROOT/scripts/certbot-dnspod-auth.sh" \
        --manual-cleanup-hook "$PROJECT_ROOT/scripts/certbot-dnspod-cleanup.sh" \
        --deploy-hook "$PROJECT_ROOT/scripts/certbot-nginx-deploy.sh" \
        --cert-name "$PUBLIC_DOMAIN" -d "$PUBLIC_DOMAIN" \
        --non-interactive --agree-tos -m "$PUBLIC_ACCESS_ACME_EMAIL"
    local certificate="/etc/letsencrypt/live/$PUBLIC_DOMAIN/fullchain.pem"
    verify_single_san "$certificate"
    openssl x509 -checkend 1209600 -noout -in "$certificate"
    local staging_renewal="/etc/letsencrypt/renewal/$PUBLIC_DOMAIN-staging.conf"
    if [[ -f "$staging_renewal" ]]; then
        certbot delete --cert-name "$PUBLIC_DOMAIN-staging" --non-interactive
    fi
    printf 'status=production-certificate-valid\n'
}

install_nginx() {
    require_root
    require_package_command nginx nginx
    local certificate="/etc/letsencrypt/live/$PUBLIC_DOMAIN/fullchain.pem"
    local default_link="/etc/nginx/sites-enabled/default"
    if ! openssl x509 -checkend 0 -noout -in "$certificate"; then
        printf 'A valid production certificate is required first.\n' >&2
        exit 1
    fi
    install -d -m 755 /etc/nginx/conf.d
    install -m 644 "$PROJECT_ROOT/deploy/nginx/claude-voice.conf" \
        /etc/nginx/conf.d/claude-voice.conf
    if [[ -L "$default_link" ]] && \
        [[ "$(readlink -f "$default_link")" == "/etc/nginx/sites-available/default" ]]; then
        rm -- "$default_link"
    fi
    nginx -t
    systemctl enable --now nginx
    reload_nginx
    local status
    status="$(curl --noproxy '*' --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --resolve "$PUBLIC_DOMAIN:443:127.0.0.1" \
        "https://$PUBLIC_DOMAIN/health/live")"
    if [[ "$status" != "200" ]]; then
        printf 'Local public-entry health check returned HTTP %s.\n' "$status" >&2
        exit 1
    fi
    printf 'status=nginx-installed\n'
}

if (( $# != 1 )); then
    usage
fi

case "$1" in
    --preflight) preflight ;;
    --install-ddns) install_ddns ;;
    --issue-staging) issue_staging ;;
    --issue-production) issue_production ;;
    --install-nginx) install_nginx ;;
    *) usage ;;
esac
