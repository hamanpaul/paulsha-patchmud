"""Cortex ``launcher.build_codex_argv``（``cortex-adapter-v1``）commit-required builder 那一列的移植。

execution profile 的 toolchain 條件是 ``{id: codex, version: cortex-adapter-v1}``——
Cortex 以它的 codex adapter 契約發射 codex。PatchMUD 只在 argv **逐 token 等於**
Cortex 對同一組輸入產生的 argv 時才宣告這個 toolchain（CI 以真 Cortex
``build_codex_argv`` 比對，argv[0] 的可執行檔路徑除外）。

這一列的形狀（``allow_unsafe=False``、``commit_required=True``）：

- ``--sandbox danger-full-access``：Cortex ``trust_root.registry.SANDBOX_MODE_DERIVATION``
  的 ``builder-workspace-write`` 列在 direct 與 Trust Root template 兩種邊界都發這個
  mode、不附內層 landlock；``workspace-write`` 契約由**外層**邊界保證（Cortex 是
  systemd unit＋egress proxy，PatchMUD 是 bwrap＋egress allowlist proxy）。
- linked worktree 才需要的 ``--add-dir <git dir>``：PatchMUD 的 workspace 是獨立
  repo（``.git`` 是目錄），Cortex ``_linked_worktree_git_write_dirs`` 對它回空；
  ``.git`` 不是目錄時 fail-closed，不猜。
- effort 一律以 ``-c model_reasoning_effort="<effort>"`` 明示（Cortex
  ``_resolve_reasoning_effort``：明示值或 descriptor 的 model／adapter 預設）。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["CodexArgvError", "build_cortex_builder_codex_argv"]


class CodexArgvError(ValueError):
    """輸入不屬於 commit-required builder 這一列（fail-closed）。"""


def build_cortex_builder_codex_argv(
    *,
    codex: str,
    prompt: str,
    model: str,
    effort: str,
    last_message_path: str,
    worktree: str,
    host_worktree: Path | None = None,
) -> list[str]:
    """回傳與 Cortex ``build_codex_argv(commit_required=True, ...)`` 相同的 argv。

    ``worktree`` 是 codex 看見的路徑（沙箱內路徑）；``host_worktree`` 給定時檢查
    ``.git`` 是目錄（非 linked worktree），否則 Cortex 會多發 ``--add-dir``。
    """
    if not codex or not prompt or not model or not effort:
        raise CodexArgvError("codex／prompt／model／effort 都必須給定")
    if not worktree.startswith("/"):
        raise CodexArgvError("worktree 必須是絕對路徑")
    if host_worktree is not None:
        marker = Path(host_worktree) / ".git"
        if marker.is_symlink() or not marker.is_dir():
            raise CodexArgvError("builder workspace 的 .git 必須是一般目錄（非 linked worktree）")
    return [
        codex,
        "exec",
        "--ignore-user-config",
        prompt,
        "--json",
        "--sandbox",
        "danger-full-access",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-o",
        last_message_path,
        "-C",
        worktree,
    ]
