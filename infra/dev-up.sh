#!/usr/bin/env bash
# Bring the Blindfold dev stack up from cold, and put it in a state `blindfold serve`
# will actually start against.
#
# `podman compose up -d` alone is NOT enough, and the gap is not obvious:
#
#   * openbao runs `-dev`, so its Transit keys live in memory only. Every `compose down`
#     destroys `blindfold-mapping` AND every token minted against it -- so the token in
#     .env is dead, and any surviving ciphertext is undecryptable (see the no-volume note
#     in docker-compose.dev.yml).
#   * `blindfold serve` refuses an empty database rather than migrating it, so a fresh
#     postgres needs migrations.sql applied before the proxy will boot.
#
# This script does all of it, idempotently: compose up -> wait healthy -> bootstrap
# Transit keys + policies -> mint a proxy token into .env -> apply the schema.
#
# Usage:  infra/dev-up.sh
# Safe to re-run: every step is idempotent, and re-running after `compose down` is the
# supported recovery path.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/infra/docker-compose.dev.yml"
ENV_FILE="$REPO_ROOT/.env"
MIGRATIONS="$REPO_ROOT/src/blindfold/store/migrations.sql"

# Podman first (ADR-0058 -- Sandcastle already runs on it), docker as a fallback.
if command -v podman &>/dev/null; then
    CONTAINER_CMD=podman
elif command -v docker &>/dev/null; then
    CONTAINER_CMD=docker
else
    echo "error: neither podman nor docker is on PATH" >&2
    exit 1
fi

compose() { "$CONTAINER_CMD" compose -f "$COMPOSE_FILE" "$@"; }

# Container names are compose-generated from the `infra` project directory.
OPENBAO_CTR=infra-openbao-1
POSTGRES_CTR=infra-postgres-1

echo "==> Starting the dev stack ($CONTAINER_CMD)"
compose up -d

# Wait on the services' own healthchecks rather than a fixed sleep -- openbao answers
# before it is unsealed, and postgres accepts TCP before it accepts queries.
wait_healthy() {
    local ctr="$1" probe="$2" waited=0
    echo -n "==> Waiting for $ctr "
    until eval "$probe" &>/dev/null; do
        if [ "$waited" -ge 120 ]; then
            echo " timed out after ${waited}s"
            echo "error: $ctr never became ready; check \`$CONTAINER_CMD logs $ctr\`" >&2
            exit 1
        fi
        echo -n "."
        sleep 2
        waited=$((waited + 2))
    done
    echo " ready"
}

wait_healthy "$OPENBAO_CTR" \
    "$CONTAINER_CMD exec $OPENBAO_CTR bao status -address=http://127.0.0.1:8200"
wait_healthy "$POSTGRES_CTR" \
    "$CONTAINER_CMD exec $POSTGRES_CTR pg_isready -U blindfold -d blindfold"

# The bootstrap script needs the `bao` CLI, which is in the container and (usually) not
# on the host -- and the image has no bash, so it is piped to `sh`.
echo "==> Bootstrapping Transit keys + RBAC policies"
"$CONTAINER_CMD" exec -i \
    -e VAULT_ADDR=http://127.0.0.1:8200 \
    -e VAULT_TOKEN=dev-root-token \
    "$OPENBAO_CTR" sh -s < "$REPO_ROOT/infra/bootstrap-openbao.sh" \
    | sed 's/^/    /'

# `blindfold serve` refuses a root token (ADR-0021), so mint one carrying the
# least-privilege proxy policy and write it into .env.
echo "==> Minting a blindfold-proxy token"
PROXY_TOKEN="$("$CONTAINER_CMD" exec \
    -e VAULT_ADDR=http://127.0.0.1:8200 \
    -e VAULT_TOKEN=dev-root-token \
    "$OPENBAO_CTR" bao token create -policy=blindfold-proxy -ttl=720h -field=token)"

if [ -z "$PROXY_TOKEN" ]; then
    echo "error: minting the proxy token produced no output; .env left untouched" >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "    no .env found -- set BLINDFOLD_OPENBAO_TOKEN=$PROXY_TOKEN yourself"
elif grep -q '^BLINDFOLD_OPENBAO_TOKEN=' "$ENV_FILE"; then
    # In place, preserving every other line and the file's own ordering.
    PROXY_TOKEN="$PROXY_TOKEN" python3 - "$ENV_FILE" <<'PY'
import os, pathlib, re, sys
path = pathlib.Path(sys.argv[1])
path.write_text(
    re.sub(
        r"^BLINDFOLD_OPENBAO_TOKEN=.*$",
        "BLINDFOLD_OPENBAO_TOKEN=" + os.environ["PROXY_TOKEN"],
        path.read_text(),
        flags=re.M,
    )
)
PY
    echo "    BLINDFOLD_OPENBAO_TOKEN updated in .env"
else
    printf '\nBLINDFOLD_OPENBAO_TOKEN=%s\n' "$PROXY_TOKEN" >> "$ENV_FILE"
    echo "    BLINDFOLD_OPENBAO_TOKEN appended to .env"
fi

# Idempotent DDL (every statement is CREATE ... IF NOT EXISTS), so this is safe on an
# already-migrated database as well as a fresh one.
echo "==> Applying the store schema"
"$CONTAINER_CMD" exec -i "$POSTGRES_CTR" \
    psql -U blindfold -d blindfold -q -v ON_ERROR_STOP=1 < "$MIGRATIONS"
TABLE_COUNT="$("$CONTAINER_CMD" exec "$POSTGRES_CTR" \
    psql -U blindfold -d blindfold -tAc \
    "SELECT count(*) FROM pg_tables WHERE schemaname='public'")"
echo "    $TABLE_COUNT tables present"

# The frozen proxy inside BlindfoldMenuBar.app does not bundle the `blindfold[gliner]`
# extra, so a `gliner` provider fail-closes every request there (it works fine for a
# source run with `--extra gliner`). Warn rather than rewrite: which value is correct
# depends on how the proxy is being launched.
if [ -f "$ENV_FILE" ] && grep -q '^BLINDFOLD_L3_PROVIDER=gliner' "$ENV_FILE"; then
    echo
    echo "note: BLINDFOLD_L3_PROVIDER=gliner works for a source run"
    echo "      (uv run --extra gliner), but the frozen proxy in the .app does not"
    echo "      bundle that extra and will fail-closed on every request. Use"
    echo "      BLINDFOLD_L3_PROVIDER=omlx against the .app binary."
fi

echo
echo "Dev stack ready."
