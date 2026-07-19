from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .audit import evidence_failures, sample_trace_rows


EXPECTED_LAYERS = {"云厂商", "GPU/ASIC", "HBM与先进封装", "网络与光互联"}
MARKET_CURRENCIES = {"A": "CNY", "H": "HKD", "US": "USD"}
REQUIRED_OUTPUTS = {
    "supply_chain_master.csv",
    "capex_tracker.csv",
    "profit_transmission.csv",
    "expectation_gap_watchlist.csv",
    "data_quality_report.json",
}


@dataclass(frozen=True)
class AcceptanceCheck:
    check_id: str
    title: str
    passed: bool
    detail: str


def _objects(connection: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }


def assess(
    connection: duckdb.DuckDBPyConnection, output_dir: Path
) -> list[AcceptanceCheck]:
    orphan_securities = connection.execute(
        """SELECT count(*) FROM security_master s
           LEFT JOIN company_master c USING (company_id) WHERE c.company_id IS NULL"""
    ).fetchone()[0]
    bad_primary = connection.execute(
        """SELECT count(*) FROM (
               SELECT company_id FROM security_master
               GROUP BY company_id HAVING count(*) FILTER (WHERE is_primary) <> 1
           )"""
    ).fetchone()[0]
    bad_currency = sum(
        connection.execute(
            "SELECT count(*) FROM security_master WHERE market=? AND currency<>?",
            [market, currency],
        ).fetchone()[0]
        for market, currency in MARKET_CURRENCIES.items()
    )
    mapped_companies = connection.execute(
        "SELECT count(DISTINCT company_id) FROM security_master"
    ).fetchone()[0]
    company_count = connection.execute("SELECT count(*) FROM company_master").fetchone()[0]
    ac1 = orphan_securities == bad_primary == bad_currency == 0 and mapped_companies == company_count

    edge_count = connection.execute("SELECT count(*) FROM supply_chain_edges").fetchone()[0]
    incomplete_edges = connection.execute(
        """SELECT count(*) FROM supply_chain_edges
           WHERE source_id IS NULL OR disclosed_at IS NULL OR valid_from IS NULL
              OR confidence IS NULL"""
    ).fetchone()[0]
    objects = _objects(connection)
    evidence_count = 0
    trace_rows: list[tuple[object, ...]] = []
    evidence_issues = ["source_evidence table missing"]
    if "source_evidence" in objects:
        evidence_count = connection.execute(
            """SELECT count(DISTINCT edge_id) FROM source_evidence
               WHERE source_locator IS NOT NULL
                 AND content_sha256 IS NOT NULL AND archived_path IS NOT NULL"""
        ).fetchone()[0]
        trace_rows = sample_trace_rows(connection)
        evidence_issues = evidence_failures(connection)
    ac2 = (
        edge_count >= 10
        and incomplete_edges == 0
        and evidence_count >= 10
        and len(trace_rows) == 10
        and not evidence_issues
    )

    layers = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT industry_layer FROM company_master"
        ).fetchall()
    }
    ac3 = company_count == 30 and layers == EXPECTED_LAYERS

    research_contracts = {
        "capex_beneficiary_map",
        "exposure_classification",
        "expectation_gap_watchlist",
    }
    ac4 = research_contracts <= objects

    present_outputs = {path.name for path in output_dir.glob("*") if path.is_file()}
    ac5 = REQUIRED_OUTPUTS <= present_outputs

    return [
        AcceptanceCheck("AC1", "统一公司与证券主表", ac1, f"companies={company_count}, mapped={mapped_companies}, mapping_errors={orphan_securities + bad_primary + bad_currency}"),
        AcceptanceCheck("AC2", "产业链关系可追溯", ac2, f"edges={edge_count}, incomplete={incomplete_edges}, archived_evidence={evidence_count}, fixed_sample={len(trace_rows)}/10, evidence_errors={len(evidence_issues)}"),
        AcceptanceCheck("AC3", "严格覆盖 30 家四层", ac3, f"companies={company_count}, layers={sorted(layers)}"),
        AcceptanceCheck("AC4", "回答三个投资问题", ac4, f"missing_contracts={sorted(research_contracts - objects)}"),
        AcceptanceCheck("AC5", "重复生成五个输出", ac5, f"missing_outputs={sorted(REQUIRED_OUTPUTS - present_outputs)}"),
    ]
