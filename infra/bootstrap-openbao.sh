#!/usr/bin/env bash
# Bootstrap OpenBao Transit keys + RBAC policies for Blindfold (dev/test/CI).
#
# Transit keys (HITL decision, issue #10):
#   blindfold-mapping       aes256-gcm96  encrypt/decrypt real values
#   blindfold-blind-index   aes256-gcm96  deterministic HMAC for blind index
#
# RBAC policies + identities (issue #10 HITL):
#   blindfold-proxy   encrypt + decrypt + hmac (inline restore path + seed ETL)
#   blindfold-human   decrypt + hmac           (re-identify; workspace-scoping app-enforced)
#   blindfold-admin   rotate + rewrap + key config (operator-only rotation; cannot decrypt)
#
# Privilege separation: proxy can't rotate; operator can't decrypt; human can't encrypt/rotate.
# The app gives per-workspace re-identify gating (ADR-0015 + RbacRegistry + workspace tags).
#
# Usage: VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=dev-root-token ./bootstrap-openbao.sh
#   or:  BAO_ADDR=http://localhost:8200 BAO_TOKEN=dev-root-token ./bootstrap-openbao.sh
#
# Requires the `bao` (OpenBao) or `vault` CLI on PATH.

set -euo pipefail

BAO="${BAO_CMD:-bao}"
if ! command -v "$BAO" &>/dev/null; then
    BAO="vault"
fi

ADDR="${VAULT_ADDR:-${BAO_ADDR:-http://localhost:8200}}"
TOKEN="${VAULT_TOKEN:-${BAO_TOKEN:-dev-root-token}}"

export VAULT_ADDR="$ADDR"
export VAULT_TOKEN="$TOKEN"

echo "Bootstrapping OpenBao at $ADDR"

# Enable the Transit secrets engine if not already enabled.
if ! "$BAO" secrets list | grep -q '^transit/'; then
    "$BAO" secrets enable transit
    echo "Transit engine enabled."
else
    echo "Transit engine already enabled."
fi

# Create Transit keys (idempotent: -force skips if already exists).
for key in blindfold-mapping blindfold-blind-index; do
    if "$BAO" read "transit/keys/$key" &>/dev/null; then
        echo "Key $key already exists."
    else
        "$BAO" write -f "transit/keys/$key" type=aes256-gcm96
        echo "Key $key created."
    fi
done

# Write RBAC policies.
"$BAO" policy write blindfold-proxy - <<'EOF'
# blindfold-proxy: inline restore path + seed ETL.
# encrypt + decrypt + hmac on both Transit keys; no rotation rights.
path "transit/encrypt/blindfold-mapping" { capabilities = ["update"] }
path "transit/decrypt/blindfold-mapping" { capabilities = ["update"] }
path "transit/hmac/blindfold-blind-index/*" { capabilities = ["update"] }
EOF
echo "Policy blindfold-proxy written."

"$BAO" policy write blindfold-human - <<'EOF'
# blindfold-human: re-identify right (decrypt) + blind-index HMAC.
# No encrypt or rotate rights; workspace-scoping enforced by the app (ADR-0015).
path "transit/decrypt/blindfold-mapping" { capabilities = ["update"] }
path "transit/hmac/blindfold-blind-index/*" { capabilities = ["update"] }
EOF
echo "Policy blindfold-human written."

"$BAO" policy write blindfold-admin - <<'EOF'
# blindfold-admin: key rotation + rewrap + config. Cannot decrypt.
path "transit/keys/blindfold-mapping" { capabilities = ["read", "update"] }
path "transit/keys/blindfold-blind-index" { capabilities = ["read", "update"] }
path "transit/rewrap/blindfold-mapping" { capabilities = ["update"] }
path "transit/rotate/blindfold-mapping" { capabilities = ["update"] }
path "transit/rotate/blindfold-blind-index" { capabilities = ["update"] }
EOF
echo "Policy blindfold-admin written."

echo "Bootstrap complete."
