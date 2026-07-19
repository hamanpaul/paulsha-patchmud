"""Authoring domain model：SourceSpec（出題輸入契約）。

一個「已解決的 closed bug」以結構化 source.yaml 描述，經 :func:`load_source`
驗證成不可變的 SourceSpec，再由 builder 凍結成 deck 關卡。
"""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_SCHEMA_VERSION = 1


class AuthoringError(ValueError):
    """出題輸入契約違反：缺欄位、路徑邊界、schema 版本等（fail-closed）。"""


@dataclass(frozen=True)
class SourceSpec:
    """source.yaml 的完整契約（見設計 spec Part B）。

    集合欄位以 tuple 落地維持不可變；``buggy_repo`` / ``public_tests`` /
    ``hidden_tests`` / ``requirements`` 為 (key, value) 有序 tuple。
    """

    schema_version: int
    issue_id: str
    archetype: str
    difficulty: str
    summary: str
    origin_source: str
    origin_fixed_at: str | None
    allowed_paths: tuple[str, ...]
    expected_paths: tuple[str, ...]
    #: repo/ 底下的待修檔案樹：(repo-relative path, 內容)。
    buggy_repo: tuple[tuple[str, str], ...]
    #: 把 buggy_repo 修好的 unified diff（→ hidden/reference.patch）。
    reference_patch: str
    #: 看得到的驗證測試：(repo-relative path, 內容)。
    public_tests: tuple[tuple[str, str], ...]
    #: 藏起來的關鍵測試：(bare 檔名, 內容)，builder 置於 hidden/ 下。
    hidden_tests: tuple[tuple[str, str], ...]
    #: 公開需求：(id, text)；缺省由 summary 生一條 MAIN-1。
    requirements: tuple[tuple[str, str], ...]
    #: functional rubric 總分（缺省 60，沿用 pilot 慣例）。
    rubric_functional: int
