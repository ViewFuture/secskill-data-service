"""猎聘 CLI 上游钉死版本（发布门禁单一事实来源）。

安装（推荐）：
  pip install -r requirements.txt

对应上游：
  https://github.com/liepin-tech-2026/liepin-cil
"""

from __future__ import annotations

# 全仓唯一允许的 liepin-cli Git commit（完整 SHA）。
LIEPIN_CLI_GIT_URL = "https://github.com/liepin-tech-2026/liepin-cil.git"
LIEPIN_CLI_PINNED_COMMIT = "858a62bd839d490e8745b7503961e4676a54b9d7"

# pip 可安装的 URL（须与 requirements.txt 中的 liepin-cli @ git+ 行一致）。
LIEPIN_CLI_PIP_URL = (
    f"liepin-cli @ git+{LIEPIN_CLI_GIT_URL}@{LIEPIN_CLI_PINNED_COMMIT}"
)
