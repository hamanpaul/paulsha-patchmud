"""Cortex dispatch builder lane（paulsha-cortex#842）：在 Cortex builder 派工條件下量測。

PatchMUD 依 Cortex Manager 為某一次目標派工算出的條件執行 builder session（Cortex
codex argv、builder persona 契約、外層 workspace-write 邊界、egress allowlist），以既有
deterministic evaluator 為 commit 出來的候選評分，並產出 resolved key 與該派工
``binding.resolved_key`` 完全相同的 execution profile。設計與條件對照見
``docs/execution-profile-v1.md``「Cortex dispatch builder lane」。
"""
