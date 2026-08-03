#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

echo "==> Python runtime"
python --version
python -m pip --version

echo "==> Installing secskill-data-service dependencies"
python -m pip install \
  --no-cache-dir \
  -r requirements.txt

echo "==> Creating isolated Liepin CLI environment"
rm -rf .liepin-venv
python -m venv .liepin-venv

echo "==> Installing Liepin CLI dependencies"
.liepin-venv/bin/python -m pip install \
  --no-cache-dir \
  -r requirements-liepin.txt

echo "==> Verifying Liepin CLI package"
.liepin-venv/bin/python - <<'PY'
import sys
import liepin_cli

print("Liepin Python:", sys.executable)
print("Liepin package:", liepin_cli.__file__)
PY

echo "==> Verifying Liepin CLI module entry"
.liepin-venv/bin/python \
  -m liepin_cli.main \
  --help \
  >/dev/null

echo "==> Liepin CLI isolated environment ready"
echo "==> Build completed successfully"
