#!/bin/sh
# Build the site from data/ and open it in your browser. Ctrl+C stops it.
set -e
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it from https://docs.astral.sh/uv/ and try again." >&2
  exit 1
fi
exec uv run who-is-running serve "$@"
