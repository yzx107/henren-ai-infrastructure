from __future__ import annotations

from datetime import date

import duckdb

from .audit import evidence_failures


ALLOWED_LAYERS = {"云厂商", "GPU/ASIC", "HBM与先进封装", "网络与光互联"}
ALLOWED_EXPOSURES = {"直接-高", "直接-中", "直接-低", "间接", "待核验"}
MARKET_CURRENCIES = {"A": "CNY", "H": "HKD", "US": "USD"}
ALLOWED_EVIDENCE_TYPES = {
    "REVENUE", "REVENUE_SHARE", "ORDER", "BACKLOG", "NAMED_CUSTOMER",
    "SHIPMENT", "DEPLOYMENT", "PRODUCT_ONLY", "MANAGEMENT_STATEMENT",
    "INDIRECT_INDUSTRY_EXPOSURE",
}


def validate(connection: duckdb.DuckDBPyConnection, as_of: date) -> list[str]:
    issues: list[str] = []

    company_count = connection.execute("SELECT count(*) FROM company_master").fetchone()[0]
    if company_count != 30:
        issues.append(f"首批公司必须恰好 30 家，当前为 {company_count} 家")

    layers = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT industry_layer FROM company_master"
        ).fetchall()
    }
    if layers != ALLOWED_LAYERS:
        issues.append(f"产业层级不匹配：{sorted(layers)}")

    missing_security = connection.execute(
        """SELECT company_id FROM company_master
           WHERE company_id NOT IN (SELECT company_id FROM security_master)"""
    ).fetchall()
    if missing_security:
        issues.append(f"缺少证券映射：{[row[0] for row in missing_security]}")

    bad_primary = connection.execute(
        """SELECT company_id FROM security_master GROUP BY company_id
           HAVING count(*) FILTER (WHERE is_primary) <> 1"""
    ).fetchall()
    if bad_primary:
        issues.append(f"主证券数量异常：{[row[0] for row in bad_primary]}")

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
        issues.append(f"缺少研究快照：{[row[0] for row in missing_snapshot]}")

    bad_sources = connection.execute(
        "SELECT source_id FROM sources WHERE url NOT LIKE 'https://%' OR disclosed_at > accessed_at"
    ).fetchall()
    if bad_sources:
        issues.append(f"来源 URL 或日期异常：{[row[0] for row in bad_sources]}")

    missing_temporal = connection.execute(
        """SELECT source_id FROM sources
           WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL
              OR first_available_at > ingested_at
           UNION ALL
           SELECT company_id FROM company_research_snapshot
           WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL
              OR first_available_at > ingested_at
           UNION ALL
           SELECT edge_id FROM supply_chain_edges
           WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL
              OR first_available_at > ingested_at
           UNION ALL
           SELECT evidence_id FROM company_exposure_evidence
           WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL
              OR first_available_at > ingested_at
           UNION ALL
           SELECT signal_id FROM fundamental_signals
           WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL
              OR first_available_at > ingested_at
           UNION ALL
           SELECT signal_id FROM expectation_signals
           WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL
              OR first_available_at > ingested_at
           UNION ALL
           SELECT signal_id FROM price_signals
           WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL
              OR first_available_at > ingested_at
           UNION ALL
           SELECT signal_id FROM valuation_signals
           WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL
              OR first_available_at > ingested_at"""
    ).fetchall()
    if missing_temporal:
        issues.append(f"PIT 字段缺失或时间倒置：{[row[0] for row in missing_temporal]}")

    future_rows = connection.execute(
        """SELECT company_id FROM company_research_snapshot WHERE as_of_date > ?
           UNION ALL SELECT source_id FROM sources WHERE disclosed_at > ?""",
        [as_of, as_of],
    ).fetchall()
    if future_rows:
        issues.append(f"存在未来数据：{[row[0] for row in future_rows]}")

    bad_confidence = connection.execute(
        """SELECT company_id FROM company_research_snapshot WHERE confidence NOT BETWEEN 0 AND 1
           UNION ALL SELECT edge_id FROM supply_chain_edges WHERE confidence NOT BETWEEN 0 AND 1"""
    ).fetchall()
    if bad_confidence:
        issues.append(f"置信度越界：{[row[0] for row in bad_confidence]}")

    exposures = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT ai_revenue_exposure FROM company_research_snapshot"
        ).fetchall()
    }
    if not exposures <= ALLOWED_EXPOSURES:
        issues.append(f"AI 收入暴露枚举异常：{sorted(exposures - ALLOWED_EXPOSURES)}")

    evidence_types = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT evidence_type FROM company_exposure_evidence"
        ).fetchall()
    }
    if not evidence_types <= ALLOWED_EVIDENCE_TYPES:
        issues.append(f"收入暴露证据枚举异常：{sorted(evidence_types - ALLOWED_EVIDENCE_TYPES)}")

    invalid_evidence = connection.execute(
        """SELECT evidence_id FROM company_exposure_evidence
           WHERE source_locator IS NULL OR trim(source_locator)=''
              OR confidence NOT BETWEEN 0 AND 1
              OR (evidence_start IS NOT NULL AND evidence_end IS NOT NULL
                  AND evidence_start > evidence_end)"""
    ).fetchall()
    if invalid_evidence:
        issues.append(f"收入暴露证据合同异常：{[row[0] for row in invalid_evidence]}")

    orphan_edges = connection.execute(
        """SELECT edge_id FROM supply_chain_edges e
           WHERE NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=e.source_company_id)
              OR NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=e.target_company_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=e.source_id)"""
    ).fetchall()
    if orphan_edges:
        issues.append(f"产业关系存在孤儿引用：{[row[0] for row in orphan_edges]}")

    incomplete_edges = connection.execute(
        """SELECT edge_id FROM supply_chain_edges
           WHERE source_id IS NULL OR disclosed_at IS NULL OR valid_from IS NULL
              OR confidence IS NULL"""
    ).fetchall()
    if incomplete_edges:
        issues.append(f"产业关系缺少审计字段：{[row[0] for row in incomplete_edges]}")

    edge_date_mismatch = connection.execute(
        """SELECT e.edge_id FROM supply_chain_edges e
           JOIN sources s USING (source_id)
           WHERE e.disclosed_at <> s.disclosed_at"""
    ).fetchall()
    if edge_date_mismatch:
        issues.append(f"关系与来源披露日期不一致：{[row[0] for row in edge_date_mismatch]}")

    orphan_evidence = connection.execute(
        """SELECT evidence_id FROM source_evidence v
           WHERE NOT EXISTS (SELECT 1 FROM supply_chain_edges e WHERE e.edge_id=v.edge_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=v.source_id)
              OR length(v.content_sha256) <> 64"""
    ).fetchall()
    if orphan_evidence:
        issues.append(f"来源证据引用或哈希异常：{[row[0] for row in orphan_evidence]}")

    orphan_research_facts = connection.execute(
        """SELECT event_id FROM capex_events e
           WHERE NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=e.company_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=e.source_id)
           UNION ALL
           SELECT signal_id FROM fundamental_signals f
           WHERE NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=f.company_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=f.source_id)
           UNION ALL
           SELECT signal_id FROM expectation_signals e
           WHERE NOT EXISTS (SELECT 1 FROM security_master s WHERE s.security_id=e.security_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=e.source_id)
           UNION ALL
           SELECT signal_id FROM price_signals p
           WHERE NOT EXISTS (SELECT 1 FROM security_master s WHERE s.security_id=p.security_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=p.source_id)
           UNION ALL
           SELECT signal_id FROM valuation_signals v
           WHERE NOT EXISTS (SELECT 1 FROM security_master s WHERE s.security_id=v.security_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=v.source_id)
           UNION ALL
           SELECT evidence_id FROM company_exposure_evidence e
           WHERE NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=e.company_id)
              OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=e.source_id)"""
    ).fetchall()
    if orphan_research_facts:
        issues.append(f"研究事实存在孤儿引用：{[row[0] for row in orphan_research_facts]}")

    invalid_signal_contracts = connection.execute(
        """SELECT signal_id FROM fundamental_signals
           WHERE metric_name IS NULL OR metric_value IS NULL OR metric_unit IS NULL
              OR fiscal_period IS NULL OR comparison_type IS NULL
              OR comparison_period IS NULL OR actual_or_guidance IS NULL
           UNION ALL
           SELECT signal_id FROM expectation_signals
           WHERE previous_snapshot_at IS NULL OR previous_snapshot_at >= snapshot_at
              OR forecast_metric IS NULL OR forecast_period IS NULL OR forecast_unit IS NULL
              OR current_value IS NULL OR previous_value IS NULL OR revision_pct IS NULL
              OR consensus_source IS NULL
           UNION ALL
           SELECT signal_id FROM price_signals
           WHERE window_start IS NULL OR window_end IS NULL OR window_start > window_end
              OR return_type IS NULL OR raw_return IS NULL OR benchmark_return IS NULL
              OR excess_return IS NULL OR benchmark_id IS NULL
              OR abs(excess_return - (raw_return - benchmark_return)) > 0.000001
           UNION ALL
           SELECT signal_id FROM valuation_signals
           WHERE valuation_metric IS NULL OR valuation_value IS NULL OR forward_period IS NULL
              OR historical_percentile IS NULL OR historical_percentile NOT BETWEEN 0 AND 1"""
    ).fetchall()
    if invalid_signal_contracts:
        issues.append(f"Q3 数据合同异常：{[row[0] for row in invalid_signal_contracts]}")

    archive_issues = evidence_failures(connection)
    if archive_issues:
        issues.append(f"来源归档校验失败：{archive_issues}")

    invalid_scores = connection.execute(
        """SELECT company_id FROM company_exposures
           WHERE coalesce(capex_exposure, 0) NOT BETWEEN 0 AND 5
              OR coalesce(bottleneck, 0) NOT BETWEEN 0 AND 5
              OR coalesce(earnings_revision, 0) NOT BETWEEN 0 AND 5
              OR coalesce(profit_capture, 0) NOT BETWEEN 0 AND 5
              OR coalesce(priced_in, 0) NOT BETWEEN 0 AND 5"""
    ).fetchall()
    if invalid_scores:
        issues.append(f"暴露评分越界：{[row[0] for row in invalid_scores]}")

    return issues
