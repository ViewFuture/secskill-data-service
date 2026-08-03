"""猎聘 CLI 上游钉死版本（独立 .liepin-venv；与主项目依赖隔离）。

安装：
  bash build.sh

上游：
  https://github.com/liepin-tech-2026/liepin-cil
"""

from __future__ import annotations

LIEPIN_CLI_GIT_URL = "https://github.com/liepin-tech-2026/liepin-cil.git"
# 由 git ls-remote refs/heads/main 得到的完整 SHA（不得编造）。
LIEPIN_CLI_PINNED_COMMIT = "858a62bd839d490e8745b7503961e4676a54b9d7"
LIEPIN_CLI_REF = LIEPIN_CLI_PINNED_COMMIT
LIEPIN_CLI_VERSION_NOT_PINNED = False

LIEPIN_CLI_PIP_URL = f"git+{LIEPIN_CLI_GIT_URL}@{LIEPIN_CLI_PINNED_COMMIT}"

DEFAULT_LIEPIN_PYTHON_EXECUTABLE = ".liepin-venv/bin/python"
