#!/bin/sh
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
# uv is needed only while checking/installing the environment. Do not keep a uv
# runner (and its package-cache lock) alive for the duration of the MCP session.
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --locked --no-dev --quiet >&2
exec .venv/bin/python -m endnote_mcp.shared "$@"
