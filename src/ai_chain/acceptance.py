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


def _ac4_failures(connection: duckdb.DuckDBPyConnection) -> list[str]:
    failures: list[str] = []
    early = capex_beneficiaries(connection, "CLOUD_GOOG", date(2026, 2, 28))
    if len(early.rows) != 1 or early.rows[0][3:5] != (1, "CHIP_NVDA"):
        failures.append("as_of 未把早期结果限制为 NVIDIA 直接路径")
    if any(row[10] > as_of_timestamp(date(2026, 2, 28)) for row in early.rows):
        failures.append("早期 CapEx 查询泄漏未来关系")
    if any(not row[9] or not row[12] or not row[13] for row in early.rows):
        failures.append("CapEx 路径缺披露时间或来源")

    late = capex_beneficiaries(connection, "CLOUD_GOOG", date(2026, 7, 19))
    direct = {row[4] for row in late.rows if row[3] == 1}
    secondary = {row[4] for row in late.rows if row[3] == 2}
    if direct != {"CHIP_NVDA"}:
        failures.append(f"直接受益路径错误：{sorted(direct)}")
    if secondary != {"MEM_SKHYNIX", "OPT_COHERENT", "OPT_LUMENTUM"}:
        failures.append(f"二级受益路径错误：{sorted(secondary)}")

    exposure = exposure_classification(connection, "CHIP_NVDA", date(2026, 7, 19))
    if len(exposure.rows) != 1:
        failures.append("收入暴露查询未返回唯一最新快照")
    else:
        row = exposure.rows[0]
        if not row[3] or not row[4] or not row[9] or not row[10] or not row[11]:
            failures.append("收入暴露分类缺证据、来源或披露时间")
        if row[12] > as_of_timestamp(date(2026, 7, 19)):
            failures.append("收入暴露查询泄漏未来快照")

    gap = expectation_gap(connection, date(2026, 7, 19), "US_NVDA")
    if len(gap.rows) != 1 or gap.rows[0][-3] != "数据不足" or gap.rows[0][-2] is not False:
        failures.append("缺预期/估值/价格时未返回数据不足或错误进入观察名单")
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
