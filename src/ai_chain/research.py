from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import duckdb

from .as_of import as_of_timestamp


@dataclass(frozen=True)
class QueryResult:
    headers: list[str]
    rows: list[tuple[object, ...]]


def _result(
    connection: duckdb.DuckDBPyConnection, sql: str, params: list[object]
) -> QueryResult:
    cursor = connection.execute(sql, params)
    return QueryResult([column[0] for column in cursor.description], cursor.fetchall())


def capex_beneficiaries(
    connection: duckdb.DuckDBPyConnection,
    event_id: str,
    as_of: date | datetime,
) -> QueryResult:
    cutoff = as_of_timestamp(as_of)
    return _result(
        connection,
        """WITH RECURSIVE selected_event AS (
               SELECT * FROM capex_events
               WHERE event_id=? AND first_available_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
           ),
           paths AS (
               SELECT e.source_company_id AS root_company_id,
                      e.target_company_id AS beneficiary_company_id,
                      1 AS benefit_level, e.edge_id, e.relation_type, e.product,
                      e.disclosed_at, e.first_available_at, e.confidence, e.source_id,
                      e.source_company_id || ' -> ' || e.target_company_id AS transmission_path
               FROM supply_chain_edges e
               WHERE e.source_company_id=(SELECT company_id FROM selected_event)
                 AND e.first_available_at <= ?
                 AND e.valid_from <= CAST(? AS DATE)
                 AND (e.valid_to IS NULL OR e.valid_to >= CAST(? AS DATE))
                 AND (e.superseded_at IS NULL OR e.superseded_at > ?)
               UNION ALL
               SELECT p.root_company_id, e.target_company_id, p.benefit_level + 1,
                      e.edge_id, e.relation_type, e.product, e.disclosed_at,
                      e.first_available_at, least(p.confidence, e.confidence), e.source_id,
                      p.transmission_path || ' -> ' || e.target_company_id
               FROM paths p
               JOIN supply_chain_edges e ON e.source_company_id=p.beneficiary_company_id
               WHERE p.benefit_level < 2
                 AND e.target_company_id <> p.root_company_id
                 AND e.first_available_at <= ?
                 AND e.valid_from <= CAST(? AS DATE)
                 AND (e.valid_to IS NULL OR e.valid_to >= CAST(? AS DATE))
                 AND (e.superseded_at IS NULL OR e.superseded_at > ?)
           )
           SELECT ev.event_id, ev.company_id AS event_company_id,
                  root.company_name AS event_company_name, ev.fiscal_period,
                  ev.event_type, ev.guidance_low_millions, ev.guidance_high_millions,
                  ev.currency, ev.direction, ev.first_available_at AS event_first_available_at,
                  ev.source_id AS event_source_id, event_source.url AS event_source_url,
                  event_source.disclosed_at AS event_disclosed_at,
                  p.benefit_level, beneficiary.company_id AS beneficiary_company_id,
                  beneficiary.company_name AS beneficiary_company_name,
                  p.transmission_path, p.edge_id, p.relation_type, p.product,
                  p.disclosed_at AS relation_disclosed_at,
                  p.first_available_at AS relation_first_available_at,
                  p.confidence AS path_confidence,
                  p.source_id AS relation_source_id,
                  relation_source.url AS relation_source_url
           FROM selected_event ev
           JOIN company_master root ON root.company_id=ev.company_id
           JOIN sources event_source ON event_source.source_id=ev.source_id
             AND event_source.first_available_at <= ?
             AND (event_source.superseded_at IS NULL OR event_source.superseded_at > ?)
           JOIN paths p ON p.root_company_id=ev.company_id
           JOIN company_master beneficiary ON beneficiary.company_id=p.beneficiary_company_id
           JOIN sources relation_source ON relation_source.source_id=p.source_id
             AND relation_source.first_available_at <= ?
             AND (relation_source.superseded_at IS NULL OR relation_source.superseded_at > ?)
           ORDER BY p.benefit_level, beneficiary.company_id, p.edge_id""",
        [
            event_id, cutoff, cutoff,
            cutoff, cutoff, cutoff, cutoff,
            cutoff, cutoff, cutoff, cutoff,
            cutoff, cutoff, cutoff, cutoff,
        ],
    )


def exposure_classification(
    connection: duckdb.DuckDBPyConnection,
    company_id: str,
    as_of: date | datetime,
) -> QueryResult:
    cutoff = as_of_timestamp(as_of)
    return _result(
        connection,
        """WITH available_evidence AS (
               SELECT e.*, s.url AS source_url, s.disclosed_at
               FROM company_exposure_evidence e
               JOIN sources s USING (source_id)
               WHERE e.company_id=?
                 AND e.first_available_at <= ?
                 AND (e.superseded_at IS NULL OR e.superseded_at > ?)
                 AND s.first_available_at <= ?
                 AND (s.superseded_at IS NULL OR s.superseded_at > ?)
           ),
           evidence_rollup AS (
               SELECT company_id,
                      count(*) AS evidence_count,
                      count(*) FILTER (
                          WHERE evidence_type IN ('REVENUE','REVENUE_SHARE','ORDER','BACKLOG','SHIPMENT','DEPLOYMENT')
                            AND confidence >= 0.7
                      ) AS high_count,
                      count(*) FILTER (
                          WHERE evidence_type='NAMED_CUSTOMER'
                            AND customer_name IS NOT NULL AND confidence >= 0.7
                      ) AS named_count,
                      count(*) FILTER (
                          WHERE evidence_type IN ('PRODUCT_ONLY','MANAGEMENT_STATEMENT')
                            AND confidence >= 0.7
                      ) AS low_count,
                      count(*) FILTER (
                          WHERE evidence_type='INDIRECT_INDUSTRY_EXPOSURE' AND confidence >= 0.7
                      ) AS indirect_count,
                      string_agg(DISTINCT evidence_type, ' | ' ORDER BY evidence_type) AS evidence_types,
                      string_agg(DISTINCT coalesce(fiscal_period,
                          concat(coalesce(CAST(evidence_start AS VARCHAR), '?'), '/',
                                 coalesce(CAST(evidence_end AS VARCHAR), '?'))),
                          ' | ' ORDER BY coalesce(fiscal_period,
                          concat(coalesce(CAST(evidence_start AS VARCHAR), '?'), '/',
                                 coalesce(CAST(evidence_end AS VARCHAR), '?')))) AS evidence_periods,
                      string_agg(DISTINCT source_id, ' | ' ORDER BY source_id) AS source_ids,
                      string_agg(DISTINCT source_url, ' | ' ORDER BY source_url) AS source_urls,
                      string_agg(DISTINCT source_locator, ' | ' ORDER BY source_locator) AS source_locators,
                      string_agg(
                          concat(evidence_id, ':', evidence_type, ':', coalesce(product, ''),
                                 ':', coalesce(customer_name, ''), ':', coalesce(metric_name, '')),
                          ' | ' ORDER BY evidence_id
                      ) AS evidence_used,
                      max(first_available_at) AS classification_cutoff
               FROM available_evidence
               GROUP BY company_id
           )
           SELECT c.company_id, c.company_name,
                  CASE
                    WHEN er.company_id IS NULL THEN '待核验'
                    WHEN er.indirect_count > 0
                      AND (er.high_count + er.named_count + er.low_count) > 0 THEN '待核验'
                    WHEN er.high_count > 0 THEN '直接-高'
                    WHEN er.named_count > 0 THEN '直接-中'
                    WHEN er.low_count > 0 THEN '直接-低'
                    WHEN er.indirect_count > 0 THEN '间接'
                    ELSE '待核验'
                  END AS classification,
                  'exposure-evidence-v1' AS rule_version,
                  coalesce(er.evidence_count, 0) AS evidence_count,
                  er.evidence_types, er.evidence_periods, er.source_ids,
                  er.source_urls, er.source_locators, er.evidence_used,
                  r.strongest_bear_case,
                  er.classification_cutoff,
                  r.ai_revenue_exposure AS legacy_candidate_label
           FROM company_master c
           LEFT JOIN evidence_rollup er USING (company_id)
           LEFT JOIN latest_company_research(?) r USING (company_id)
           WHERE c.company_id=?""",
        [company_id, cutoff, cutoff, cutoff, cutoff, cutoff, company_id],
    )


def expectation_gap(
    connection: duckdb.DuckDBPyConnection,
    as_of: date | datetime,
    security_id: str | None = None,
) -> QueryResult:
    cutoff = as_of_timestamp(as_of)
    return _result(
        connection,
        """WITH latest_fundamental AS (
               SELECT * FROM fundamental_signals
               WHERE first_available_at <= ? AND snapshot_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY company_id ORDER BY snapshot_at DESC, first_available_at DESC, revision_id DESC
               ) = 1
           ),
           latest_expectation AS (
               SELECT * FROM expectation_signals
               WHERE first_available_at <= ? AND snapshot_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY security_id ORDER BY snapshot_at DESC, first_available_at DESC, revision_id DESC
               ) = 1
           ),
           latest_price AS (
               SELECT * FROM price_signals
               WHERE first_available_at <= ? AND snapshot_at <= ?
                 AND window_end <= CAST(? AS DATE)
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY security_id ORDER BY window_end DESC, snapshot_at DESC,
                                                    first_available_at DESC, revision_id DESC
               ) = 1
           ),
           latest_valuation AS (
               SELECT * FROM valuation_signals
               WHERE first_available_at <= ? AND snapshot_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY security_id ORDER BY snapshot_at DESC,
                                                    first_available_at DESC, revision_id DESC
               ) = 1
           ),
           joined AS (
               SELECT s.security_id, s.ticker, s.market, s.currency,
                      c.company_id, c.company_name,
                      f.signal_id AS fundamental_signal_id,
                      f.snapshot_at AS fundamental_snapshot_at,
                      f.metric_name, f.metric_value, f.metric_unit, f.fiscal_period,
                      f.comparison_type, f.comparison_period, f.actual_or_guidance,
                      f.source_id AS fundamental_source_id,
                      fs.url AS fundamental_source_url,
                      f.first_available_at AS fundamental_first_available_at,
                      e.signal_id AS expectation_signal_id,
                      e.snapshot_at AS expectation_snapshot_at,
                      e.previous_snapshot_at AS expectation_previous_snapshot_at,
                      e.forecast_metric, e.forecast_period, e.forecast_unit,
                      e.current_value, e.previous_value, e.revision_pct,
                      e.consensus_source, e.source_id AS expectation_source_id,
                      es.url AS expectation_source_url,
                      e.first_available_at AS expectation_first_available_at,
                      p.signal_id AS price_signal_id, p.snapshot_at AS price_snapshot_at,
                      p.window_start, p.window_end, p.return_type,
                      p.raw_return, p.benchmark_return, p.excess_return, p.benchmark_id,
                      p.source_id AS price_source_id, ps.url AS price_source_url,
                      p.first_available_at AS price_first_available_at,
                      v.signal_id AS valuation_signal_id,
                      v.snapshot_at AS valuation_snapshot_at,
                      v.valuation_metric, v.valuation_value, v.forward_period,
                      v.historical_percentile, v.source_id AS valuation_source_id,
                      vs.url AS valuation_source_url,
                      v.first_available_at AS valuation_first_available_at
               FROM security_master s
               JOIN company_master c USING (company_id)
               LEFT JOIN latest_fundamental f USING (company_id)
               LEFT JOIN latest_expectation e USING (security_id)
               LEFT JOIN latest_price p USING (security_id)
               LEFT JOIN latest_valuation v USING (security_id)
               LEFT JOIN sources fs ON fs.source_id=f.source_id AND fs.first_available_at <= ?
                 AND (fs.superseded_at IS NULL OR fs.superseded_at > ?)
               LEFT JOIN sources es ON es.source_id=e.source_id AND es.first_available_at <= ?
                 AND (es.superseded_at IS NULL OR es.superseded_at > ?)
               LEFT JOIN sources ps ON ps.source_id=p.source_id AND ps.first_available_at <= ?
                 AND (ps.superseded_at IS NULL OR ps.superseded_at > ?)
               LEFT JOIN sources vs ON vs.source_id=v.source_id AND vs.first_available_at <= ?
                 AND (vs.superseded_at IS NULL OR vs.superseded_at > ?)
               WHERE s.is_primary AND (? IS NULL OR s.security_id=?)
           )
           SELECT *,
                  CASE
                    WHEN fundamental_signal_id IS NULL OR expectation_signal_id IS NULL
                      OR price_signal_id IS NULL OR valuation_signal_id IS NULL THEN '数据不足'
                    WHEN metric_name <> forecast_metric THEN '数据不足'
                    WHEN fiscal_period <> forecast_period OR forecast_period <> forward_period THEN '数据不足'
                    WHEN metric_unit <> forecast_unit THEN '数据不足'
                    WHEN expectation_previous_snapshot_at IS NULL
                      OR expectation_previous_snapshot_at >= expectation_snapshot_at THEN '数据不足'
                    WHEN window_start IS NULL OR window_end IS NULL OR window_start > window_end
                      OR benchmark_id IS NULL OR benchmark_return IS NULL
                      OR excess_return IS NULL THEN '数据不足'
                    WHEN abs(excess_return - (raw_return - benchmark_return)) > 0.000001 THEN '数据不足'
                    WHEN valuation_metric IS NULL OR valuation_value IS NULL
                      OR historical_percentile IS NULL THEN '数据不足'
                    WHEN fundamental_source_url IS NULL OR expectation_source_url IS NULL
                      OR price_source_url IS NULL OR valuation_source_url IS NULL THEN '数据不足'
                    ELSE '可比较候选（非投资结论）'
                  END AS assessment,
                  CASE
                    WHEN fundamental_signal_id IS NOT NULL AND expectation_signal_id IS NOT NULL
                      AND price_signal_id IS NOT NULL AND valuation_signal_id IS NOT NULL
                      AND metric_name=forecast_metric
                      AND fiscal_period=forecast_period AND forecast_period=forward_period
                      AND metric_unit=forecast_unit
                      AND expectation_previous_snapshot_at IS NOT NULL
                      AND expectation_previous_snapshot_at < expectation_snapshot_at
                      AND window_start IS NOT NULL AND window_end IS NOT NULL
                      AND window_start <= window_end AND benchmark_id IS NOT NULL
                      AND benchmark_return IS NOT NULL AND excess_return IS NOT NULL
                      AND abs(excess_return - (raw_return - benchmark_return)) <= 0.000001
                      AND valuation_metric IS NOT NULL AND valuation_value IS NOT NULL
                      AND historical_percentile IS NOT NULL
                      AND fundamental_source_url IS NOT NULL
                      AND expectation_source_url IS NOT NULL
                      AND price_source_url IS NOT NULL
                      AND valuation_source_url IS NOT NULL
                    THEN true ELSE false
                  END AS is_watchlist,
                  CASE
                    WHEN fundamental_signal_id IS NOT NULL AND expectation_signal_id IS NOT NULL
                      AND price_signal_id IS NOT NULL AND valuation_signal_id IS NOT NULL
                    THEN greatest(fundamental_first_available_at,
                                  expectation_first_available_at,
                                  price_first_available_at,
                                  valuation_first_available_at)
                    ELSE NULL
                  END AS data_cutoff
           FROM joined
           ORDER BY company_id""",
        [
            cutoff, cutoff, cutoff,
            cutoff, cutoff, cutoff,
            cutoff, cutoff, cutoff, cutoff,
            cutoff, cutoff, cutoff,
            cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff,
            security_id, security_id,
        ],
    )
