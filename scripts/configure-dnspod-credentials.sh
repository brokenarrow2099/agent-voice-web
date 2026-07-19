#!/usr/bin/env bash
set -euo pipefail
umask 077

config_dir="$HOME/.config/claude-voice"
target_env="$config_dir/dnspod.env"
install -d -m 700 "$config_dir"
temporary_env="$(mktemp "$config_dir/dnspod.env.XXXXXX")"
trap 'rm -f "$temporary_env"' EXIT

read -r -s -p 'DNSPod SecretId: ' secret_id
printf '\n'
read -r -s -p 'DNSPod SecretKey: ' secret_key
printf '\n'

if [[ ! "$secret_id" =~ ^AKID[[:alnum:]]+$ ]]; then
    printf 'SecretId must be a normalized CAM value beginning with AKID.\n' >&2
    exit 1
fi
if [[ ! "$secret_key" =~ ^[[:alnum:]]+$ ]]; then
    printf 'SecretKey must be a normalized alphanumeric CAM value.\n' >&2
    exit 1
fi

{
    printf 'DNSPOD_SECRET_ID=%s\n' "$secret_id"
    printf 'DNSPOD_SECRET_KEY=%s\n' "$secret_key"
    printf 'DNSPOD_DOMAIN=example.com\n'
    printf 'DNSPOD_DOMAIN_ID=12345678\n'
    printf 'DNSPOD_SUBDOMAIN=voice\n'
} >"$temporary_env"

install -m 600 "$temporary_env" "$target_env"
chmod 600 "$target_env"

set -a
source "$target_env"
set +a
secret_id=
secret_key=
unset secret_id secret_key

/home/agentvoice/agent-voice-web/.venv/bin/python -m public_access.cli validate \
    --domain example.com --domain-id 12345678
