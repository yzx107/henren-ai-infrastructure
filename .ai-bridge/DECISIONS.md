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
- 收入暴露查询必须同时返回分类依据、来源、披露时间和数据 cutoff。
- 预期差查询缺少经营、预期、估值或价格任一项时，唯一合法结论是“数据不足”，`is_watchlist=false`。
- 原因：视图存在或 SQL 能解析不能证明研究问题被正确回答。

### 4. AC5 使用单一 Python CLI build

- 命令：`uv run ai-chain build --as-of YYYY-MM-DD`。
- 原因：项目规模小，不另加 Makefile、编排器或服务层。
- build 先运行 DQA，再生成五个确定性文件，最后校验文件内容和未来日期。
- 输出 JSON 不写运行时间等不稳定字段；同输入、同 cutoff 的研究内容哈希必须一致。

### 5. 验收必须自证失败路径

- AC5 在临时目录删除并重建输出。
- 注入空文件、未来 cutoff 和币种 DQA 错误，要求构建/校验失败。
- 验收遇到坏数据返回 `FAIL`，不向上抛出未处理异常，也不得把 partial 写成 PASS。

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
4. 财务、预期、估值和价格信号表为空，因此当前没有可投资的“未充分定价”结论。
5. 公司研究快照和证券映射的证据归档弱于供应链关系。
