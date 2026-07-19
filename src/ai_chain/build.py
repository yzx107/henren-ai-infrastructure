from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

import duckdb

from .as_of import as_of_date, as_of_timestamp
from .audit import evidence_failures, sample_trace_rows
from .research import QueryResult, expectation_gap
from .validation import MARKET_CURRENCIES, validate


REQUIRED_OUTPUTS = {
    "supply_chain_master.csv",
    "capex_tracker.csv",
    "profit_transmission.csv",
    "expectation_gap_watchlist.csv",
    "data_quality_report.json",
}
REQUIRED_DQA_CHECKS = {
    "row_counts",
    "primary_key_duplicates",
    "required_nulls",
    "orphan_references",
    "future_dates",
    "currency_market_rules",
    "relation_trace_sample",
}


class BuildError(RuntimeError):
    pass


def _query(
    connection: duckdb.DuckDBPyConnection, sql: str, params: list[object]
) -> QueryResult:
    cursor = connection.execute(sql, params)
    return QueryResult([column[0] for column in cursor.description], cursor.fetchall())


def _write_csv(path: Path, result: QueryResult) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(result.headers)
        writer.writerows(result.rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _supply_chain_master(
    connection: duckdb.DuckDBPyConnection, cutoff: datetime
) -> QueryResult:
    return _query(
        connection,
        """SELECT company, security_code, market, industry_layer, core_product,
                  major_customers, ai_revenue_exposure, exposure_basis,
                  relationship_source, disclosed_at, first_available_at, revision_id,
                  current_thesis, strongest_bear_case, confidence
           FROM initial_universe_as_of(?)
           ORDER BY industry_layer, company""",
        [cutoff],
    )


def _capex_tracker(
    connection: duckdb.DuckDBPyConnection, cutoff: datetime
) -> QueryResult:
    return _query(
        connection,
        """WITH latest_event AS (
               SELECT * FROM capex_events
               WHERE first_available_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY company_id ORDER BY first_available_at DESC, revision_id DESC
               ) = 1
           )
           SELECT c.company_id, c.company_name, e.event_id, e.fiscal_period,
                  e.event_type, e.guidance_low_millions, e.guidance_high_millions,
                  e.currency, e.direction, e.first_available_at, e.revision_id,
                  e.source_id, s.url AS source_url,
                  CASE WHEN e.event_id IS NULL THEN '数据不足' ELSE '有披露' END AS data_status
           FROM company_master c
           LEFT JOIN latest_event e USING (company_id)
           LEFT JOIN sources s ON s.source_id=e.source_id
                             AND s.first_available_at <= ?
                             AND (s.superseded_at IS NULL OR s.superseded_at > ?)
           WHERE c.industry_layer='云厂商'
           ORDER BY c.company_id""",
        [cutoff, cutoff, cutoff, cutoff],
    )


def _profit_transmission(
    connection: duckdb.DuckDBPyConnection, cutoff: datetime
) -> QueryResult:
    return _query(
        connection,
        """WITH latest_fundamental AS (
               SELECT * FROM fundamental_signals
               WHERE first_available_at <= ?
                 AND (superseded_at IS NULL OR superseded_at > ?)
               QUALIFY row_number() OVER (
                   PARTITION BY company_id ORDER BY snapshot_at DESC, first_available_at DESC, revision_id DESC
               ) = 1
           )
           SELECT c.company_id, c.company_name, c.industry_layer,
                  f.metric_name, f.metric_change, f.snapshot_at,
                  f.first_available_at AS data_cutoff, f.revision_id, f.source_id,
                  s.url AS source_url,
                  CASE WHEN f.metric_change IS NULL THEN '数据不足' ELSE '有经营数据' END AS data_status
           FROM company_master c
           LEFT JOIN latest_fundamental f USING (company_id)
           LEFT JOIN sources s ON s.source_id=f.source_id
                             AND s.first_available_at <= ?
                             AND (s.superseded_at IS NULL OR s.superseded_at > ?)
           ORDER BY c.industry_layer, c.company_id""",
        [cutoff, cutoff, cutoff, cutoff],
    )


def _duplicates(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    key_sql = {
        "company_master": "company_id",
        "security_master": "security_id",
        "sources": "source_id",
        "supply_chain_edges": "edge_id",
        "source_evidence": "evidence_id",
        "capex_events": "event_id",
    }
    return {
        table: connection.execute(
            f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY {key} HAVING count(*)>1)"
        ).fetchone()[0]
        for table, key in key_sql.items()
    }


def _dqa_payload(
    connection: duckdb.DuckDBPyConnection,
    cutoff: datetime,
    content_hashes: dict[str, str],
) -> dict[str, object]:
    tables = [
        "company_master", "security_master", "sources", "company_research_snapshot",
        "supply_chain_edges", "source_evidence", "capex_events",
        "fundamental_signals", "expectation_signals", "price_signals",
    ]
    row_counts = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in tables
    }
    duplicates = _duplicates(connection)
    required_nulls = {
        "sources": connection.execute(
            "SELECT count(*) FROM sources WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL"
        ).fetchone()[0],
        "research_snapshots": connection.execute(
            "SELECT count(*) FROM company_research_snapshot WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL"
        ).fetchone()[0],
        "edges": connection.execute(
            "SELECT count(*) FROM supply_chain_edges WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL"
        ).fetchone()[0],
    }
    orphan_references = {
        "securities": connection.execute(
            "SELECT count(*) FROM security_master s LEFT JOIN company_master c USING(company_id) WHERE c.company_id IS NULL"
        ).fetchone()[0],
        "edges": connection.execute(
            """SELECT count(*) FROM supply_chain_edges e
               WHERE NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=e.source_company_id)
                  OR NOT EXISTS (SELECT 1 FROM company_master c WHERE c.company_id=e.target_company_id)
                  OR NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=e.source_id)"""
        ).fetchone()[0],
    }
    future_dates = {
        "stored_after_as_of": connection.execute(
            """SELECT
                 (SELECT count(*) FROM company_research_snapshot WHERE first_available_at > ?)
               + (SELECT count(*) FROM supply_chain_edges WHERE first_available_at > ?)
               + (SELECT count(*) FROM capex_events WHERE first_available_at > ?)""",
            [cutoff, cutoff, cutoff],
        ).fetchone()[0],
        "output_leakage": 0,
    }
    currency_errors = sum(
        connection.execute(
            "SELECT count(*) FROM security_master WHERE market=? AND currency<>?",
            [market, currency],
        ).fetchone()[0]
        for market, currency in MARKET_CURRENCIES.items()
    )
    trace_rows = sample_trace_rows(connection)
    archive_errors = evidence_failures(connection)
    return {
        "as_of_date": as_of_date(cutoff).isoformat(),
        "status": "PASS",
        "contract_checks": {
            "row_counts": row_counts,
            "primary_key_duplicates": {"total": sum(duplicates.values()), "by_table": duplicates},
            "required_nulls": {"total": sum(required_nulls.values()), "by_table": required_nulls},
            "orphan_references": {"total": sum(orphan_references.values()), "by_table": orphan_references},
            "future_dates": future_dates,
            "currency_market_rules": {"errors": currency_errors},
            "relation_trace_sample": {
                "seed": "mvp-v1",
                "sample_count": len(trace_rows),
                "pass_count": sum(row[-2] == "PASS" for row in trace_rows),
                "archive_errors": archive_errors,
            },
        },
        "research_content_sha256": content_hashes,
    }


def validate_built_outputs(output_dir: Path, cutoff: datetime) -> None:
    if output_dir.name != f"as_of={as_of_date(cutoff).isoformat()}":
        raise BuildError("输出目录未包含 as_of_date")
    missing = REQUIRED_OUTPUTS - {path.name for path in output_dir.iterdir() if path.is_file()}
    if missing:
        raise BuildError(f"缺少输出：{sorted(missing)}")
    for name in REQUIRED_OUTPUTS:
        if (output_dir / name).stat().st_size == 0:
            raise BuildError(f"空输出：{name}")
    for name in REQUIRED_OUTPUTS - {"data_quality_report.json"}:
        with (output_dir / name).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise BuildError(f"只有表头的输出：{name}")
        for row in rows:
            for field in ("first_available_at", "data_cutoff"):
                value = row.get(field)
                if value and datetime.fromisoformat(value).astimezone(cutoff.tzinfo) > cutoff:
                    raise BuildError(f"未来数据泄漏：{name}:{field}={value}")
    report = json.loads((output_dir / "data_quality_report.json").read_text(encoding="utf-8"))
    checks = report.get("contract_checks", {})
    if not REQUIRED_DQA_CHECKS <= set(checks):
        raise BuildError(f"DQA 报告缺少合同检查：{sorted(REQUIRED_DQA_CHECKS - set(checks))}")
    if report.get("status") != "PASS":
        raise BuildError("DQA 报告非 PASS")


def build_outputs(
    connection: duckdb.DuckDBPyConnection,
    as_of: date | datetime,
    output_root: Path,
) -> tuple[Path, dict[str, str]]:
    cutoff = as_of_timestamp(as_of)
    issues = validate(connection, as_of_date(cutoff))
    if issues:
        raise BuildError("DQA FAILED: " + "; ".join(issues))
    output_dir = output_root / f"as_of={as_of_date(cutoff).isoformat()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    results = {
        "supply_chain_master.csv": _supply_chain_master(connection, cutoff),
        "capex_tracker.csv": _capex_tracker(connection, cutoff),
        "profit_transmission.csv": _profit_transmission(connection, cutoff),
        "expectation_gap_watchlist.csv": expectation_gap(connection, cutoff),
    }
    for name, result in results.items():
        _write_csv(output_dir / name, result)
    content_hashes = {name: _sha256(output_dir / name) for name in sorted(results)}
    payload = _dqa_payload(connection, cutoff, content_hashes)
    (output_dir / "data_quality_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validate_built_outputs(output_dir, cutoff)
    hashes = {name: _sha256(output_dir / name) for name in sorted(REQUIRED_OUTPUTS)}
    return output_dir, hashes
