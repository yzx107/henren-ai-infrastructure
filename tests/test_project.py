from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from ai_chain.acceptance import assess
from ai_chain.audit import evidence_failures, sample_trace_rows
from ai_chain.db import initialize
from ai_chain.validation import validate


def test_seed_database_passes_dqa(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    counts = initialize(db_path)
    assert counts["company_master"] == 30
    assert counts["source_evidence"] == 10
    connection = duckdb.connect(str(db_path))
    try:
        assert validate(connection, date(2026, 7, 19)) == []
        assert connection.execute("SELECT count(*) FROM initial_universe").fetchone()[0] == 30
    finally:
        connection.close()


def test_opportunity_score_formula(tmp_path: Path) -> None:
    db_path = tmp_path / "score.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            """INSERT INTO company_exposures
               VALUES ('CHIP_NVDA', '2026-07-19', 5, 4, 3, 5, 5, 0.8, 'S_NVDA', 'test')"""
        )
        score = connection.execute(
            "SELECT opportunity_score FROM opportunity_scores WHERE company_id='CHIP_NVDA'"
        ).fetchone()[0]
        assert score == 2.9
    finally:
        connection.close()


def test_rank_excludes_incomplete_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "incomplete.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            """INSERT INTO company_exposures
               (company_id, as_of_date, capex_exposure, source_id)
               VALUES ('CHIP_NVDA', '2026-07-19', 5, 'S_NVDA')"""
        )
        assert connection.execute("SELECT count(*) FROM opportunity_scores").fetchone()[0] == 0
    finally:
        connection.close()


def test_acceptance_contract_has_exactly_five_gates(tmp_path: Path) -> None:
    db_path = tmp_path / "acceptance.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        checks = assess(connection, tmp_path / "outputs")
        assert [check.check_id for check in checks] == ["AC1", "AC2", "AC3", "AC4", "AC5"]
        assert all(isinstance(check.passed, bool) for check in checks)
    finally:
        connection.close()


def test_acceptance_detects_scope_drift(tmp_path: Path) -> None:
    db_path = tmp_path / "scope.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("DELETE FROM company_master WHERE company_id='CLOUD_MSFT'")
        scope_check = assess(connection, tmp_path / "outputs")[2]
        assert scope_check.check_id == "AC3"
        assert not scope_check.passed
    finally:
        connection.close()


def test_trace_sample_is_deterministic_and_verified(tmp_path: Path) -> None:
    db_path = tmp_path / "trace.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        first = sample_trace_rows(connection)
        second = sample_trace_rows(connection)
        assert len(first) == 10
        assert [row[0] for row in first] == [row[0] for row in second]
        assert all(row[-2] == "PASS" for row in first)
        assert evidence_failures(connection) == []
        assert assess(connection, tmp_path / "outputs")[1].passed
    finally:
        connection.close()


def test_trace_audit_rejects_hash_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "trace-bad.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            "UPDATE source_evidence SET content_sha256=repeat('0', 64) WHERE edge_id='E_GOOG_NVDA'"
        )
        assert "E_GOOG_NVDA: SHA-256 mismatch" in evidence_failures(connection)
        assert not assess(connection, tmp_path / "outputs")[1].passed
    finally:
        connection.close()


def test_dqa_fails_when_trace_sample_is_not_complete(tmp_path: Path) -> None:
    db_path = tmp_path / "trace-count.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("DELETE FROM supply_chain_edges WHERE edge_id='E_GOOG_NVDA'")
        issues = validate(connection, date(2026, 7, 19))
        assert any("sample=9/10" in issue for issue in issues)
    finally:
        connection.close()


def test_dqa_fails_when_blocking_gate_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "blocking.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            """INSERT INTO company_exposures
               (company_id, as_of_date, capex_exposure, source_id)
               VALUES ('CHIP_AMD', '2026-07-19', 5, 'S_AMD')"""
        )
        # Force the downstream ranking to be unsafe so DQA proves the blocking gate is enforced.
        connection.execute(
            """CREATE OR REPLACE VIEW opportunity_scores AS
               SELECT company_id, NULL AS company_name, NULL AS industry_layer, as_of_date,
                      0 AS opportunity_score, confidence, analyst_note
               FROM company_exposures"""
        )
        issues = validate(connection, date(2026, 7, 19))
        assert any("硬闸门校验失败" in issue and "五项评分不全却进入排名" in issue for issue in issues)
    finally:
        connection.close()


def test_research_outputs_do_not_include_future_windows(tmp_path: Path) -> None:
    db_path = tmp_path / "future-output.duckdb"
    initialize(db_path)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            """INSERT INTO company_research_snapshot
               VALUES ('CHIP_NVDA', '2099-01-01', 'future product', 'future customers',
                       '直接-高', 'future basis', 'future thesis', 'future bear', 'S_NVDA', 0.9)"""
        )
        row = connection.execute(
            "SELECT core_product, as_of_date FROM initial_universe WHERE company='NVIDIA'"
        ).fetchone()
        assert row[0] != "future product"
        assert row[1] <= date.today()

        connection.execute(
            """INSERT INTO company_exposures
               VALUES ('CHIP_NVDA', '2099-01-01', 5, 5, 5, 5, 0, 0.9, 'S_NVDA', 'future score')"""
        )
        assert connection.execute(
            "SELECT count(*) FROM opportunity_scores WHERE as_of_date > current_date"
        ).fetchone()[0] == 0
    finally:
        connection.close()
