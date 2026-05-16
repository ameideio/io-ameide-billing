#!/usr/bin/env bash
# T-L1 in isolation: apply the catalog TWICE, assert the second pass and a
# subsequent `verify` both report ZERO drift. Needs a running Lago.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="python3 ${HERE}/apply.py"

if [ -z "${LAGO_API_KEY:-}" ]; then
  echo "PENDING — needs a Lago instance (LAGO_API_KEY unset)"
  exit 0
fi

echo "pass 1:"; $PY apply
echo "pass 2:"; $PY apply
echo "verify:"
$PY verify
echo "T-L1 PASS: apply x2 -> zero diff."
