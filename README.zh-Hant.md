# Poliscope

> **閱讀語言 / Language / 語言：** [简体中文](README.md) · [繁體中文](README.zh-Hant.md) · [English](README.en.md)

> 七位 AI 科學家，一場沒有和稀泥的科研審查。歡迎來到現場。
>
> **線上體驗：** [https://poliscope.tech/](https://poliscope.tech/) — 作者部署的公開實例，打開即用，無需安裝
>
> **學術論文：** [《EpistemoBrain: A Seven-Scientist Council with Dual-Graph Evidence Governance for Auditable Scientific Blindspot Discovery》](docs/paper/epistemobrain_main.pdf)（ACL Findings 2026 格式；LaTeX 源見 `docs/paper/`）

Poliscope 是面向計算社會科學爭議問題的**深度研究智慧體**：輸入一個存疑的問題，**7 名各有專長的 AI 科學家**獨立取證、交叉質詢、專門找反例，最後產出一張**結論與局限並排、證據可溯源、異議不刪除**的爭議證據地圖。

## 核心原則

- **不是醫療建議。** 涉及心理健康的問題，報告明確標註「這是科研輔助，不是臨床診斷」。
- **不編結論。** 模型沒配好、預算耗盡、席位缺席，都會逐條顯式標記——空白不等於沒問題。
- **模型置信度不是統計證據。** 模型判斷、作者原話與統計推斷分開標註。
- **異議不刪除。** 被反駁、被隔離的觀點保留原文出處，可追溯。

## 文件與部署

- 自建部署（Docker Compose）、完整 CLI / API 用法與架構細節：[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- 正式設計規格：[`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md)
- 技術白皮書：[《Poliscope 技術白皮書》](docs/tech/tech.pdf)

## 許可證

Poliscope 自身程式碼以 [MIT 許可證](LICENSE) 開源；執行記憶基座 [MemoBrain](https://github.com/qhjqhj00/MemoBrain) 保留其自身許可證，歸屬細節見 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md#许可证与归属)。
