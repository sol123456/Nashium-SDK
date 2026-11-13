#!/usr/bin/env bash
# One-time setup for a machine that will run Nashium match workers.
#
#   ./scripts/setup.sh
#
# Then, every time you want to work:
#   source .venv/bin/activate
#   nashium farm --workers 6
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: $PYTHON not found. Install Python 3.10+ first." >&2
    exit 1
fi

echo "==> creating virtual environment (.venv)"
[ -d .venv ] || "$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> installing the SDK (editable, with worker extras)"
python -m pip install --upgrade pip >/dev/null
python -m pip install -e ".[worker]"

echo
echo "==> checking docker and building the runner image"
# Non-fatal: --check also reports a missing token, which is expected here.
python -m nashium farm --build-image --check || true

cat <<'EOF'

Next step (once only, stores your token and seed secret):
  nashium farm --save-config --token "YOUR_TOKEN" --seed-secret "YOUR_SECRET"

Then:
  nashium farm --workers 6
EOF
