---
type: fix
scope: changelog
---
補齊 `changelog.d/` 內 8 個既有 fragment（`1.md`、`2.md`、`beginner-play-rules.md`、`encounter-authoring.md`、`friendly-cli.md`、`legible-battle-report.md`、`oauth-aliases.md`、`versus.md`）缺失的 YAML frontmatter，並修正 `48-project-policy-manifest.md` 的非法 type `chore` → `change`（合法值僅 `change`/`deprecate`/`feat`/`fix`/`perf`/`refactor`/`remove`/`security`）；另將 `2.md` 依原始內容拆為 `2-relative-runs-root-checkpoint.md`（fix）與 `2-live-spectator.md`（feat）兩個獨立 fragment，使各自 type 正確反映內容。這些缺陷全數早於本次 policy 1.0.15 升級即存在，非本次升級新增；修正後 `collate` 乾跑已可正常通過，不再於下次 release 時因 `FragmentError` 中斷。
