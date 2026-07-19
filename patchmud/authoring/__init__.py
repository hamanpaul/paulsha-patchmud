"""結構化出題模組：把已解決的 closed bug 凍結成 deck 關卡。

- :class:`SourceSpec` / :func:`load_source`：出題輸入契約與載入。
- :func:`build_encounter`：寫出 deck 檔並經品質閘驗證後凍結。
"""

from __future__ import annotations

from patchmud.authoring.builder import build_encounter
from patchmud.authoring.loader import load_source
from patchmud.authoring.model import AuthoringError, SourceSpec

__all__ = ["SourceSpec", "AuthoringError", "load_source", "build_encounter"]
