# 研究协议与扩展顺序

## 1. 研究问题

主问题：2026—2027 年 AI 基础设施新增资本开支的边际美元，主要流向 GPU、网络、光互联、存储封装，还是后续扩展层的电力与冷却；每一层的增量收入能否转成利润和自由现金流，并超出市场原有预期？

第一阶段的验收标准不是“图更完整”，而是：

1. 每个关系都有 `disclosed_at`，回测可按当时信息集重建。
2. 订单、交付、收入、利润、现金流、预期修正分开记录。
3. 直接披露、研究者推断、待核验三种证据不混写。
4. 任一候选结论都有最强反方和可执行的失效条件。

## 2. 事件时间规则

- `event_time`：事件实际发生时间。
- `first_available_time`：投资者最早能合理取得信息的时间。
- `disclosed_at`：公开材料的发布日期。
- 回测只使用 `first_available_time <= portfolio_formation_time` 的记录。
- 盘后披露不能进入同日收盘价形成的组合。
- 后续更正不覆盖旧值；新建版本并保留旧记录。

## 3. 暴露证据等级

| 等级 | 最低证据 |
|---|---|
| 直接-高 | 公司单列 AI 收入/订单，或披露具规模的明确合同/产品收入 |
| 直接-中 | 产品明确服务 AI 基础设施，但 AI 收入未完全拆分 |
| 直接-低 | AI 是广义分部中的一小部分，尚无规模证据 |
| 间接 | 需求机制合理，但没有直接收入或订单映射 |
| 待核验 | 只有媒体/市场说法，缺少一手披露 |

收入暴露等级不能替代 `ProfitCapture`。高收入暴露公司也可能因价格下降、资本开支、营运资本或客户议价而无法产生自由现金流。

## 4. MVP 内的数据闭环

以下工作只用于满足 [MVP 验收合同](MVP_ACCEPTANCE.md)，不是扩大范围。

### 当前骨架

- 冻结首批 30 家和四层公司口径。
- 对 9 条已披露关系做双向来源复核。
- 为每家公司补 `primary_business`、财政年度和证券映射。

### 资本开支

新增 `capex_quarterly`，最少字段：

```text
company_id, fiscal_quarter, period_end, disclosed_at,
total_capex, finance_lease_additions, server_share,
datacenter_network_share, ai_capex_comment, currency, source_id
```

不把总资本开支直接映射成某家供应商机会。

### 利润与预期

新增 `company_quarterly` 和 `expectations`：

```text
revenue, segment_revenue, gross_margin, operating_margin,
capex, free_cash_flow, inventory, backlog, customer_concentration
```

```text
security_id, snapshot_date, revenue_consensus, eps_consensus,
valuation_multiple, revision_1m, revision_3m, provider
```

所有供应商和货币字段必须保留，禁止在源层混用口径。

### 简单验证

- CapEx 指引事件的 0/1/5/20 日分层相对收益。
- CapEx 变化到供应链收入预测修正的 1 月、1 季度、2 季度滞后。
- 直接/间接、高/低毛利率、高/低估值、A/H/US 分组。
- 报告同时展示样本数、置信区间、事件重叠和幸存者偏差检查。

## 5. 三个硬闸门

- 没有来源和披露时间：不进关系表。
- 五项评分不全：不进排名。
- 假设状态不是 `approved_for_test`：不进入策略或仓位流程。
