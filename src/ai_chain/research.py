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
    company_id: str,
    as_of: date | datetime,
) -> QueryResult:
    cutoff = as_of_timestamp(as_of)
    return _result(
        connection,
        """WITH RECURSIVE latest_event AS (
               SELECT * FROM capex_events
               WHERE company_id=? AND first_available_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY company_id ORDER BY first_available_at DESC, revision_id DESC
               ) = 1
           ),
           paths AS (
               SELECT e.source_company_id AS root_company_id,
                      e.target_company_id AS beneficiary_company_id,
                      1 AS benefit_level,
                      e.edge_id,
                      e.relation_type,
                      e.product,
                      e.disclosed_at,
                      e.first_available_at,
                      e.confidence,
                      e.source_id,
                      e.source_company_id || ' -> ' || e.target_company_id AS transmission_path
               FROM supply_chain_edges e
               WHERE e.source_company_id=?
                 AND e.first_available_at <= ?
                 AND e.valid_from <= CAST(? AS DATE)
                 AND (e.valid_to IS NULL OR e.valid_to >= CAST(? AS DATE))
                 AND (e.superseded_at IS NULL OR e.superseded_at > ?)
               UNION ALL
               SELECT p.root_company_id,
                      e.target_company_id,
                      p.benefit_level + 1,
                      e.edge_id,
                      e.relation_type,
                      e.product,
                      e.disclosed_at,
                      e.first_available_at,
                      least(p.confidence, e.confidence),
                      e.source_id,
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
           SELECT le.event_id, le.fiscal_period, le.direction,
                  p.benefit_level, c.company_id, c.company_name,
                  p.transmission_path, p.relation_type, p.product,
                  p.disclosed_at, p.first_available_at, p.confidence,
                  p.source_id, s.url AS source_url
           FROM latest_event le
           JOIN paths p ON p.root_company_id=le.company_id
           JOIN company_master c ON c.company_id=p.beneficiary_company_id
           JOIN sources s ON s.source_id=p.source_id
           WHERE s.first_available_at <= ?
             AND (s.superseded_at IS NULL OR s.superseded_at > ?)
           ORDER BY p.benefit_level, c.company_id, p.edge_id""",
        [
            company_id, cutoff, cutoff,
            company_id, cutoff, cutoff, cutoff, cutoff,
            cutoff, cutoff, cutoff, cutoff,
            cutoff, cutoff,
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
        """SELECT r.company_id, c.company_name, r.as_of_date,
                  r.ai_revenue_exposure AS classification,
                  r.exposure_basis, r.core_product, r.major_customers,
                  r.strongest_bear_case, r.confidence,
                  r.source_id, s.url AS source_url,
                  s.disclosed_at, r.first_available_at, r.revision_id
           FROM latest_company_research(?) r
           JOIN company_master c USING (company_id)
           JOIN sources s USING (source_id)
           WHERE r.company_id=?
             AND s.first_available_at <= ?
             AND (s.superseded_at IS NULL OR s.superseded_at > ?)""",
        [cutoff, company_id, cutoff, cutoff],
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
               WHERE first_available_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY company_id ORDER BY snapshot_at DESC, first_available_at DESC, revision_id DESC
               ) = 1
           ),
           latest_expectation AS (
               SELECT * FROM expectation_signals
               WHERE first_available_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY security_id ORDER BY snapshot_at DESC, first_available_at DESC, revision_id DESC
               ) = 1
           ),
           latest_price AS (
               SELECT * FROM price_signals
               WHERE first_available_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY security_id ORDER BY snapshot_at DESC, first_available_at DESC, revision_id DESC
               ) = 1
           )
           SELECT s.security_id, s.ticker, s.market, s.currency,
                  c.company_id, c.company_name,
                  f.metric_name, f.metric_change,
                  e.revenue_revision, e.eps_revision, e.valuation_multiple,
                  p.price_return,
                  CASE
                    WHEN f.metric_change IS NULL
                      OR e.revenue_revision IS NULL
                      OR e.eps_revision IS NULL
                      OR e.valuation_multiple IS NULL
                      OR p.price_return IS NULL THEN '数据不足'
                    WHEN f.metric_change > greatest(e.revenue_revision, e.eps_revision)
                      AND p.price_return < f.metric_change THEN '预期差候选'
                    ELSE '未见正向预期差'
                  END AS assessment,
                  CASE
                    WHEN f.metric_change IS NOT NULL
                      AND e.revenue_revision IS NOT NULL
                      AND e.eps_revision IS NOT NULL
                      AND e.valuation_multiple IS NOT NULL
                      AND p.price_return IS NOT NULL
                      AND f.metric_change > greatest(e.revenue_revision, e.eps_revision)
                      AND p.price_return < f.metric_change THEN true
                    ELSE false
                  END AS is_watchlist,
                  greatest(f.first_available_at, e.first_available_at, p.first_available_at) AS data_cutoff
           FROM security_master s
           JOIN company_master c USING (company_id)
           LEFT JOIN latest_fundamental f USING (company_id)
           LEFT JOIN latest_expectation e USING (security_id)
           LEFT JOIN latest_price p USING (security_id)
           WHERE s.is_primary AND (? IS NULL OR s.security_id=?)
           ORDER BY c.company_id""",
        [cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, security_id, security_id],
    )
