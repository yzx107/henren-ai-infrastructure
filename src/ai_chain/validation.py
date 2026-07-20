from __future__ import annotations

from datetime import date

import duckdb

from .audit import evidence_failures, sample_trace_rows


ALLOWED_LAYERS = {"云厂商", "GPU/ASIC", "HBM与先进封装", "网络与光互联"}
ALLOWED_EXPOSURES = {"直接-高", "直接-中", "直接-低", "间接", "待核验"}
MARKET_CURRENCIES = {"A": "CNY", "H": "HKD", "US": "USD"}


def _ids(rows: list[tuple[object, ...]]) -> list[object]:
    return [row[0] for row in rows]


def blocking_failures(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Return violations of the research protocol's hard blocking gates."""
    failures: list[str] = []

    relation_rows = connection.execute(
        """SELECT edge_id FROM supply_chain_edges
           WHERE source_id IS NULL OR disclosed_at IS NULL OR valid_from IS NULL"""
    ).fetchall()
    if relation_rows:
        failures.append(f"关系缺少来源或披露时间：{_ids(relation_rows)}")

    incomplete_rank_rows = connection.execute(
        """SELECT company_id FROM company_exposures
           WHERE capex_exposure IS NULL OR bottleneck IS NULL
              OR earnings_revision IS NULL OR profit_capture IS NULL
              OR priced_in IS NULL"""
    ).fetchall()
    if incomplete_rank_rows:
        ranked_rows = connection.execute(
            "SELECT company_id FROM opportunity_scores"
        ).fetchall()
        leaked = sorted(set(_ids(incomplete_rank_rows)) & set(_ids(ranked_rows)))
        if leaked:
            failures.append(f"五项评分不全却进入排名：{leaked}")

    hypothesis_rows = connection.execute(
        """SELECT hypothesis_id FROM hypotheses
           WHERE status <> 'approved_for_test'"""
    ).fetchall()
    if hypothesis_rows:
        # The MVP stores hypotheses but does not yet use them in a strategy view, so this is
        # reported only if a non-approved hypothesis can enter downstream strategy objects.
        strategy_objects = {
            row[0]
            for row in connection.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema='main'
                     AND (table_name LIKE '%strategy%' OR table_name LIKE '%position%' OR table_name LIKE '%portfolio%')"""
            ).fetchall()
        }
        if strategy_objects:
            failures.append(
                f"未批准假设进入策略/仓位流程：hypotheses={_ids(hypothesis_rows)}, objects={sorted(strategy_objects)}"
            )

    return failures


def validate(connection: duckdb.DuckDBPyConnection, as_of: date) -> list[str]:
    issues: list[str] = []

    company_count = connection.execute("SELECT count(*) FROM company_master").fetchone()[0]
    if company_count != 30:
        issues.append(f"首批公司必须恰好 30 家，当前为 {company_count} 家")

    layers = {row[0] for row in connection.execute("SELECT DISTINCT industry_layer FROM company_master").fetchall()}
    if layers != ALLOWED_LAYERS:
        issues.append(f"产业层级不匹配：{sorted(layers)}")

    missing_security = connection.execute(
        """SELECT company_id FROM company_master
           WHERE company_id NOT IN (SELECT company_id FROM security_master)"""
    ).fetchall()
    if missing_security:
        issues.append(f"缺少证券映射：{_ids(missing_security)}")

    bad_primary = connection.execute(
        """SELECT company_id FROM security_master GROUP BY company_id
           HAVING count(*) FILTER (WHERE is_primary) <> 1"""
    ).fetchall()
    if bad_primary:
        issues.append(f"主证券数量异常：{_ids(bad_primary)}")

    bad_currency = []
    for market, currency in MARKET_CURRENCIES.items():
        bad_currency.extend(
            row[0]
            for row in connection.execute(
                "SELECT security_id FROM security_master WHERE market=? AND currency<>?",
                [market, currency],
            ).fetchall()
        )
    if bad_currency:
        issues.append(f"市场币种不一致：{bad_currency}")

    missing_snapshot = connection.execute(
        """SELECT company_id FROM company_master
           WHERE company_id NOT IN (SELECT company_id FROM company_research_snapshot)"""
    ).fetchall()
    if missing_snapshot:
        issues.append(f"缺少研究快照：{_ids(missing_snapshot)}")

    bad_sources = connection.execute(
        "SELECT source_id FROM sources WHERE url NOT LIKE 'https://%' OR disclosed_at > accessed_at"
    ).fetchall()
    if bad_sources:
        issues.append(f"来源 URL 或日期异常：{_ids(bad_sources)}")

    future_rows = connection.execute(
        """SELECT 'research_snapshot', company_id FROM company_research_snapshot WHERE as_of_date > ?
           UNION ALL SELECT 'source', source_id FROM sources WHERE disclosed_at > ?
           UNION ALL SELECT 'edge', edge_id FROM supply_chain_edges WHERE disclosed_at > ? OR valid_from > ?
           UNION ALL SELECT 'exposure', company_id FROM company_exposures WHERE as_of_date > ?""",
        [as_of, as_of, as_of, as_of, as_of],
    ).fetchall()
    if future_rows:
        issues.append(f"存在未来数据：{[(kind, item) for kind, item in future_rows]}")

    bad_confidence = connection.execute(
        """SELECT company_id FROM company_research_snapshot WHERE confidence NOT BETWEEN 0 AND 1
           UNION ALL SELECT edge_id FROM supply_chain_edges WHERE confidence NOT BETWEEN 0 AND 1"""
    ).fetchall()
    if bad_confidence:
        issues.append(f"置信度越界：{_ids(bad_confidence)}")

    exposures = {row[0] for row in connection.execute("SELECT DISTINCT ai_revenue_exposure FROM company_research_snapshot").fetchall()}
    if not exposures <= ALLOWED_EXPOSURES:
        issues.append(f"AI 收入暴露枚举异常：{sorted(exposures - ALLOWED_EXPOSURES)}")

    orphan_edges = connection.execute(
        """SELECT edge_id FROM supply_chain_edges e
           WHERE NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=e.source_company_id)
              OR NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=e.target_company_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=e.source_id)"""
    ).fetchall()
    if orphan_edges:
        issues.append(f"产业关系存在孤儿引用：{_ids(orphan_edges)}")

    incomplete_edges = connection.execute(
        """SELECT edge_id FROM supply_chain_edges
           WHERE source_id IS NULL OR disclosed_at IS NULL OR valid_from IS NULL
              OR confidence IS NULL"""
    ).fetchall()
    if incomplete_edges:
        issues.append(f"产业关系缺少审计字段：{_ids(incomplete_edges)}")

    edge_date_mismatch = connection.execute(
        """SELECT e.edge_id FROM supply_chain_edges e
           JOIN sources s USING (source_id)
           WHERE e.disclosed_at <> s.disclosed_at"""
    ).fetchall()
    if edge_date_mismatch:
        issues.append(f"关系与来源披露日期不一致：{_ids(edge_date_mismatch)}")

    orphan_evidence = connection.execute(
        """SELECT evidence_id FROM source_evidence v
           WHERE NOT EXISTS (SELECT 1 FROM supply_chain_edges e WHERE e.edge_id=v.edge_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=v.source_id)
              OR length(v.content_sha256) <> 64"""
    ).fetchall()
    if orphan_evidence:
        issues.append(f"来源证据引用或哈希异常：{_ids(orphan_evidence)}")

    archive_issues = evidence_failures(connection)
    trace_rows = sample_trace_rows(connection)
    if archive_issues or len(trace_rows) != 10:
        issues.append(f"关系抽查/来源归档校验失败：sample={len(trace_rows)}/10, errors={archive_issues}")

    invalid_scores = connection.execute(
        """SELECT company_id FROM company_exposures
           WHERE coalesce(capex_exposure, 0) NOT BETWEEN 0 AND 5
              OR coalesce(bottleneck, 0) NOT BETWEEN 0 AND 5
              OR coalesce(earnings_revision, 0) NOT BETWEEN 0 AND 5
              OR coalesce(profit_capture, 0) NOT BETWEEN 0 AND 5
              OR coalesce(priced_in, 0) NOT BETWEEN 0 AND 5"""
    ).fetchall()
    if invalid_scores:
        issues.append(f"暴露评分越界：{_ids(invalid_scores)}")

    gate_issues = blocking_failures(connection)
    if gate_issues:
        issues.append(f"硬闸门校验失败：{gate_issues}")

    leaked_outputs = connection.execute(
        """SELECT 'initial_universe', company FROM initial_universe WHERE disclosed_at > ?
           UNION ALL SELECT 'opportunity_scores', company_id FROM opportunity_scores WHERE as_of_date > ?""",
        [as_of, as_of],
    ).fetchall()
    if leaked_outputs:
        issues.append(f"研究输出包含未来快照/窗口：{[(kind, item) for kind, item in leaked_outputs]}")

    return issues
