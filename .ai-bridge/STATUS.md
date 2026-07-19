# 当前状态

- 状态日期：`2026-07-19`
- 分支：`codex/p0-acceptance-pit`
- PR：`#1 P0: enforce point-in-time acceptance gates`
- 被 review 的旧 SHA：`f2302457c29421438fe2cf468b7278de99fb1f71`
- 总体状态：`MVP ACCEPTANCE PENDING DOMAIN REVIEW`
- 验收口径：`AC1—AC3 PASS；AC4—AC5 IMPLEMENTED, REVIEW PENDING`
- 范围保持：30 家公司、四层、10 条关系、34 条证券映射。

## 本轮 P0/P1 状态

| 项目 | 状态 | 证据 |
|---|---|---|
| P0-1 Q2 证据化分类 | IMPLEMENTED | `company_exposure_evidence` + `exposure-evidence-v1`；旧人工标签只作候选备注 |
| P0-2 Q3 数据合同 | IMPLEMENTED | 同指标、期间、单位、窗口、benchmark、估值完整才返回“可比较候选（非投资结论）” |
| P0-3 edge-level 主表 | IMPLEMENTED | `supply_chain_master.csv` 一行一条 edge，含定位、归档、哈希和审计结果 |
| P1-1 event_id 查询 | IMPLEMENTED | `capex_beneficiaries(connection, event_id, as_of)` 不再自动选择公司最新事件 |
| P1-2 非破坏性 build | IMPLEMENTED | `build` 只读已有数据库；只有显式 `init-seed` 重建种子库 |
| P1-3 GitHub Actions | IMPLEMENTED, RUN PENDING | `.github/workflows/research-ci.yml`；等待新 SHA push 后远端运行 |

## 数据合同变化

- 新增 `company_exposure_evidence`，证据类型限定为收入、收入占比、订单、积压、具名客户、出货、部署、产品、管理层陈述、间接行业暴露。
- Q2 规则：可验证收入/订单/出货/部署证据可到 `直接-高`；仅具名客户最高 `直接-中`；仅产品或管理层陈述最高 `直接-低`；纯间接证据为 `间接`；无证据、低置信度或直接/间接冲突为 `待核验`。
- `fundamental_signals` 增加 value/unit/fiscal/comparison/actual-or-guidance 语义。
- `expectation_signals` 增加 forecast metric/period/unit、current/previous、明确 previous/current snapshot 窗口、revision 和 consensus source。
- `price_signals` 增加明确窗口、收益口径、原始/benchmark/超额收益和 benchmark ID。
- 新增 `valuation_signals`，包含估值指标、数值、forward period 和历史分位。
- Q3 不实现 Alpha 评分；只做可比较性验证和原始字段对照。

## 本地真实验证

```text
uv sync --frozen                                      exit 0
uv run pytest -q                                      exit 0 | 32 passed in 5.04s
uv run ai-chain init-seed                             exit 0 | 30/4/10/34 unchanged
uv run ai-chain check --as-of 2026-07-19             exit 0 | DQA PASS
uv run ai-chain build --as-of 2026-07-19             exit 0 | five outputs
uv run ai-chain trace-audit                           exit 0 | 10/10 PASS
uv run ai-chain acceptance --as-of 2026-07-19        exit 0 | AC1—AC5 implementation checks PASS
```

CLI 最后一行是 `AC1-AC5 IMPLEMENTED; DOMAIN REVIEW PENDING`，不是最终 MVP ACCEPTED。

## 输出哈希（当前本地运行）

```text
capex_tracker.csv                66dece122024bf59044af512e23ac14fb550ecea001753e7e52685722f6b9349
data_quality_report.json         c5749aef100ba088aeee781e96f538ac4b670590466877258213589e06500e89
expectation_gap_watchlist.csv    82f6d21e76f7ee6075f9ecdeba4187f5b81cf9d6ab2032637b0b49f0b64b4ed4
profit_transmission.csv          8b0715382c1d52687b8a90f490105c7fddf07ba66518111f3d19868e13c96294
supply_chain_master.csv          c199f52f0a2626aeb8fe6fba96d725024be549fb863338fbebdf4d4b1662140e
```

## GitHub CI 门槛

- Workflow：`research-ci`
- 触发：PR opened/synchronize/reopened/ready_for_review 和 workflow_dispatch。
- 固定 Python：`3.12`。
- 输出 artifact：`research-output-2026-07-19`。
- 日志 artifact：`research-ci-logs`。
- 当前状态：尚未 push 新 SHA，因此远端 run 尚未产生。

## 已知限制

1. 结构化 Q2 证据种子为空；规则由少量固定 fixture 验证。没有证据的正式公司诚实返回 `待核验`，没有为了覆盖 30 家编造证据。
2. 财务、预期、价格和估值种子为空；Q3 正式输出为“数据不足”，完整 fixture 仅证明可比较性合同。
3. 旧来源时间按 UTC 日末保守回填，不支持日内事件研究。
4. `revision_id` / `superseded_at` 已进入查询，但尚无完整 immutable revision 摄取工作流。
5. 证券映射和部分公司研究快照的证据归档弱于 10 条供应链关系。
6. 最终 `MVP ACCEPTED` 仍需新 SHA 的 GitHub Actions 成功及 ChatGPT domain review 通过。
