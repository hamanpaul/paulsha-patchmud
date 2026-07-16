"""patchmud CLI 進入點。

子命令（validate-deck / score-diff / run / pilot / replay / report）依
docs/superpowers/plans/2026-07-16-patchmud-mvp.md 逐 task 落地。
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(f"patchmud: 子命令尚未實作：{args[0]}", file=sys.stderr)
        return 2
    print("patchmud 0.0.0 — 尚無子命令；見 docs/superpowers/plans/2026-07-16-patchmud-mvp.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
