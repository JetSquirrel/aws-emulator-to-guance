#!/usr/bin/env bash
# Run any command against the local AWS Emulator, e.g. ./run.sh python demo.py / ./run.sh pytest
# Loads the connect variables in this process only; your shell is left alone.
set -euo pipefail
PORT="${EMULATOR_PORT:-9297}"
ENV_FILE="$HOME/.aws/emulator/$PORT.env"
[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE — is the emulator set up?" >&2; exit 1; }
. "$ENV_FILE"
curl -sf --cacert "$AWS_CA_BUNDLE" "$AWS_ENDPOINT_URL/__admin/health" >/dev/null \
  || { echo "emulator not answering on $AWS_ENDPOINT_URL — start the container first" >&2; exit 1; }
exec uv run "$@"
