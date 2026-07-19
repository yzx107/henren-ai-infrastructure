from __future__ import annotations

import csv
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

from .as_of import as_of_timestamp
from .audit import evidence_failures, sample_trace_rows
from .build import (
    REQUIRED_DQA_CHECKS,
    REQUIRED_OUTPUTS,
    BuildError,
    build_outputs,
    validate_built_outputs,
)
from .research import capex_beneficiaries, expectation_gap, exposure_classification


EXPECTED_LAYERS = {"云厂商", "GPU/ASIC", "HBM与先进封装", "网络与光互联"}
MARKET_CURRENCIES = {"A": "CNY", "H": "HKD", "US": "USD"}


@dataclass(frozen=True)
class AcceptanceCheck:
    check_id: str
    title: str
    passed: bool
    detail: str


def _row_dicts(result) -> list[dict[str, object]]:
    return [dict(zip(result.headers, row, strict=True)) for row in result.rows]


def _ac4_failures(connection: duckdb.DuckDBPyConnection) -> list[str]:
    failures: list[str] = []
    event_id = "CAPEX_GOOG_FY2026"
    early = _row_dicts(capex_beneficiaries(connection, event_id, date(2026, 2, 28)))
    if len(early) != 1 or early[0]["benefit_level"] != 1 \
            or early[0]["beneficiary_company_id"] != "CHIP_NVDA":
        failures.append("as_of 未把早期结果限制为 NVIDIA 直接路径")
    if any(row["relation_first_available_at"] > as_of_timestamp(date(2026, 2, 28)) for row in early):
        failures.append("早期 CapEx 查询泄漏未来关系")
    capex_required = {
        "event_id", "fiscal_period", "event_type", "guidance_low_millions",
        "guidance_high_millions", "currency", "direction",
        "event_first_available_at", "event_source_url",
        "relation_disclosed_at", "relation_source_url",
    }
    if not early or not capex_required <= set(early[0]) \
            or any(row["event_id"] != event_id for row in early):
        failures.append("CapEx 路径未绑定指定 event_id 或缺事件/关系来源")

    late = _row_dicts(capex_beneficiaries(connection, event_id, date(2026, 7, 19)))
    direct = {row["beneficiary_company_id"] for row in late if row["benefit_level"] == 1}
    secondary = {row["beneficiary_company_id"] for row in late if row["benefit_level"] == 2}
    if direct != {"CHIP_NVDA"}:
        failures.append(f"直接受益路径错误：{sorted(direct)}")
    if secondary != {"MEM_SKHYNIX", "OPT_COHERENT", "OPT_LUMENTUM"}:
        failures.append(f"二级受益路径错误：{sorted(secondary)}")

    no_evidence = _row_dicts(
        exposure_classification(connection, "CHIP_INTC", date(2026, 7, 19))
    )[0]
    if no_evidence["classification"] != "待核验" or no_evidence["evidence_count"] != 0:
        failures.append("无结构化收入证据时未返回待核验")

    connection.execute("BEGIN")
    try:
        connection.execute(
            """INSERT INTO company_exposure_evidence
               (evidence_id, company_id, evidence_type, product, fiscal_period,
                source_id, source_locator, first_available_at, ingested_at,
                revision_id, confidence)
               VALUES
               ('AC4_PRODUCT', 'CHIP_NVDA', 'PRODUCT_ONLY', 'Blackwell', 'FY2027Q1',
                'S_NVDA', 'fixed fixture: product section',
                '2026-05-20T23:59:59+00:00', '2026-07-19T23:59:59+00:00', 'v1', 0.95),
               ('AC4_FUTURE', 'CHIP_NVDA', 'DEPLOYMENT', 'Blackwell', 'FY2028',
                'S_NVDA', 'fixed fixture: future deployment',
                '2027-01-01T00:00:00+00:00', '2027-01-01T00:01:00+00:00', 'v1', 0.95)"""
        )
        exposure = _row_dicts(
            exposure_classification(connection, "CHIP_NVDA", date(2026, 7, 19))
        )[0]
        if exposure["classification"] != "直接-低" \
                or exposure["legacy_candidate_label"] != "直接-高":
            failures.append("产品证据或旧人工标签骗过规则形成高暴露")
        if exposure["evidence_types"] != "PRODUCT_ONLY" \
                or not exposure["evidence_periods"] or not exposure["source_ids"] \
                or not exposure["source_locators"] or not exposure["strongest_bear_case"]:
            failures.append("规则分类未输出证据类型、期间、来源和最强反方")
        if exposure["classification_cutoff"] > as_of_timestamp(date(2026, 7, 19)):
            failures.append("未来收入暴露证据进入历史分类")
    finally:
        connection.execute("ROLLBACK")

    gap = _row_dicts(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
    if gap["assessment"] != "数据不足" or gap["is_watchlist"] is not False:
        failures.append("空 Q3 数据合同未返回数据不足")

    connection.execute("BEGIN")
    try:
        connection.execute(
            """INSERT INTO fundamental_signals
               (signal_id, company_id, snapshot_at, metric_name, metric_value,
                metric_unit, fiscal_period, comparison_type, comparison_period,
                actual_or_guidance, source_id, first_available_at, ingested_at, revision_id)
               VALUES ('AC4_F', 'CHIP_NVDA', '2026-07-18T00:00:00+00:00',
                       'REVENUE', 40.0, 'USD_BN', 'FY2027Q1', 'YOY', 'FY2026Q1',
                       'ACTUAL', 'S_NVDA', '2026-07-18T00:00:00+00:00',
                       '2026-07-18T00:01:00+00:00', 'v1')"""
        )
        connection.execute(
            """INSERT INTO expectation_signals
               (signal_id, security_id, snapshot_at, forecast_metric, forecast_period,
                previous_snapshot_at, forecast_unit, current_value, previous_value, revision_pct,
                consensus_source, source_id, first_available_at, ingested_at, revision_id)
               VALUES ('AC4_E', 'US_NVDA', '2026-07-17T00:00:00+00:00',
                       'REVENUE', 'FY2027Q1', '2026-06-17T00:00:00+00:00',
                       'USD_BN', 39.0, 38.0, 0.026315789,
                       'FIXED_FIXTURE', 'S_NVDA', '2026-07-17T00:00:00+00:00',
                       '2026-07-17T00:01:00+00:00', 'v1')"""
        )
        connection.execute(
            """INSERT INTO price_signals
               (signal_id, security_id, snapshot_at, window_start, window_end,
                return_type, raw_return, benchmark_return, excess_return,
                benchmark_id, source_id, first_available_at, ingested_at, revision_id)
               VALUES ('AC4_P', 'US_NVDA', '2026-07-18T00:00:00+00:00',
                       '2026-07-01', '2026-07-18', 'TOTAL_RETURN', 0.08, 0.03, 0.05,
                       'SP500', 'S_NVDA', '2026-07-18T00:00:00+00:00',
                       '2026-07-18T00:01:00+00:00', 'v1')"""
        )
        connection.execute(
            """INSERT INTO valuation_signals
               (signal_id, security_id, snapshot_at, valuation_metric,
                valuation_value, forward_period, historical_percentile,
                source_id, first_available_at, ingested_at, revision_id)
               VALUES ('AC4_V', 'US_NVDA', '2026-07-18T00:00:00+00:00',
                       'FORWARD_PE', 25.0, 'FY2027Q1', 0.60, 'S_NVDA',
                       '2026-07-18T00:00:00+00:00',
                       '2026-07-18T00:01:00+00:00', 'v1')"""
        )
        complete = _row_dicts(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
        required_output = {
            "fundamental_snapshot_at", "fiscal_period", "metric_unit",
            "expectation_snapshot_at", "expectation_previous_snapshot_at",
            "forecast_period", "forecast_unit",
            "window_start", "window_end", "benchmark_id", "valuation_metric",
            "fundamental_source_url", "expectation_source_url",
            "price_source_url", "valuation_source_url",
        }
        if complete["is_watchlist"] is not True or not required_output <= set(complete):
            failures.append("完整同口径 Q3 fixture 未形成可比较候选或缺原始合同字段")

        connection.execute("UPDATE expectation_signals SET forecast_period='FY2028Q1' WHERE signal_id='AC4_E'")
        mismatch = _row_dicts(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
        if mismatch["is_watchlist"] is not False:
            failures.append("不同财务期间进入 Q3 候选")
        connection.execute(
            "UPDATE expectation_signals SET forecast_period='FY2027Q1', forecast_unit='PERCENT' WHERE signal_id='AC4_E'"
        )
        mismatch = _row_dicts(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]
        if mismatch["is_watchlist"] is not False:
            failures.append("不同单位进入 Q3 候选")
        connection.execute("UPDATE expectation_signals SET forecast_unit='USD_BN' WHERE signal_id='AC4_E'")
        connection.execute(
            "UPDATE fundamental_signals SET first_available_at='2027-01-01T00:00:00+00:00' WHERE signal_id='AC4_F'"
        )
        if _row_dicts(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]["is_watchlist"] is not False:
            failures.append("未来 Q3 数据进入历史候选")
        connection.execute(
            "UPDATE fundamental_signals SET first_available_at='2026-07-18T00:00:00+00:00' WHERE signal_id='AC4_F'"
        )
        connection.execute("DELETE FROM valuation_signals WHERE signal_id='AC4_V'")
        if _row_dicts(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]["is_watchlist"] is not False:
            failures.append("缺估值时进入 Q3 候选")
        connection.execute(
            """INSERT INTO valuation_signals
               (signal_id, security_id, snapshot_at, valuation_metric,
                valuation_value, forward_period, historical_percentile,
                source_id, first_available_at, ingested_at, revision_id)
               VALUES ('AC4_V', 'US_NVDA', '2026-07-18T00:00:00+00:00',
                       'FORWARD_PE', 25.0, 'FY2027Q1', 0.60, 'S_NVDA',
                       '2026-07-18T00:00:00+00:00',
                       '2026-07-18T00:01:00+00:00', 'v1')"""
        )
        connection.execute("DELETE FROM price_signals WHERE signal_id='AC4_P'")
        if _row_dicts(expectation_gap(connection, date(2026, 7, 19), "US_NVDA"))[0]["is_watchlist"] is not False:
            failures.append("缺收益窗口或 benchmark 时进入 Q3 候选")
    finally:
        connection.execute("ROLLBACK")
    return failures


def _rejects_empty_file(output_dir: Path, cutoff: date) -> bool:
    path = output_dir / "capex_tracker.csv"
    original = path.read_bytes()
    path.write_bytes(b"")
    try:
        validate_built_outputs(output_dir, as_of_timestamp(cutoff))
    except BuildError:
        return True
    finally:
        path.write_bytes(original)
    return False


def _rejects_future_leak(output_dir: Path, cutoff: date) -> bool:
    path = output_dir / "supply_chain_master.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        headers = list(rows[0])
    original = path.read_bytes()
    rows[0]["first_available_at"] = "2027-01-01T00:00:00+00:00"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    try:
        validate_built_outputs(output_dir, as_of_timestamp(cutoff))
    except BuildError:
        return True
    finally:
        path.write_bytes(original)
    return False


def _rejects_dqa_failure(
    connection: duckdb.DuckDBPyConnection, cutoff: date, output_root: Path
) -> bool:
    connection.execute("BEGIN")
    try:
        connection.execute(
            """UPDATE security_master SET currency='USD'
               WHERE security_id=(SELECT security_id FROM security_master WHERE market='A' LIMIT 1)"""
        )
        try:
            build_outputs(connection, cutoff, output_root)
        except BuildError:
            return True
        return False
    finally:
        connection.execute("ROLLBACK")


def _ac5_failures(
    connection: duckdb.DuckDBPyConnection, cutoff: date
) -> tuple[list[str], str]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ai-chain-acceptance-") as temp:
        root = Path(temp)
        try:
            first_dir, first_hashes = build_outputs(connection, cutoff, root)
        except BuildError as error:
            return [f"统一 build 失败：{error}"], "command=ai-chain build, files=0, deterministic=False"
        if set(first_hashes) != REQUIRED_OUTPUTS:
            failures.append("统一 build 未生成五个输出")
        if first_dir.name != f"as_of={cutoff.isoformat()}":
            failures.append("输出目录未包含 as_of_date")
        report = json.loads((first_dir / "data_quality_report.json").read_text(encoding="utf-8"))
        checks = set(report.get("contract_checks", {}))
        if not REQUIRED_DQA_CHECKS <= checks:
            failures.append("DQA JSON 缺合同检查项")
        with (first_dir / "supply_chain_master.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            supply_rows = list(csv.DictReader(handle))
        if len(supply_rows) != 10 or len({row.get("edge_id") for row in supply_rows}) != 10:
            failures.append("supply_chain_master 不是 10 条唯一 edge-level 关系")

        shutil.rmtree(first_dir)
        try:
            second_dir, second_hashes = build_outputs(connection, cutoff, root)
        except BuildError as error:
            failures.append(f"删除输出后无法重建：{error}")
            return failures, "command=ai-chain build, files=0, deterministic=False"
        if first_hashes != second_hashes:
            failures.append("相同输入与 as_of 的输出哈希不一致")
        if not _rejects_empty_file(second_dir, cutoff):
            failures.append("空文件骗过验收")
        if not _rejects_future_leak(second_dir, cutoff):
            failures.append("未来数据骗过输出校验")
        if not _rejects_dqa_failure(connection, cutoff, root / "invalid"):
            failures.append("DQA 失败未阻断 build")
        detail = f"command=ai-chain build, files={len(second_hashes)}, deterministic={first_hashes == second_hashes}"
    return failures, detail


def assess(
    connection: duckdb.DuckDBPyConnection,
    _output_dir: Path,
    as_of: date = date(2026, 7, 19),
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
              OR confidence IS NULL OR first_available_at IS NULL
              OR ingested_at IS NULL OR revision_id IS NULL"""
    ).fetchone()[0]
    evidence_count = connection.execute(
        """SELECT count(DISTINCT edge_id) FROM source_evidence
           WHERE source_locator IS NOT NULL
             AND content_sha256 IS NOT NULL AND archived_path IS NOT NULL"""
    ).fetchone()[0]
    trace_rows = sample_trace_rows(connection)
    evidence_issues = evidence_failures(connection)
    ac2 = edge_count == 10 and incomplete_edges == 0 and evidence_count == 10 and len(trace_rows) == 10 and not evidence_issues

    layers = {
        row[0]
        for row in connection.execute("SELECT DISTINCT industry_layer FROM company_master").fetchall()
    }
    ac3 = company_count == 30 and layers == EXPECTED_LAYERS

    ac4_failures = _ac4_failures(connection)
    ac5_failures, ac5_detail = _ac5_failures(connection, as_of)

    return [
        AcceptanceCheck("AC1", "统一公司与证券主表", ac1, f"companies={company_count}, mapped={mapped_companies}, mapping_errors={orphan_securities + bad_primary + bad_currency}"),
        AcceptanceCheck("AC2", "产业链关系可追溯", ac2, f"edges={edge_count}, incomplete={incomplete_edges}, archived_evidence={evidence_count}, fixed_sample={len(trace_rows)}/10, evidence_errors={len(evidence_issues)}"),
        AcceptanceCheck("AC3", "严格覆盖 30 家四层", ac3, f"companies={company_count}, layers={sorted(layers)}"),
        AcceptanceCheck("AC4", "三个研究查询执行验收", not ac4_failures, f"failures={ac4_failures}"),
        AcceptanceCheck("AC5", "统一构建与失败注入验收", not ac5_failures, f"{ac5_detail}, failures={ac5_failures}"),
    ]
