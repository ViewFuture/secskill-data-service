#!/usr/bin/env bash
# 校验并确保钉死 commit 的 liepin-cli 已安装（Render / 本地）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REQ_FILE="requirements.txt"
PIN_FILE="providers/liepin/cli_pin.py"

EXPECTED="$(python - <<'PY'
from providers.liepin.cli_pin import LIEPIN_CLI_PINNED_COMMIT
print(LIEPIN_CLI_PINNED_COMMIT)
PY
)"

if [[ ! -f "$REQ_FILE" ]]; then
  echo "missing $REQ_FILE" >&2
  exit 1
fi

if ! grep -q "liepin-cli @ git+https://github.com/liepin-tech-2026/liepin-cil.git@${EXPECTED}" "$REQ_FILE"; then
  echo "requirements.txt missing pinned liepin-cli @ $EXPECTED" >&2
  exit 1
fi

if ! grep -q "$EXPECTED" "$PIN_FILE"; then
  echo "cli_pin.py missing expected commit $EXPECTED" >&2
  exit 1
fi

echo "Ensuring liepin-cli @ $EXPECTED"
pip install "liepin-cli @ git+https://github.com/liepin-tech-2026/liepin-cil.git@${EXPECTED}"
python -c "import liepin_cli"
python -m liepin_cli.main --help >/dev/null
echo "liepin_cli module OK @ $EXPECTED"
