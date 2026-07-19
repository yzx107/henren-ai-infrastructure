# 关键决策

## 本轮 P0 决策

### 1. 所有研究入口必须显式接收 cutoff

- 删除无参数的 `initial_universe`、`opportunity_scores` 静态视图。
- 使用 `latest_company_research(cutoff)` 作为公司研究快照的统一 PIT 入口。
- `initial_universe_as_of(cutoff)` 和 `opportunity_scores_as_of(cutoff)` 只能读取 cutoff 前已可得且未被 supersede 的记录。
- 原因：仅在 schema 增加时间列不能防止未来函数，过滤必须在研究查询中生效。

### 2. 区分事件日、首次可得时间、摄取时间和修订状态

- `disclosed_at`：公开披露自然日。
- `first_available_at`：研究系统允许使用该记录的最早时间。
- `ingested_at`：记录进入本地数据层的时间。
- `revision_id` / `superseded_at`：修订身份和失效边界。
- 旧日期数据按 UTC 日末回填，明确保留“不支持日内研究”的限制。

### 3. AC4 必须执行研究查询

- 固定 fixture 检查 Alphabet CapEx 的直接 NVIDIA 路径和二级 SK hynix、Coherent、Lumentum 路径。
- CapEx 查询的研究主键是 `event_id`；同一公司的旧事件不能被新事件替换。
- 收入暴露由 `company_exposure_evidence` 和固定规则计算，旧人工标签不是最终真值。
- Q3 只做可比较性验证；期间、单位、收益窗口、benchmark 或估值任一不完整时，唯一合法结论是“数据不足”。
- 原因：视图存在或 SQL 能解析不能证明研究问题被正确回答。

### 4. AC5 使用单一 Python CLI build

- 命令：`uv run ai-chain build --as-of YYYY-MM-DD`。
- 原因：项目规模小，不另加 Makefile、编排器或服务层。
- build 默认只读现有数据库，先运行 DQA，再生成五个确定性文件；不会调用 seed 初始化。
- 只有显式 `ai-chain init-seed` 会删除研究表内容并从 CSV seed 重建。
- 输出 JSON 不写运行时间等不稳定字段；同输入、同 cutoff 的研究内容哈希必须一致。

### 5. 验收必须自证失败路径

- AC5 在临时目录删除并重建输出。
- 注入空文件、未来 cutoff 和币种 DQA 错误，要求构建/校验失败。
- 验收遇到坏数据返回 `FAIL`，不向上抛出未处理异常，也不得把 partial 写成 PASS。

### 6. Q2 采用证据强度规则，不采用人工标签直通

- `REVENUE`、`REVENUE_SHARE`、`ORDER`、`BACKLOG`、`SHIPMENT`、`DEPLOYMENT` 的可得高置信证据可进入 `直接-高`。
- 只有具名客户证据时最高为 `直接-中`；只有产品或管理层陈述时最高为 `直接-低`。
- 只有间接行业证据时为 `间接`；无证据、低置信或直接/间接证据冲突时为 `待核验`。
- 规则版本固定为 `exposure-evidence-v1`，并输出证据类型、期间、来源定位和最强反方。

### 7. Q3 是可比较性验证器，不是低估模型

- 财务指标与一致预期必须匹配指标名、财务期和单位。
- 价格必须有明确起止窗口、收益口径、benchmark 和可复算超额收益。
- 估值必须有指标、forward period 和历史分位。
- 完整同口径记录只标记 `可比较候选（非投资结论）`；不根据几个百分比自动推断 Alpha。

### 8. `supply_chain_master.csv` 固定为 edge grain

- 一行一条 `edge_id`，同时输出关系两端、有效期、来源、精确定位、归档、SHA-256 和审计结果。
- 公司研究 universe 不再冒充供应链关系主表，也不新增第六个 AC5 文件。

## 延续的架构决策

### CSV 源层 + DuckDB 查询层

- 当前 30 家/10 条关系的规模下，CSV 可读、可 diff、可重建，DuckDB 足够支持 PIT 查询。
- PostgreSQL、Neo4j 和正式迁移框架继续推迟；本轮不为未来规模预先增加抽象。

### 公司实体与证券分离

- `company_master` 表示经济实体；`security_master` 表示上市证券。
- A/H/ADR 不增加公司计数，市场、币种和证券类型保留在证券层。

### 关系必须证据化

- 每条关系需要来源、披露时间、有效期和置信度；本地证据归档保存定位及 SHA-256。
- 固定种子 `mvp-v1` 对当前 10 条关系做 10/10 审计。

### 不完整数据不制造排名

- 五项暴露评分任一缺失，不进入 `opportunity_scores_as_of`。
- 基本面、预期、估值或价格任一缺失，不进入预期差观察名单。

## 明确推迟

- 扩公司、扩产业层级、UI、GNN、Transformer、自动交易。
- 自动外部数据抓取、正式增量修订管线、并发服务和日内事件研究。
- 这些不是隐含待办，除非用户重新授权，否则不进入下一轮范围。

## 当前技术债务

1. 旧数据的 PIT 时间是日期到 UTC 日末的保守回填，不是精确发布时间。
2. schema 有修订字段，但业务键和种子加载尚未支持同一事实的完整修订链。
3. DQA 主要在应用层执行，数据库没有完整外键约束。
4. 结构化暴露证据与 Q3 四类信号种子为空，因此正式输出没有高暴露或“未充分定价”结论。
5. 公司研究快照和证券映射的证据归档弱于供应链关系。
