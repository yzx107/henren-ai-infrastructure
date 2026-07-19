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
from ai_chain.cli import cmd_build
from ai_chain.db import initialize
from ai_chain.research import capex_beneficiaries, expectation_gap, exposure_classification
from ai_chain.validation import validate


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    path = tmp_path / "test.duckdb"
    initialize(path)
    return path


@pytest.fixture
def connection(seeded_db: Path):
    handle = duckdb.connect(str(seeded_db))
    try:
        yield handle
    finally:
        handle.close()


def test_scope_and_parameterized_universe_are_exact(connection) -> None:
    assert validate(connection, date(2026, 7, 19)) == []
    assert connection.execute("SELECT count(*) FROM company_master").fetchone()[0] == 30
    assert connection.execute("SELECT count(*) FROM supply_chain_edges").fetchone()[0] == 10
    assert connection.execute(
        "SELECT count(DISTINCT industry_layer) FROM company_master"
    ).fetchone()[0] == 4
    assert connection.execute(
        "SELECT count(*) FROM initial_universe_as_of(?)",
        [as_of_timestamp(date(2026, 7, 19))],
    ).fetchone()[0] == 30
    static_views = connection.execute(
        """SELECT count(*) FROM information_schema.views
           WHERE table_name IN ('initial_universe', 'opportunity_scores')"""
    ).fetchone()[0]
    assert static_views == 0


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
        """SELECT core_product FROM initial_universe_as_of(?)
           WHERE company='Alphabet'""",
        [as_of_timestamp(date(2026, 7, 19))],
    ).fetchall()
    assert len(late) == 1
    assert late[0][0] != "historical product"


def test_capex_queries_enforce_as_of_paths_and_provenance(connection) -> None:
    early = capex_beneficiaries(connection, "CLOUD_GOOG", date(2026, 2, 28))
    assert [(row[3], row[4]) for row in early.rows] == [(1, "CHIP_NVDA")]
    assert all(row[9] and row[12] and row[13] for row in early.rows)
    assert all(row[10] <= as_of_timestamp(date(2026, 2, 28)) for row in early.rows)

    late = capex_beneficiaries(connection, "CLOUD_GOOG", date(2026, 7, 19))
    assert {row[4] for row in late.rows if row[3] == 1} == {"CHIP_NVDA"}
    assert {row[4] for row in late.rows if row[3] == 2} == {
        "MEM_SKHYNIX", "OPT_COHERENT", "OPT_LUMENTUM"
    }


def test_future_relation_cannot_enter_historical_query(connection) -> None:
    connection.execute(
        """UPDATE supply_chain_edges
           SET first_available_at='2027-01-01T00:00:00+00:00'
           WHERE edge_id='E_NVDA_SKHYNIX'"""
    )
    rows = capex_beneficiaries(connection, "CLOUD_GOOG", date(2026, 7, 19)).rows
    assert "MEM_SKHYNIX" not in {row[4] for row in rows}


def test_exposure_classification_has_evidence_and_one_snapshot(connection) -> None:
    result = exposure_classification(connection, "CHIP_NVDA", date(2026, 7, 19))
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row[3] == "直接-高"
    assert row[4] and row[9] and row[10] and row[11]
    assert row[12] <= as_of_timestamp(date(2026, 7, 19))


def _insert_gap_inputs(connection, missing: str) -> None:
    connection.execute(
        """INSERT INTO fundamental_signals VALUES
           ('F_NVDA', 'CHIP_NVDA', '2026-07-18T00:00:00+00:00', 'revenue_growth',
            0.30, 'S_NVDA', '2026-07-18T00:00:00+00:00',
            '2026-07-18T00:01:00+00:00', 'v1', NULL)"""
    )
    if missing != "expectation":
        valuation = "NULL" if missing == "valuation" else "25.0"
        connection.execute(
            f"""INSERT INTO expectation_signals VALUES
                ('E_NVDA', 'US_NVDA', '2026-07-18T00:00:00+00:00', 0.10, 0.12,
                 {valuation}, 'S_NVDA', '2026-07-18T00:00:00+00:00',
                 '2026-07-18T00:01:00+00:00', 'v1', NULL)"""
        )
    if missing != "price":
        connection.execute(
            """INSERT INTO price_signals VALUES
               ('P_NVDA', 'US_NVDA', '2026-07-18T00:00:00+00:00', 0.05,
                'S_NVDA', '2026-07-18T00:00:00+00:00',
                '2026-07-18T00:01:00+00:00', 'v1', NULL)"""
        )


@pytest.mark.parametrize("missing", ["expectation", "valuation", "price"])
def test_missing_market_inputs_never_make_watchlist(connection, missing: str) -> None:
    _insert_gap_inputs(connection, missing)
    row = expectation_gap(connection, date(2026, 7, 19), "US_NVDA").rows[0]
    assert row[-3] == "数据不足"
    assert row[-2] is False


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
    early = connection.execute(
        "SELECT count(*) FROM opportunity_scores_as_of(?)",
        [as_of_timestamp(date(2026, 7, 18))],
    ).fetchone()[0]
    score = connection.execute(
        "SELECT opportunity_score FROM opportunity_scores_as_of(?)",
        [as_of_timestamp(date(2026, 7, 19))],
    ).fetchone()[0]
    assert early == 0
    assert score == pytest.approx(2.9)


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
            """UPDATE company_research_snapshot
               SET as_of_date='2027-01-01' WHERE company_id='CHIP_NVDA'"""
        )
    handle.close()
    args = Namespace(
        db=seeded_db,
        as_of=date(2026, 7, 19),
        output_root=tmp_path / failure,
        no_init=True,
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
