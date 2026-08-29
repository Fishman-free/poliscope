# Poliscope

> **阅读语言 / Language / 語言：** [简体中文](README.md) · [繁體中文](README.zh-Hant.md) · [English](README.en.md)

> 七位 AI 科学家，一场没有和稀泥的科研审查。欢迎来到现场。
>
> **在线体验：** [https://poliscope.tech/](https://poliscope.tech/) — 作者部署的公开实例，打开即用，无需安装
>
> **学术论文：** [《EpistemoBrain: A Seven-Scientist Council with Dual-Graph Evidence Governance for Auditable Scientific Blindspot Discovery》](docs/paper/epistemobrain_main.pdf)（ACL Findings 2026 格式；LaTeX 源见 `docs/paper/`）

Poliscope 是面向计算社会科学争议问题的**深度研究智能体**：输入一个存疑的问题，**7 名各有专长的 AI 科学家**独立取证、交叉质询、专门找反例，最后产出一张**结论与局限并排、证据可溯源、异议不删除**的争议证据地图。

## 核心原则

- **不是医疗建议。** 涉及心理健康的问题，报告明确标注「这是科研辅助，不是临床诊断」。
- **不编结论。** 模型没配好、预算耗尽、席位缺席，都会逐条显式标记——空白不等于没问题。
- **模型置信度不是统计证据。** 模型判断、作者原话与统计推断分开标注。
- **异议不删除。** 被反驳、被隔离的观点保留原文出处，可追溯。

## 文档与部署

- 自建部署（Docker Compose）、完整 CLI / API 用法与架构细节：[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- 正式设计规格：[`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md)
- 技术白皮书：[《Poliscope 技术白皮书》](docs/tech/tech.pdf)

## 许可证

Poliscope 自身代码以 [MIT 许可证](LICENSE) 开源；执行记忆基座 [MemoBrain](https://github.com/qhjqhj00/MemoBrain) 保留其自身许可证，归属细节见 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md#许可证与归属)。
