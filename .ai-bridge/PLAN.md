# 项目计划

- 项目：全球 AI 基础设施产业链图谱
- MVP 合同：`MVP-1`
- 数据截止日：`2026-07-19`
- 当前目标：建立可审计、可重复运行的投资研究数据层，而不是产业链百科或展示网站。

## 投资目标

回答全球 AI 资本开支流向哪些环节，并区分：

1. 真实订单、收入、利润与自由现金流暴露；
2. 二级传导或概念映射；
3. 基本面变化是否快于盈利预期与价格反映。

## 第一版冻结范围

- 公司恰好 30 家；证券覆盖必要的 A/H/US 及全球核心节点映射。
- 只覆盖四层：云厂商、GPU/ASIC、HBM 与先进封装、网络与光互联。
- 只验收五项：公司/证券主表、可追溯关系、30 家范围、三个投资问题、五个重复输出。
- 当前执行顺序以 `docs/MVP_ACCEPTANCE.md` 为准；未通过项不得写成完成。

## 明确不做

- GNN、Transformer 预测、Neo4j 复杂迁移；
- 多 Agent 自动决策、自动交易；
- 全市场覆盖、电力/冷却/服务器扩层；
- 静态网页或 UI 优化；
- 在 point-in-time 证据不足时做回测或给出“未充分定价”结论。

## 核心架构

```text
官方披露与人工研究判断
        ↓
data/seed/*.csv + data/sources/archive/*
        ↓
src/ai_chain/db.py → runtime/ai_chain.duckdb
        ↓
schema 表 / parameterized as-of macros
        ↓
PIT 研究查询 / CLI DQA / 统一 build / 验收合同
```

CSV 是可审计源层；DuckDB 是本地查询层。供应链关系通过 `source_evidence` 连接到原始披露定位、本地归档和 SHA-256。

## 数据流

1. `ai-chain init-seed` 显式从种子 CSV 重建 DuckDB；这是唯一会重置研究表的命令。
2. `ai-chain check` 检查范围、映射、日期、来源、置信度、孤儿引用和归档哈希。
3. `ai-chain trace-audit` 用固定种子 `mvp-v1` 生成 10 条关系抽查。
4. `ai-chain build --as-of YYYY-MM-DD` 只读已有数据库并生成五个确定性研究输出。
5. `ai-chain acceptance --as-of YYYY-MM-DD` 实际执行三类查询、重建和失败注入。

## 主要模块

| 路径 | 职责 |
|---|---|
| `sql/schema.sql` | 表和视图定义 |
| `src/ai_chain/db.py` | CSV 载入与 DuckDB 初始化 |
| `src/ai_chain/as_of.py` | cutoff 标准化 |
| `src/ai_chain/research.py` | CapEx、收入暴露和预期差 PIT 查询 |
| `src/ai_chain/build.py` | 五输出确定性构建与内容校验 |
| `src/ai_chain/validation.py` | 数据质量闸门 |
| `src/ai_chain/audit.py` | 关系抽样、归档与哈希校验 |
| `src/ai_chain/acceptance.py` | 五项 MVP 验收合同 |
| `src/ai_chain/cli.py` | 命令行入口 |
| `tests/test_project.py` | PIT、失败注入、范围、DQA 和审计回归测试 |

## 下一步边界

当前 P0/P1 实现进入 GitHub CI 与 domain review，不扩公司、层级、模型或界面。CI 与 review 均通过后才能写最终 MVP ACCEPTED。
