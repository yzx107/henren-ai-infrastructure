# ChatGPT Review Guide

请先只读 review，不要修改项目。总体目标是判断现有实现是否忠实支持 `MVP-1`，而不是评价界面或扩大产业范围。

## 建议阅读顺序

1. `docs/MVP_ACCEPTANCE.md`：冻结的完成定义。
2. `.ai-bridge/STATUS.md`：真实通过项、失败项和运行结果。
3. `sql/schema.sql`：数据模型和视图。
4. `data/seed/supply_chain_edges.csv`、`data/seed/source_evidence.csv`：关系与证据。
5. `src/ai_chain/audit.py`、`validation.py`、`acceptance.py`：未来函数与 DQA 是否真正生效。
6. `tests/test_project.py`：当前测试覆盖和遗漏。
7. `data/seed/company_master.csv`、`security_master.csv`、`company_research_snapshot.csv`：公司/证券/研究判断。
8. `outputs/mvp/relation_trace_sample.csv`：10 条关系抽查结果。

Git 历史从 `2026-07-19` 首次公开提交开始，因此可以 review 后续 diff，但无法追溯首次提交前的逐步实施过程。

## 数据库对象

| 对象 | 粒度与用途 |
|---|---|
| `company_master` | 每个经济实体一行；公司名称、国家、产业层和主业 |
| `security_master` | 每只证券一行；映射公司、ticker、市场、币种、类型、主证券标记 |
| `sources` | 每个外部披露来源一行；URL、披露日、访问日 |
| `company_research_snapshot` | 公司 × `as_of_date`；产品、客户、AI 暴露、假设、反方和来源 |
| `supply_chain_edges` | 每条有方向关系一行；产品、有效期、披露日、经济暴露、置信度和来源 |
| `source_evidence` | 每条关系一行；原文定位、归档、SHA-256 和人工审计 |
| `hypotheses` | 机制、最强反方、证伪条件和状态 |
| `company_exposures` | 公司 × 截止日的五项评分输入；当前为空 |
| `initial_universe` | 30 家初始研究表视图 |
| `opportunity_scores` | 只计算五项评分完整的公司；当前返回 0 行 |

## 外部字段及 point-in-time 含义

| 字段 | 来源 | 当前含义 | 已知限制 |
|---|---|---|---|
| `sources.disclosed_at` | 公司 IR、交易所或官方公告 | 市场最早可见的自然日 | 无时间和时区 |
| `sources.accessed_at` | 本地采集记录 | 本项目访问来源的自然日 | 不等于市场首次可见时间 |
| `company_research_snapshot.as_of_date` | 研究快照 | 该判断允许使用的信息截止日 | 依赖来源日期，但全部原文尚未归档 |
| `supply_chain_edges.valid_from` | 原始披露人工解释 | 关系可确认的生效起点 | 协议、交付、量产语义尚未完全统一 |
| `supply_chain_edges.valid_to` | 原始披露 | 关系失效日；空值表示尚无公开终止证据 | 空值不证明关系永久有效 |
| `supply_chain_edges.disclosed_at` | 原始披露 | 市场知道该关系的自然日 | 当前 DQA 要求等于来源披露日 |
| `confidence` | 人工判断 | 0—1 的证据置信度 | 不是发生概率或预期收益 |
| `economic_exposure` | 原始披露人工摘要 | 关系的商业含义 | 当前为文字，不能跨公司直接比较 |
| `content_sha256` | 本地归档计算 | 归档内容完整性指纹 | 只证明文件未变，不证明语义正确 |

当前没有 `first_available_at`、历史一致预期、价格、估值、CapEx 分拆或财务修订版本。这些缺失必须限制研究结论。

## 主要入口

- CLI：`src/ai_chain/cli.py`
- 数据库初始化：`src/ai_chain/db.py`
- DQA：`src/ai_chain/validation.py`
- 关系审计：`src/ai_chain/audit.py`
- MVP 验收：`src/ai_chain/acceptance.py`
- 测试：`tests/test_project.py`

## 从零运行当前最小验证

在项目根目录顺序执行。以下单一 shell 命令验证当前已经完成的能力并应返回 0：

```bash
uv run ai-chain init && uv run ai-chain check --as-of 2026-07-19 && uv run ai-chain trace-audit && uv run pytest -q
```

然后单独运行完整 MVP 验收：

```bash
uv run ai-chain acceptance
```

当前预期退出码为 `1`，因为 AC4/AC5 尚未完成。若它返回 0，需检查是否有人放宽了验收合同。

## 三个示例查询

用 Python/DuckDB 在 `runtime/ai_chain.duckdb` 上执行。

### 1. 公司与证券映射规模

```sql
SELECT
  (SELECT count(*) FROM company_master) AS companies,
  (SELECT count(*) FROM security_master) AS securities,
  (SELECT count(*) FROM (
    SELECT company_id FROM security_master GROUP BY company_id HAVING count(*) > 1
  )) AS multi_security_companies;
```

预期：`(30, 34, 4)`。

### 2. 截至 2026-02-28 可用的关系

```sql
SELECT edge_id, disclosed_at
FROM supply_chain_edges
WHERE disclosed_at <= DATE '2026-02-28'
ORDER BY disclosed_at, edge_id;
```

预期 4 行：`E_TSMC_AMKOR`、`E_COHERENT_MRVL`、`E_GOOG_NVDA`、`E_META_AMD`。任何 2026-03 之后关系出现在结果中都属于未来函数。

### 3. 关系证据完整性

```sql
SELECT
  count(*) AS evidence_rows,
  count(*) FILTER (WHERE auditor_result='PASS') AS pass_rows,
  count(DISTINCT content_sha256) AS distinct_hashes
FROM source_evidence;
```

预期：`(10, 10, 10)`。还应运行 `ai-chain trace-audit`，因为 SQL 计数本身不会重新计算文件哈希。

## 优先 review 问题

1. AC2 的字段是否真正阻断未来数据，而非仅存在于 schema。
2. 10 条关系的原文是否支持关系方向、产品和经济暴露。
3. `source_evidence` 的人工 PASS 是否过于宽松。
4. 公司/证券映射缺乏逐条来源是否应降低 AC1 状态。
5. 在没有 CapEx、财务、预期和价格数据时，AC4/AC5 是否被正确保持为失败。
6. 测试是否遗漏日期边界、来源修订、重复证券、归档缺失和跨币种问题。

请将结论按 `P0 / P1 / P2` 输出，并为每项给出文件、证据、建议修复和验证方法。不要因为 7 个测试通过就推断投资研究闭环已经完成。
