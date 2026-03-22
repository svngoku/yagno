#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
#  Yagno Docker entrypoint
#
#  Usage patterns:
#    docker run yagno run specs/simple_researcher.yaml -i '"Hello"'
#    docker run yagno serve specs/my_workflow.yaml
#    docker run yagno validate specs/my_workflow.yaml
#    docker run yagno --help
#    docker run --entrypoint bash yagno   # drop into shell
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

# "serve" is a convenience alias: starts uvicorn with the FastAPI app
if [ "${1:-}" = "serve" ]; then
    shift
    SPEC_PATH="${1:?Usage: yagno serve <spec.yaml>}"
    shift
    echo "Starting Yagno API server with spec: ${SPEC_PATH}"
    exec uvicorn yagno.api:create_app \
        --factory \
        --host 0.0.0.0 \
        --port "${PORT:-8000}" \
        "$@"
fi

# Default: forward everything to the yagno CLI
exec yagno "$@"
