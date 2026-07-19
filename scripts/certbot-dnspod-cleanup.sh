#!/usr/bin/env bash
set -euo pipefail
set -a
source /home/agentvoice/.config/claude-voice/dnspod.env
set +a
exec /home/agentvoice/agent-voice-web/.venv/bin/python -m public_access.cli acme-cleanup
