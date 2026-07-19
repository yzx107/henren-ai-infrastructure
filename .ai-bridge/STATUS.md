# 当前状态

- 状态日期：`2026-07-19`
- 总体状态：`MVP NOT ACCEPTED`
- 公开仓库：`https://github.com/yzx107/henren-ai-infrastructure`
- 本次 handoff 只更新文档并运行验证；未修改研究逻辑、schema 或公司范围。

## 验收状态

| 验收项 | 状态 | 证据或阻断项 |
|---|---|---|
| AC1 公司与证券主表 | PASS | 30 家公司、34 条证券、30 家均映射、结构错误 0 |
| AC2 关系可追溯 | PASS | 10 条关系、10 份归档证据、固定抽样 10/10、哈希错误 0 |
| AC3 30 家四层 | PASS | 公司数恰好 30；层级集合严格等于四层 |
| AC4 三个投资问题 | FAIL | 缺 `capex_beneficiary_map`、`exposure_classification`、`expectation_gap_watchlist` |
| AC5 五个重复输出 | FAIL | 缺 CapEx、利润传导、预期差、DQA JSON 和完整产业链主表构建闭环 |

## 已完成

- 30 家公司主表、34 条证券映射、30 条研究快照。
- 38 条来源记录、10 条供应链关系、10 条关系证据。
- 原始披露定位、本地归档、SHA-256、固定随机抽查。
- DuckDB 初始化、DQA、关系审计、范围验收和评分公式测试。
- 对五项评分缺失的公司不生成伪精确排名。

## 未完成

- 云厂商 CapEx 事件及直接/二级传导查询。
- 真实收入暴露的证据化分类合同。
- 财务、盈利预期、估值和价格在同一截止日下的比较。
- 五个确定性研究输出及统一构建命令。
- 外部数据增量更新、修订版本、失败重试和运行日志体系。

## 已知问题与 review 限制

1. Git 历史从 `2026-07-19` 首次公开提交开始；首次提交前的实施过程没有可供 review 的历史 diff。
2. `disclosed_at` 只有 `DATE`，没有时间和时区；也没有独立 `first_available_at`。日内事件研究前必须修复。
3. `valid_from` 当前表示关系在披露材料中可确认的生效起点，但不同关系可能混合“协议签署日”“产品量产日”和“公开披露日”。
4. `security_master` 尚无逐条证券来源字段。AC1 的结构检查通过，不等于交易所层面的人工核验已完成。
5. 只有 10 条供应链关系建立了本地归档；30 条公司研究快照引用的所有来源尚未全部归档。
6. TSMC–Amkor 的站点拒绝自动下载，现保存经官方页面核验的正文文本快照及哈希；reviewer 应判断该证据形式是否满足长期归档标准。
7. `company_exposures` 当前为空；`opportunity_scores` 因而为空，这是数据闸门而不是运行错误。
8. 当前没有 CapEx、季度财务、一致预期、估值或价格表，不能回答“尚未充分定价”。
9. 多进程同时打开同一 DuckDB 文件可能产生文件锁；当前命令按顺序执行。

## 本次真实验证结果

```text
uv run ai-chain init                         exit 0
  sources=38, companies=30, securities=34
  edges=10, source_evidence=10, exposures=0

uv run ai-chain check --as-of 2026-07-19    exit 0
  DQA PASS

uv run ai-chain trace-audit                  exit 0
  TRACE AUDIT PASS, fixed_seed=mvp-v1, sample=10/10

uv run ai-chain acceptance                   exit 1
  AC1 PASS, AC2 PASS, AC3 PASS, AC4 FAIL, AC5 FAIL
  MVP NOT ACCEPTED

uv run pytest -q                             exit 0
  7 passed in 0.85s
```

## 当前可运行命令

```bash
uv run ai-chain init
uv run ai-chain check --as-of 2026-07-19
uv run ai-chain summary
uv run ai-chain universe
uv run ai-chain universe --output outputs/initial_universe.csv
uv run ai-chain rank
uv run ai-chain trace-audit
uv run ai-chain acceptance
uv run pytest -q
```

## 当前输出

| 路径 | 状态 |
|---|---|
| `outputs/initial_universe.csv` | 30 家初始表，存在 |
| `outputs/mvp/relation_trace_sample.csv` | 固定抽样 10 条，存在 |
| `outputs/mvp/supply_chain_master.csv` | 不存在，AC5 blocker |
| `outputs/mvp/capex_tracker.csv` | 不存在，AC5 blocker |
| `outputs/mvp/profit_transmission.csv` | 不存在，AC5 blocker |
| `outputs/mvp/expectation_gap_watchlist.csv` | 不存在，AC5 blocker |
| `outputs/mvp/data_quality_report.json` | 不存在，AC5 blocker |

当前两个 CSV 的 SHA-256：

```text
relation_trace_sample.csv ed3dfc452dddef82162afb71d8dd2bfd2798bfaa174bef456d255cb793ccd2d7
initial_universe.csv       d3daa4ad295111c6c9cfcc6b2435a3f87af46095c6a8efff180d0301202784d4
```
