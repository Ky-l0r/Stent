"""工程元数据一致性校验（CI 与本地都能跑）。

守住三件容易「各改一处」的事：

1. **版本号单一来源**——``stent/__init__.py`` 是真源，``pyproject.toml`` 通过
   ``dynamic`` 读它，版本号不能再写死；
2. **CHANGELOG 与版本号对齐**——发版时两处一起改；
3. **依赖口径一致**——``pyproject.toml`` 的 ``dependencies`` 与
   ``requirements.txt`` 必须声明同一组运行依赖（这两处曾经漂移过：pyproject 里多出
   一个没人直接 import 的 httpx）。

运行：``python tests/check_project.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "requirements.txt"

sys.path.insert(0, str(ROOT))

from stent import __version__  # noqa: E402  版本真源，必须在 sys.path 之后导入

_VERSION_RE = re.compile(r"^##\s*v?(\d+\.\d+\.\d+)", re.MULTILINE)
_REQ_RE = re.compile(r"""^\s*["']?([A-Za-z0-9._-]+)""")


def _section(text: str, name: str) -> str:
    """取出某个 TOML 表（``[name]``）的内容，找不到返回空串。"""
    match = re.search(rf"^\[{re.escape(name)}\]$(.*?)(?=^\[|\Z)", text, re.S | re.MULTILINE)
    return match.group(1) if match else ""


def _dep_names(lines: list[str], *, strip_inline_comment: bool) -> set[str]:
    """从依赖行里取出包名（忽略注释、空行、`-r` 引用与 `#` 说明）。"""
    names: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if strip_inline_comment:
            line = line.split("#", 1)[0].strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = _REQ_RE.match(line)
        if match:
            names.add(match.group(1).lower())
    return names


def _pyproject_dependencies(text: str) -> set[str]:
    block = re.search(r"^dependencies = \[(.*?)^\]", text, re.S | re.MULTILINE)
    if block is None:
        return set()
    return _dep_names(block.group(1).splitlines(), strip_inline_comment=True)


def main() -> int:
    problems: list[str] = []
    pyproject_text = PYPROJECT.read_text(encoding="utf-8")
    project_table = _section(pyproject_text, "project")

    # ---- 1. 版本号单一来源 -------------------------------------------------
    if 'dynamic = ["version"]' not in pyproject_text:
        problems.append('pyproject.toml 应使用 dynamic = ["version"]，不要写死版本号')
    if re.search(r"^version\s*=", project_table, re.MULTILINE):
        problems.append(
            "pyproject.toml 的 [project] 里出现了硬编码 version，与 stent.__version__ 可能漂移"
        )

    # ---- 2. CHANGELOG 顶部版本 --------------------------------------------
    changelog_text = CHANGELOG.read_text(encoding="utf-8")
    match = _VERSION_RE.search(changelog_text)
    if match is None:
        problems.append("CHANGELOG.md 顶部没有找到 `## vX.Y.Z` 形式的版本标题")
    elif match.group(1) != __version__:
        problems.append(
            f"CHANGELOG 顶部是 v{match.group(1)}，而 stent.__version__ 是 v{__version__}"
            "——发版时两处要一起改"
        )

    # ---- 3. 依赖口径 -------------------------------------------------------
    declared = _pyproject_dependencies(pyproject_text)
    listed = _dep_names(
        REQUIREMENTS.read_text(encoding="utf-8").splitlines(), strip_inline_comment=True
    )
    if not declared:
        problems.append("没能从 pyproject.toml 的 dependencies 里解析出依赖")
    elif declared != listed:
        for name in sorted(declared - listed):
            problems.append(f"pyproject.toml 声明了 {name}，requirements.txt 里没有")
        for name in sorted(listed - declared):
            problems.append(f"requirements.txt 声明了 {name}，pyproject.toml 里没有")

    if problems:
        for item in problems:
            print(f"[FAIL] {item}")
        return 1

    print(f"[OK] 版本 v{__version__} 与 CHANGELOG、依赖声明三处一致（{len(declared)} 个运行依赖）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
