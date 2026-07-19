# 全球 AI 基础设施产业链图谱

这个项目不是公司百科，也不把“出现在 AI 产业链上”当作投资结论。它把以下四条链放进同一个可追溯数据模型：

```text
资本开支 → 实物交付 → 收入/利润/自由现金流 → 市场预期差
```

首版截至 **2026-07-19**，只覆盖 30 家公司和四层：云厂商、GPU/ASIC、HBM 与先进封装、网络与光互联。A/H/US 是主要研究市场，同时保留 TSMC、SK hynix、Samsung、IBIDEN 等不可省略的全球核心节点。

第一版范围和完成定义已经冻结在 [MVP 验收合同](docs/MVP_ACCEPTANCE.md)。当前状态为 **AC1—AC3 PASS；AC4—AC5 IMPLEMENTED, DOMAIN REVIEW PENDING**。在 GitHub CI 和 domain review 完成前，不宣称最终 MVP ACCEPTED。

## 已完成的最小闭环

- 30 家公司主表与 34 条证券映射；同一公司的 ADR/A/H 股不会重复计算公司数。
- 每家公司一条研究快照：产品、客户类型、AI 收入暴露、核心假设、最强反方、来源与披露时间。
- 10 条只有公开证据支持的公司间关系；每条均绑定原始披露定位、本地归档和 SHA-256。
- 固定种子 `mvp-v1` 的关系审计输出；归档缺失或哈希变化会使 AC2 失败。
- 三个可证伪假设和 `candidate` 状态闸门。
- DuckDB 数据库、CLI、PIT 查询宏、数据质量检查和评分查询。
- 一个统一 build 命令，可确定性生成五个带 `as_of_date` 的研究输出。
- 五项评分表默认留空。没有完整证据时，`rank` 不生成伪精确排名。

## 快速开始

```bash
cd "/Volumes/Data/Henren Investment System/ai_infrastructure_graph"
uv sync
uv run ai-chain init-seed
uv run ai-chain check --as-of 2026-07-19
uv run ai-chain build --as-of 2026-07-19
uv run ai-chain summary --as-of 2026-07-19
uv run ai-chain universe --as-of 2026-07-19
uv run ai-chain rank --as-of 2026-07-19
uv run ai-chain trace-audit
uv run ai-chain acceptance --as-of 2026-07-19
uv run pytest -q
```

默认数据库位于 `runtime/ai_chain.duckdb`，由 CSV 种子数据重建。CSV 是可审计的源层，DuckDB 是查询和分析层。
上述命令应顺序执行；多个进程同时打开同一个本地 DuckDB 文件可能触发文件锁冲突。

## 数据结构

| 表/视图 | 作用 |
|---|---|
| `company_master` | 公司与主产业层级 |
| `security_master` | A/H/US 及必要的 TW/KR/JP 证券映射 |
| `sources` | 来源、披露时间及 PIT/修订字段 |
| `company_research_snapshot` | 可按 cutoff 取得的版本化公司研究判断 |
| `supply_chain_edges` | 带有效期、披露时间和 PIT/修订字段的产业关系 |
| `source_evidence` | 关系对应的原始披露定位、归档路径、哈希和人工审计结论 |
| `hypotheses` | 机制、最强反方、证伪条件和状态 |
| `company_exposures` | 五项 0—5 分的人工/模型评分输入 |
| `company_exposure_evidence` | Q2 结构化收入、订单、客户、交付和产品证据；规则分类的唯一事实输入 |
| `capex_events` | 云厂商 CapEx 事件及 PIT 字段 |
| `fundamental_signals` / `expectation_signals` / `price_signals` / `valuation_signals` | Q3 同期间、同单位、明确窗口的可比较性输入；种子当前为空 |
| `initial_universe_as_of(cutoff)` | 每家公司截至 cutoff 最新的一条有效研究快照 |
| `opportunity_scores_as_of(cutoff)` | 只对 cutoff 前五项评分齐全的记录计算分数 |

评分公式按第一版定义：

```text
0.25 × CapExExposure
+ 0.20 × Bottleneck
+ 0.20 × EarningsRevision
+ 0.20 × ProfitCapture
- 0.15 × PricedIn
```

评分不是事实。每次评分必须带 `as_of_date`、`source_id`、置信度和分析备注；缺一项就不进入排名。

## 研究边界

- `主要客户` 只写公司公开披露的名称；否则写客户类型。
- `company_research_snapshot.ai_revenue_exposure` 只是旧研究候选标签；最终分类必须由 `company_exposure_evidence` 规则计算。
- 产品发布、客户认证、订单、交付、收入确认是不同事件，不能互相替代。
- 公司 IR 是一手来源，但仍带管理层选择性披露偏差。
- 当前财务、预期、估值和价格种子为空，因此 Q3 只返回“数据不足”。完整 fixture 也只产生“可比较候选（非投资结论）”，不会自动判断低估。
- 这里的 `candidate` 假设和空评分不进入交易或风险承担流程。

本轮仍严格冻结为 30 家、四层、10 条关系；后续扩范围必须由用户另行授权。
