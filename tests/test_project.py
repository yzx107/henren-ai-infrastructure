from __future__ import annotations

import csv
import json
import shutil
from argparse import Namespace
from datetime import date
from pathlib import Path

import duckdb
import pytest

from ai_chain.acceptance import assess
from ai_chain.as_of import as_of_timestamp
from ai_chain.audit import evidence_failures, sample_trace_rows
from ai_chain.build import (
    REQUIRED_DQA_CHECKS,
    REQUIRED_OUTPUTS,
    BuildError,
    build_outputs,
    validate_built_outputs,
)
from ai_chain.cli import build_parser, cmd_build
from ai_chain.db import initialize_seed
from ai_chain.research import capex_beneficiaries, expectation_gap, exposure_classification
from ai_chain.validation import validate


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    path = tmp_path / "test.duckdb"
    initialize_seed(path)
    return path


@pytest.fixture
def connection(seeded_db: Path):
    handle = duckdb.connect(str(seeded_db))
    try:
        yield handle
    finally:
        handle.close()


def _dict_rows(result) -> list[dict[str, object]]:
    return [dict(zip(result.headers, row, strict=True)) for row in result.rows]


def test_scope_and_parameterized_universe_are_exact(connection) -> None:
    assert validate(connection, date(2026, 7, 19)) == []
    assert connection.execute("SELECT count(*) FROM company_master").fetchone()[0] == 30
    assert connection.execute("SELECT count(*) FROM supply_chain_edges").fetchone()[0] == 10
    assert connection.execute("SELECT count(*) FROM security_master").fetchone()[0] == 34
    assert connection.execute(
        "SELECT count(DISTINCT industry_layer) FROM company_master"
    ).fetchone()[0] == 4
    assert connection.execute(
        "SELECT count(*) FROM initial_universe_as_of(?)",
        [as_of_timestamp(date(2026, 7, 19))],
    ).fetchone()[0] == 30


def test_initial_universe_uses_latest_available_snapshot_once(connection) -> None:
    connection.execute(
        """INSERT INTO company_research_snapshot
           (company_id, as_of_date, core_product, major_customers,
            ai_revenue_exposure, exposure_basis, current_thesis,
            strongest_bear_case, source_id, confidence, first_available_at,
            ingested_at, revision_id, superseded_at)
           VALUES ('CLOUD_GOOG', '2026-02-20', 'historical product', 'historical customer',
                   '直接-高', 'historical evidence', 'historical thesis',
                   'historical bear case', 'S_GOOG', 0.8,
                   '2026-02-20T12:00:00+00:00', '2026-02-20T12:01:00+00:00', 'v0', NULL)"""
    )
    early = connection.execute(
        "SELECT company, core_product FROM initial_universe_as_of(?)",
        [as_of_timestamp(date(2026, 2, 28))],
    ).fetchall()
    assert early == [("Alphabet", "historical product")]
    late = connection.execute(
        "SELECT core_product FROM initial_universe_as_of(?) WHERE company='Alphabet'",
        [as_of_timestamp(date(2026, 7, 19))],
    ).fetchall()
    assert len(late) == 1 and late[0][0] != "historical product"


def test_capex_query_is_bound_to_explicit_event_and_paths(connection) -> None:
    event_id = "CAPEX_GOOG_FY2026"
    early = _dict_rows(capex_beneficiaries(connection, event_id, date(2026, 2, 28)))
    assert [(row["benefit_level"], row["beneficiary_company_id"]) for row in early] == [
        (1, "CHIP_NVDA")
    ]
    assert all(row["event_id"] == event_id for row in early)
    assert all(row["event_source_url"] and row["relation_source_url"] for row in early)
    assert all(
        row["relation_first_available_at"] <= as_of_timestamp(date(2026, 2, 28))
        for row in early
    )

    late = _dict_rows(capex_beneficiaries(connection, event_id, date(2026, 7, 19)))
    assert {row["beneficiary_company_id"] for row in late if row["benefit_level"] == 1} == {
        "CHIP_NVDA"
    }
    assert {row["beneficiary_company_id"] for row in late if row["benefit_level"] == 2} == {
        "MEM_SKHYNIX", "OPT_COHERENT", "OPT_LUMENTUM"
    }


def test_same_company_capex_events_are_independently_reproducible(connection) -> None:
    connection.execute(
        """INSERT INTO capex_events
           (event_id, company_id, fiscal_period, event_type,
            guidance_low_millions, guidance_high_millions, currency, direction,
            source_id, first_available_at, ingested_at, revision_id)
           VALUES ('CAPEX_GOOG_FY2025', 'CLOUD_GOOG', 'FY2025', 'GUIDANCE',
                   75000, 75000, 'USD', 'NEW_GUIDANCE', 'S_GOOG',
                   '2026-02-01T00:00:00+00:00', '2026-02-01T00:01:00+00:00', 'v1')"""
    )
    old_rows = _dict_rows(
        capex_beneficiaries(connection, "CAPEX_GOOG_FY2025", date(2026, 7, 19))
    )
    new_rows = _dict_rows(
        capex_beneficiaries(connection, "CAPEX_GOOG_FY2026", date(2026, 7, 19))
    )
    assert old_rows and new_rows
    assert {row["event_id"] for row in old_rows} == {"CAPEX_GOOG_FY2025"}
    assert {row["event_id"] for row in new_rows} == {"CAPEX_GOOG_FY2026"}
    assert {row["guidance_low_millions"] for row in old_rows} == {75000.0}
    assert {row["guidance_low_millions"] for row in new_rows} == {175000.0}


def _insert_exposure_evidence(
    connection,
    evidence_id: str,
    evidence_type: str,
    first_available_at: str,
    customer_name: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO company_exposure_evidence
           (evidence_id, company_id, evidence_type, product, customer_name, fiscal_period,
            source_id, source_locator, first_available_at, ingested_at,
            revision_id, confidence)
           VALUES (?, 'CHIP_NVDA', ?, 'Blackwell', ?, 'FY2027Q1', 'S_NVDA',
                   'fixed fixture locator', ?, ?, 'v1', 0.95)""",
        [
            evidence_id, evidence_type, customer_name,
            first_available_at, first_available_at,
        ],
    )


def test_no_exposure_evidence_returns_unverified(connection) -> None:
    row = _dict_rows(
        exposure_classification(connection, "CHIP_NVDA", date(2026, 7, 19))
    )[0]
    assert row["classification"] == "待核验"
    assert row["evidence_count"] == 0
    assert row["legacy_candidate_label"] == "直接-高"


def test_product_evidence_cannot_claim_high_exposure(connection) -> None:
    _insert_exposure_evidence(
        connection, "EE_PRODUCT", "PRODUCT_ONLY", "2026-07-18T00:00:00+00:00"
    )
    row = _dict_rows(
        exposure_classification(connection, "CHIP_NVDA", date(2026, 7, 19))
    )[0]
    assert row["classification"] == "直接-低"
    assert row["classification"] != row["legacy_candidate_label"]
    assert row["evidence_types"] == "PRODUCT_ONLY"
    assert row["evidence_periods"] == "FY2027Q1"
    assert row["source_ids"] == "S_NVDA"
    assert row["source_locators"] and row["strongest_bear_case"]


def test_future_exposure_evidence_cannot_enter_history(connection) -> None:
    _insert_exposure_evidence(
        connection, "EE_FUTURE", "DEPLOYMENT", "2027-01-01T00:00:00+00:00"
    )
    row = _dict_rows(
        exposure_classification(connection, "CHIP_NVDA", date(2026, 7, 19))
    )[0]
    assert row["classification"] == "待核验"
    assert row["evidence_count"] == 0


@pytest.mark.parametrize(
    ("evidence_type", "customer_name", "expected"),
    [("NAMED_CUSTOMER", "Microsoft", "直接-中"), ("DEPLOYMENT", None, "直接-高")],
)
def test_exposure_rule_respects_evidence_strength(
    connection, evidence_type: str, customer_name: str | None, expected: str
) -> None:
    _insert_exposure_evidence(
        connection,
        "EE_STRENGTH",
        evidence_type,
        "2026-07-18T00:00:00+00:00",
        customer_name,
    )
    row = _dict_rows(
        exposure_classification(connection, "CHIP_NVDA", date(2026, 7, 19))
    )[0]
    assert row["classification"] == expected


def _insert_comparable_fixture(
    connection,
    *,
    expectation_period: str = "FY2027Q1",
    expectation_unit: str = "USD_BN",
    include_price: bool = True,
    include_valuation: bool = True,
    first_available_at: str = "2026-07-18T00:00:00+00:00",
) -> None:
    connection.execute(
        """INSERT INTO fundamental_signals
           (signal_id, company_id, snapshot_at, metric_name, metric_value,
            metric_unit, fiscal_period, comparison_type, comparison_period,
            actual_or_guidance, source_id, first_available_at, ingested_at, revision_id)
           VALUES ('F_NVDA', 'CHIP_NVDA', '2026-07-18T00:00:00+00:00',
                   'REVENUE', 40.0, 'USD_BN', 'FY2027Q1', 'YOY', 'FY2026Q1',
                   'ACTUAL', 'S_NVDA', ?, ?, 'v1')""",
        [first_available_at, first_available_at],
    )
    connection.execute(
        """INSERT INTO expectation_signals
           (signal_id, security_id, snapshot_at, forecast_metric, forecast_period,
            previous_snapshot_at, forecast_unit, current_value, previous_value, revision_pct,
            consensus_source, source_id, first_available_at, ingested_at, revision_id)
           VALUES ('E_NVDA', 'US_NVDA', '2026-07-17T00:00:00+00:00',
                   'REVENUE', ?, '2026-06-17T00:00:00+00:00', ?, 39.0, 38.0, 0.026315789,
                   'FIXED_FIXTURE', 'S_NVDA', ?, ?, 'v1')""",
        [expectation_period, expectation_unit, first_available_at, first_available_at],
    )
    if include_price:
        connection.execute(
            """INSERT INTO price_signals
               (signal_id, security_id, snapshot_at, window_start, window_end,
                return_type, raw_return, benchmark_return, excess_return,
                benchmark_id, source_id, first_available_at, ingested_at, revision_id)
               VALUES ('P_NVDA', 'US_NVDA', '2026-07-18T00:00:00+00:00',
                       '2026-07-01', '2026-07-18', 'TOTAL_RETURN', 0.08, 0.03, 0.05,
                       'SP500', 'S_NVDA', ?, ?, 'v1')""",
            [first_available_at, first_available_at],
        )
    if include_valuation:
        connection.execute(
            """INSERT INTO valuation_signals
               (signal_id, security_id, snapshot_at, valuation_metric,
                valuation_value, forward_period, historical_percentile,
                source_id, first_available_at, ingested_at, revision_id)
               VALUES ('V_NVDA', 'US_NVDA', '2026-07-18T00:00:00+00:00',
                       'FORWARD_PE', 25.0, 'FY2027Q1', 0.60, 'S_NVDA', ?, ?, 'v1')""",
            [first_available_at, first_available_at],
        )


def test_complete_same_basis_q3_fixture_is_comparable_only(connection) -> None:
    _insert_comparable_fixture(connection)
    row = _dict_rows(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
    assert row["assessment"] == "可比较候选（非投资结论）"
    assert row["is_watchlist"] is True
    for field in (
        "fundamental_snapshot_at", "fiscal_period", "metric_unit",
        "expectation_snapshot_at", "expectation_previous_snapshot_at",
        "forecast_period", "forecast_unit",
        "window_start", "window_end", "benchmark_id", "valuation_metric",
        "fundamental_source_url", "expectation_source_url",
        "price_source_url", "valuation_source_url",
    ):
        assert row[field] is not None


def test_invalid_expectation_revision_window_is_not_q3_candidate(connection) -> None:
    _insert_comparable_fixture(connection)
    connection.execute(
        """UPDATE expectation_signals
           SET previous_snapshot_at=snapshot_at WHERE signal_id='E_NVDA'"""
    )
    row = _dict_rows(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
    assert row["assessment"] == "数据不足"
    assert row["is_watchlist"] is False


@pytest.mark.parametrize(
    ("period", "unit"),
    [("FY2028Q1", "USD_BN"), ("FY2027Q1", "PERCENT")],
)
def test_mismatched_period_or_unit_is_not_q3_candidate(connection, period: str, unit: str) -> None:
    _insert_comparable_fixture(
        connection, expectation_period=period, expectation_unit=unit
    )
    row = _dict_rows(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
    assert row["assessment"] == "数据不足"
    assert row["is_watchlist"] is False


@pytest.mark.parametrize("missing", ["price", "valuation"])
def test_missing_window_benchmark_or_valuation_is_not_q3_candidate(
    connection, missing: str
) -> None:
    _insert_comparable_fixture(
        connection,
        include_price=missing != "price",
        include_valuation=missing != "valuation",
    )
    row = _dict_rows(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
    assert row["assessment"] == "数据不足"
    assert row["is_watchlist"] is False


def test_price_contract_rejects_missing_benchmark(connection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        connection.execute(
            """INSERT INTO price_signals
               (signal_id, security_id, snapshot_at, window_start, window_end,
                return_type, raw_return, benchmark_return, excess_return,
                benchmark_id, source_id, first_available_at, ingested_at, revision_id)
               VALUES ('P_BAD', 'US_NVDA', '2026-07-18T00:00:00+00:00',
                       '2026-07-01', '2026-07-18', 'TOTAL_RETURN', 0.08, 0.03, 0.05,
                       NULL, 'S_NVDA', '2026-07-18T00:00:00+00:00',
                       '2026-07-18T00:01:00+00:00', 'v1')"""
        )


def test_future_q3_data_is_not_candidate(connection) -> None:
    _insert_comparable_fixture(
        connection, first_available_at="2027-01-01T00:00:00+00:00"
    )
    row = _dict_rows(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
    assert row["assessment"] == "数据不足"
    assert row["is_watchlist"] is False


def test_opportunity_score_is_point_in_time(connection) -> None:
    connection.execute(
        """INSERT INTO company_exposures
           (company_id, as_of_date, capex_exposure, bottleneck, earnings_revision,
            profit_capture, priced_in, confidence, source_id, analyst_note,
            first_available_at, ingested_at, revision_id)
           VALUES ('CHIP_NVDA', '2026-07-19', 5, 4, 3, 5, 5, 0.8,
                   'S_NVDA', 'test', '2026-07-19T10:00:00+00:00',
                   '2026-07-19T10:01:00+00:00', 'v1')"""
    )
    assert connection.execute(
        "SELECT count(*) FROM opportunity_scores_as_of(?)",
        [as_of_timestamp(date(2026, 7, 18))],
    ).fetchone()[0] == 0
    score = connection.execute(
        "SELECT opportunity_score FROM opportunity_scores_as_of(?)",
        [as_of_timestamp(date(2026, 7, 19))],
    ).fetchone()[0]
    assert score == pytest.approx(2.9)


def test_supply_chain_master_is_edge_level_and_traceable(connection, tmp_path: Path) -> None:
    output_dir, _ = build_outputs(connection, date(2026, 7, 19), tmp_path)
    with (output_dir / "supply_chain_master.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 10
    assert len({row["edge_id"] for row in rows}) == 10
    assert all(
        row["source_company_id"] and row["target_company_id"]
        and row["source_locator"] and row["archived_path"]
        and row["content_sha256"] and row["auditor_result"] == "PASS"
        for row in rows
    )
    assert "company" not in rows[0]


def test_future_edge_is_excluded_from_supply_chain_output(connection, tmp_path: Path) -> None:
    connection.execute(
        """UPDATE supply_chain_edges
           SET first_available_at='2027-01-01T00:00:00+00:00',
               ingested_at='2027-01-01T00:01:00+00:00'
           WHERE edge_id='E_NVDA_SKHYNIX'"""
    )
    output_dir, _ = build_outputs(connection, date(2026, 7, 19), tmp_path)
    with (output_dir / "supply_chain_master.csv").open(encoding="utf-8", newline="") as handle:
        edge_ids = {row["edge_id"] for row in csv.DictReader(handle)}
    assert "E_NVDA_SKHYNIX" not in edge_ids
    assert len(edge_ids) == 9


def test_build_recreates_five_nonempty_deterministic_outputs(connection, tmp_path: Path) -> None:
    first_dir, first_hashes = build_outputs(connection, date(2026, 7, 19), tmp_path)
    assert first_dir.name == "as_of=2026-07-19"
    assert set(first_hashes) == REQUIRED_OUTPUTS
    assert all((first_dir / name).stat().st_size > 0 for name in REQUIRED_OUTPUTS)
    report = json.loads((first_dir / "data_quality_report.json").read_text())
    assert REQUIRED_DQA_CHECKS <= set(report["contract_checks"])
    shutil.rmtree(first_dir)
    second_dir, second_hashes = build_outputs(connection, date(2026, 7, 19), tmp_path)
    assert first_hashes == second_hashes
    assert second_dir.exists()


def test_cli_build_does_not_reset_existing_database(seeded_db: Path, tmp_path: Path) -> None:
    handle = duckdb.connect(str(seeded_db))
    handle.execute(
        """INSERT INTO capex_events
           (event_id, company_id, fiscal_period, event_type,
            guidance_low_millions, guidance_high_millions, currency, direction,
            source_id, first_available_at, ingested_at, revision_id)
           VALUES ('CAPEX_PRESERVE', 'CLOUD_GOOG', 'FY2025', 'GUIDANCE',
                   1, 1, 'USD', 'TEST', 'S_GOOG',
                   '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00', 'v1')"""
    )
    before = {
        table: handle.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("company_master", "security_master", "supply_chain_edges", "capex_events")
    }
    handle.close()
    args = Namespace(
        db=seeded_db, as_of=date(2026, 7, 19), output_root=tmp_path / "build"
    )
    assert cmd_build(args) == 0
    handle = duckdb.connect(str(seeded_db))
    after = {
        table: handle.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert handle.execute(
        "SELECT count(*) FROM capex_events WHERE event_id='CAPEX_PRESERVE'"
    ).fetchone()[0] == 1
    handle.close()
    assert after == before


def test_cli_build_missing_database_returns_nonzero(tmp_path: Path) -> None:
    missing_db = tmp_path / "missing" / "never-created.duckdb"
    args = Namespace(
        db=missing_db, as_of=date(2026, 7, 19), output_root=tmp_path / "outputs"
    )
    assert cmd_build(args) != 0
    assert not missing_db.exists()


def test_seed_rebuild_is_an_explicit_command() -> None:
    args = build_parser().parse_args(["init-seed"])
    assert args.command == "init-seed"
    assert args.func.__name__ == "cmd_init_seed"


@pytest.mark.parametrize("empty_bytes", [b"", b"only_header\n"])
def test_empty_output_cannot_pass(connection, tmp_path: Path, empty_bytes: bytes) -> None:
    output_dir, _ = build_outputs(connection, date(2026, 7, 19), tmp_path)
    (output_dir / "capex_tracker.csv").write_bytes(empty_bytes)
    with pytest.raises(BuildError):
        validate_built_outputs(output_dir, as_of_timestamp(date(2026, 7, 19)))


def test_future_output_cannot_pass(connection, tmp_path: Path) -> None:
    output_dir, _ = build_outputs(connection, date(2026, 7, 19), tmp_path)
    path = output_dir / "supply_chain_master.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0])
    rows[0]["first_available_at"] = "2027-01-01T00:00:00+00:00"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(BuildError, match="未来数据泄漏"):
        validate_built_outputs(output_dir, as_of_timestamp(date(2026, 7, 19)))


@pytest.mark.parametrize("failure", ["dqa", "future"])
def test_unified_build_returns_nonzero_on_invalid_data(
    seeded_db: Path, tmp_path: Path, failure: str
) -> None:
    handle = duckdb.connect(str(seeded_db))
    if failure == "dqa":
        handle.execute("UPDATE security_master SET currency='USD' WHERE market='A'")
    else:
        handle.execute(
            "UPDATE company_research_snapshot SET as_of_date='2027-01-01' WHERE company_id='CHIP_NVDA'"
        )
    handle.close()
    args = Namespace(
        db=seeded_db, as_of=date(2026, 7, 19), output_root=tmp_path / failure
    )
    assert cmd_build(args) != 0


def test_acceptance_reports_fail_instead_of_crashing_on_bad_data(connection, tmp_path: Path) -> None:
    connection.execute("DELETE FROM capex_events")
    checks = assess(connection, tmp_path, date(2026, 7, 19))
    assert [check.check_id for check in checks] == ["AC1", "AC2", "AC3", "AC4", "AC5"]
    assert not checks[3].passed


def test_trace_audit_rejects_hash_mismatch(connection, tmp_path: Path) -> None:
    assert len(sample_trace_rows(connection)) == 10
    connection.execute(
        "UPDATE source_evidence SET content_sha256=repeat('0', 64) WHERE edge_id='E_GOOG_NVDA'"
    )
    assert "E_GOOG_NVDA: SHA-256 mismatch" in evidence_failures(connection)
    checks = assess(connection, tmp_path, date(2026, 7, 19))
    assert not checks[1].passed
    assert not checks[4].passed


def test_full_acceptance_passes_fixed_fixture(connection, tmp_path: Path) -> None:
    checks = assess(connection, tmp_path, date(2026, 7, 19))
    assert all(check.passed for check in checks), checks
