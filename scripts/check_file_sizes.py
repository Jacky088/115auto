"""单文件行数红线守护脚本：防止 backend/ 与 web/src/ 再次出现巨型单体文件。

规则：
- 所有新建或常规 `.py` / `.vue` / `.js` 文件默认上限为 800 行；
- 历史遗留超过 800 行的文件按当前基线锁定上限（只允许小幅 bugfix 缓冲，禁止继续膨胀）；
- 任一文件超限即以非零退出码阻断 CI，引导按 AGENTS.md 拆分模块或组件。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_MAX_LINES = 800
WARN_LINES = 600

# 历史文件只减不增基线（当前行数 + 30 行缺陷修复缓冲；重构缩减后应同步下调或移除）
LEGACY_CAPS: dict[str, int] = {
    "backend/core/media_parser.py": 840,
    "backend/database.py": 1220,
    "backend/pan115/auto_move.py": 900,
    "backend/pan115/client.py": 1020,
    "backend/worker/bot_commands.py": 1360,
    "backend/worker/media_worker/scrape.py": 900,
    "backend/worker/queue_worker.py": 1040,
    "web/src/components/dirs/AutoMovePanel.vue": 1020,
    "web/src/views/Files.vue": 1320,
    "web/src/views/Settings.vue": 1460,
}

SCAN_GLOBS = (
    "backend/**/*.py",
    "web/src/**/*.vue",
    "web/src/**/*.js",
)


def main() -> int:
    show_warn = "--warn" in sys.argv
    violations: list[tuple[str, int, int]] = []
    warnings: list[tuple[str, int]] = []
    checked = 0

    for pattern in SCAN_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if "static" in path.parts or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            checked += 1
            cap = LEGACY_CAPS.get(rel, DEFAULT_MAX_LINES)
            if line_count > cap:
                violations.append((rel, line_count, cap))
            elif show_warn and rel not in LEGACY_CAPS and line_count > WARN_LINES:
                warnings.append((rel, line_count))

    if warnings:
        print(f"提示：以下常规文件已超过 {WARN_LINES} 行，新增功能前建议规划拆分：")
        for rel, n in warnings:
            print(f"  [WARN] {rel}: {n} 行（上限 {DEFAULT_MAX_LINES}）")

    if violations:
        print("文件行数红线校验失败！请按 AGENTS.md 拆分为子模块或子组件：")
        for rel, n, cap in violations:
            print(f"  [FAIL] {rel}: {n} 行 > 上限 {cap} 行")
        return 1

    print(f"文件行数红线校验通过：共检查 {checked} 个源码文件，0 项超限（常规上限 {DEFAULT_MAX_LINES} 行）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
