#37：

- PR 1 新增 execution-profile v1 descriptor、requested／resolved／observed profile 與和 Cortex wire contract 對齊的 canonical key；`patchmud run` 可指定 adapter 原生 effort／工具模式，並在建立 adapter 前拒絕 descriptor 未宣告的設定，省略 effort 時保留 codex／agy 的 `high` 預設。
- PR 2 新增欄位級 usage provenance 與 append-only `usage_evidence.jsonl`，只封存正規化值，不保存 provider 原始 payload；run/report 保留 observed／estimated／unknown、來源／schema／adapter 版本及 usage 語意，unknown 不轉為 0，失敗前已收到的 usage 仍封存。report 直接升為 v2 並只輸出 v2，增加 role／coverage／artifact digest 與 profile cohort 排名、JSON/YAML/CSV 同源輸出及結構驗證；舊 run v1 目錄和 tar 可唯讀重建，缺少 provenance 的值維持 legacy/unknown。
- PR 3 新增 execution-profile v1、usage-provenance v1、report v2 與 legacy v1 run/result/report 的公開 golden 正反例；固定 manifest 記錄來源 revision、schema versions 與逐檔 SHA-256，測試會拒絕未同步 digest 與 hidden bytes／憑證／個人路徑。新增 `patchmud schema --json` 能力宣告及 v1→v2 migration 矩陣；report v1 僅 opaque 讀取，producer 只輸出 v2。
