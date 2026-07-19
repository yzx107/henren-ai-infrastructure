# 全球 AI 基础设施产业链图谱

这个项目不是公司百科，也不把“出现在 AI 产业链上”当作投资结论。它把以下四条链放进同一个可追溯数据模型：

```text
资本开支 → 实物交付 → 收入/利润/自由现金流 → 市场预期差
```

首版截至 **2026-07-19**，只覆盖 30 家公司和四层：云厂商、GPU/ASIC、HBM 与先进封装、网络与光互联。A/H/US 是主要研究市场，同时保留 TSMC、SK hynix、Samsung、IBIDEN 等不可省略的全球核心节点。

第一版范围和完成定义已经冻结在 [MVP 验收合同](docs/MVP_ACCEPTANCE.md)。当前状态是 **MVP NOT ACCEPTED**；已有脚手架不等于五项验收完成。

## 已完成的最小闭环

- 30 家公司主表与 34 条证券映射；同一公司的 ADR/A/H 股不会重复计算公司数。
- 每家公司一条研究快照：产品、客户类型、AI 收入暴露、核心假设、最强反方、来源与披露时间。
- 10 条只有公开证据支持的公司间关系；每条均绑定原始披露定位、本地归档和 SHA-256。
- 固定种子 `mvp-v1` 的关系审计输出；归档缺失或哈希变化会使 AC2 失败。
- 三个可证伪假设和 `candidate` 状态闸门。
- DuckDB 数据库、CLI、数据质量检查和评分视图。
- 五项评分表默认留空。没有完整证据时，`rank` 不生成伪精确排名。

## 快速开始

```bash
cd "/Volumes/Data/Henren Investment System/ai_infrastructure_graph"
uv sync
uv run ai-chain init
uv run ai-chain check --as-of 2026-07-19
uv run ai-chain summary
uv run ai-chain universe
uv run ai-chain universe --output outputs/initial_universe.csv
uv run ai-chain rank
uv run ai-chain trace-audit
uv run ai-chain acceptance
uv run pytest
```

默认数据库位于 `runtime/ai_chain.duckdb`，由 CSV 种子数据重建。CSV 是可审计的源层，DuckDB 是查询和分析层。
上述命令应顺序执行；多个进程同时打开同一个本地 DuckDB 文件可能触发文件锁冲突。

## 数据结构

| 表/视图 | 作用 |
|---|---|
| `company_master` | 公司与主产业层级 |
| `security_master` | A/H/US 及必要的 TW/KR/JP 证券映射 |
| `sources` | 来源、披露时间、访问时间 |
| `company_research_snapshot` | 截止某日可见的公司研究判断 |
| `supply_chain_edges` | 有披露时间和置信度的产业关系 |
| `source_evidence` | 关系对应的原始披露定位、归档路径、哈希和人工审计结论 |
| `hypotheses` | 机制、最强反方、证伪条件和状态 |
| `company_exposures` | 五项 0—5 分的人工/模型评分输入 |
| `initial_universe` | 用户要求的首批公司表 |
| `opportunity_scores` | 只对五项评分齐全的记录计算分数 |

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
- `AI收入暴露` 是证据等级，不是假装精确的收入百分比。
- 产品发布、客户认证、订单、交付、收入确认是不同事件，不能互相替代。
- 公司 IR 是一手来源，但仍带管理层选择性披露偏差。
- 当前数据不能证明任何标的“尚未充分定价”；需要补充一致预期、估值和价格反应后才能检验。
- 这里的 `candidate` 假设和空评分不进入交易或风险承担流程。

后续工作只能用于消除 [MVP 验收合同](docs/MVP_ACCEPTANCE.md) 中的 blocker；五项通过前不扩公司、层级、模型或界面。
