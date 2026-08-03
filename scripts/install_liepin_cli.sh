#!/usr/bin/env bash
# 兼容入口：转发到根目录 build.sh（独立 .liepin-venv）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/build.sh"
