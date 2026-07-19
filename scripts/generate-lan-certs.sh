#!/usr/bin/env bash
set -euo pipefail
umask 077

CERT_DIR="${VOICE_CERT_DIR:-$HOME/.config/claude-voice/certs}"
LAN_IP="${VOICE_LAN_IP:-192.0.2.10}"
WG_IP="${VOICE_WG_IP:-10.0.0.2}"
LAN_HOSTNAME="${VOICE_LAN_HOSTNAME:-$(hostname -s)}"
CA_KEY="$CERT_DIR/ca.key"
CA_CERT="$CERT_DIR/ca.crt"
SERVER_KEY="$CERT_DIR/server.key"
SERVER_CSR="$CERT_DIR/server.csr"
SERVER_CERT="$CERT_DIR/server.crt"

mkdir -p "$CERT_DIR"

if [[ ! -s "$CA_KEY" || ! -s "$CA_CERT" ]]; then
  openssl genrsa -out "$CA_KEY" 4096
  openssl req -x509 -new -sha256 -days 3650 -key "$CA_KEY" -out "$CA_CERT" \
    -subj "/CN=Claude Voice Local CA/O=Claude Voice LAN"
fi

EXT_FILE="$(mktemp)"
trap 'rm -f "$EXT_FILE" "$SERVER_CSR"' EXIT
printf '%s\n' \
  'basicConstraints = CA:FALSE' \
  'keyUsage = digitalSignature, keyEncipherment' \
  'extendedKeyUsage = serverAuth' \
  'subjectAltName = @alt_names' \
  '' \
  '[alt_names]' \
  "IP.1 = $LAN_IP" \
  "IP.2 = $WG_IP" \
  "DNS.1 = $LAN_HOSTNAME" \
  "DNS.2 = $LAN_HOSTNAME.local" >"$EXT_FILE"

if [[ ! -s "$SERVER_KEY" || ! -s "$SERVER_CERT" || "${1:-}" == "--force" ]]; then
  openssl genrsa -out "$SERVER_KEY" 3072
  openssl req -new -sha256 -key "$SERVER_KEY" -out "$SERVER_CSR" \
    -subj "/CN=$LAN_HOSTNAME.local/O=Claude Voice LAN"
  openssl x509 -req -sha256 -days 825 -in "$SERVER_CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$SERVER_CERT" -extfile "$EXT_FILE"
fi

chmod 600 "$CA_KEY" "$SERVER_KEY"
chmod 644 "$CA_CERT" "$SERVER_CERT"
openssl verify -CAfile "$CA_CERT" "$SERVER_CERT"
echo "Certificates are ready in $CERT_DIR"
