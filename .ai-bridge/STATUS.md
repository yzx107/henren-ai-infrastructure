# 当前状态

- 状态日期：`2026-07-19`
- 分支：`codex/p0-acceptance-pit`
- 总体状态：`MVP ACCEPTED`（冻结合同 AC1—AC5 全部通过）
- 公开仓库：`https://github.com/yzx107/henren-ai-infrastructure`
- 范围未变化：30 家公司、四个产业层级、10 条关系、34 条证券映射。

## 验收状态

| 验收项 | 状态 | 执行证据 |
|---|---|---|
| AC1 公司与证券主表 | PASS | 30 家公司全部映射；主证券、市场和币种规则错误 0 |
| AC2 关系可追溯 | PASS | 10 条关系、10 份归档证据、固定抽样 10/10、哈希错误 0 |
| AC3 30 家四层 | PASS | 公司数和层级集合严格相等，不是下限检查 |
| AC4 三个投资问题 | PASS | 固定数据实际执行三类查询；覆盖 as-of、直接/二级路径、来源/披露时间、暴露证据、数据不足和未来排除 |
| AC5 五个重复输出 | PASS | 统一 build 生成五个非空输出；删除重建哈希一致；失败注入被阻断 |

## 本轮完成

- 为来源、公司研究快照、产业关系和暴露评分增加 `first_available_at TIMESTAMPTZ`、`ingested_at TIMESTAMPTZ`、`revision_id`、`superseded_at`。
- 新增带同类 PIT 字段的 `capex_events`、`fundamental_signals`、`expectation_signals`、`price_signals`。
- 删除无 cutoff 的 `initial_universe` 和 `opportunity_scores` 静态视图，改为 `initial_universe_as_of(cutoff)`、`opportunity_scores_as_of(cutoff)` 及统一 `latest_company_research(cutoff)`。
- `initial_universe_as_of` 每家公司只返回 cutoff 前最新的一条未失效研究快照。
- 实现 CapEx 直接/二级传导、收入暴露分类和预期差查询。市场数据任一缺失时返回“数据不足”且不进入观察名单。
- 实现 `uv run ai-chain build --as-of YYYY-MM-DD`，输出目录为 `outputs/research/as_of=YYYY-MM-DD/`。
- AC4/AC5 由“对象/文件名存在”改为实际执行查询、重建、哈希比较和失败注入。
- 测试增加空文件、仅表头、未来泄漏、多期快照去重、缺失预期/估值/价格及非零失败退出码。

## 本次真实验证结果

```text
uv run ai-chain init                                      exit 0
  sources=38, companies=30, securities=34, snapshots=30
  edges=10, source_evidence=10, capex_events=3
  fundamental/expectation/price signals=0

uv run ai-chain check --as-of 2026-07-19                 exit 0
  DQA PASS

uv run ai-chain acceptance --as-of 2026-07-19            exit 0
  AC1 PASS, AC2 PASS, AC3 PASS, AC4 PASS, AC5 PASS
  MVP ACCEPTED

uv run pytest -q                                          exit 0
  18 passed in 3.54s
```

最终 push 前会再次从干净输出目录运行同一验证；若结果变化，以 PR 描述中的最后一次结果为准。

## 统一构建与输出

```bash
uv run ai-chain build --as-of 2026-07-19
```

生成：

```text
outputs/research/as_of=2026-07-19/
  supply_chain_master.csv
  capex_tracker.csv
  profit_transmission.csv
  expectation_gap_watchlist.csv
  data_quality_report.json
```

五个文件必须存在、非空；四个 CSV 还必须至少有一条数据行。DQA 报告包含行数、主键重复、必填空值、孤儿引用、未来日期、币种/市场规则和关系抽查。

## 已知限制

1. 种子数据原本只有日期；迁移将 `first_available_at` 回填为披露日 UTC 日末，将 `ingested_at` 回填为访问日 UTC 日末。它能阻止跨日未来函数，不支持日内事件研究。
2. `revision_id` / `superseded_at` 已进入 schema 和查询闸门，但 CSV 主键仍是当前业务键；本轮没有建立完整的多修订摄取工作流。
3. 只有 Alphabet、Microsoft、Oracle 三条 CapEx 固定事件，用于验证查询机制；没有扩大公司或关系数据。
4. `fundamental_signals`、`expectation_signals`、`price_signals` 种子为空。利润传导和预期差输出会诚实显示“数据不足”，不会生成“未充分定价”名单。
5. 30 条公司研究快照并非全部拥有与 10 条产业关系同等级的本地原文归档。
6. `security_master` 仍缺逐条证券来源字段；AC1 的 PASS 是当前冻结合同的结构验收，不替代交易所层面人工抽查。
7. DuckDB 仍是本地单进程工作流；并发写入、增量批次和正式 migration 不在本轮范围。
