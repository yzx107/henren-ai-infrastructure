from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from .acceptance import assess
from .as_of import as_of_timestamp
from .audit import evidence_failures, write_trace_sample
from .build import BuildError, build_outputs
from .db import DEFAULT_DB_PATH, connect, initialize
from .validation import validate


def _print_rows(headers: list[str], rows: list[tuple[object, ...]]) -> None:
    widths = [len(str(header)) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value or "")))
    print(" | ".join(str(header).ljust(widths[i]) for i, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(str(value or "").ljust(widths[i]) for i, value in enumerate(row)))


def _query(
    path: Path, sql: str, params: list[object] | None = None
) -> tuple[list[str], list[tuple[object, ...]]]:
    connection = connect(path)
    try:
        result = connection.execute(sql, params or [])
        return [item[0] for item in result.description], result.fetchall()
    finally:
        connection.close()


def cmd_init(args: argparse.Namespace) -> int:
    counts = initialize(args.db)
    print(f"数据库已初始化：{args.db}")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    connection = connect(args.db)
    try:
        issues = validate(connection, args.as_of)
    finally:
        connection.close()
    if issues:
        print("DQA FAILED")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("DQA PASS: 30 家公司、4 个层级、证券/来源/日期/引用完整")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    headers, rows = _query(
        args.db,
        """SELECT industry_layer AS layer, count(*) AS companies,
                  round(avg(confidence), 2) AS avg_confidence
           FROM initial_universe_as_of(?) GROUP BY industry_layer ORDER BY industry_layer""",
        [as_of_timestamp(args.as_of)],
    )
    _print_rows(headers, rows)
    return 0


def cmd_universe(args: argparse.Namespace) -> int:
    headers, rows = _query(
        args.db,
        """SELECT company, security_code, market, industry_layer, core_product,
                  major_customers, ai_revenue_exposure, disclosed_at,
                  current_thesis, strongest_bear_case, relationship_source
           FROM initial_universe_as_of(?) ORDER BY industry_layer, company""",
        [as_of_timestamp(args.as_of)],
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)
        print(f"已导出：{args.output}")
    else:
        compact = [row[:4] + row[6:8] for row in rows]
        _print_rows(headers[:4] + headers[6:8], compact)
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    headers, rows = _query(
        args.db,
        """SELECT company_name, industry_layer, round(opportunity_score, 3) AS score,
                  confidence, analyst_note
           FROM opportunity_scores_as_of(?) ORDER BY opportunity_score DESC""",
        [as_of_timestamp(args.as_of)],
    )
    if not rows:
        print("暂无完整评分。先补充 company_exposures 的五项证据化评分，系统不会用空值制造排名。")
        return 0
    _print_rows(headers, rows)
    return 0


def cmd_acceptance(args: argparse.Namespace) -> int:
    connection = connect(args.db)
    try:
        checks = assess(connection, args.output_dir, args.as_of)
    finally:
        connection.close()
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"{check.check_id} {status} | {check.title} | {check.detail}")
    accepted = all(check.passed for check in checks)
    print("MVP ACCEPTED" if accepted else "MVP NOT ACCEPTED")
    return 0 if accepted else 1


def cmd_build(args: argparse.Namespace) -> int:
    if not args.no_init:
        initialize(args.db)
    connection = connect(args.db)
    try:
        output_dir, hashes = build_outputs(connection, args.as_of, args.output_root)
    except BuildError as error:
        print(f"BUILD FAILED: {error}")
        return 1
    finally:
        connection.close()
    print(f"BUILD PASS: output={output_dir}")
    for name, digest in hashes.items():
        print(f"  {name}: {digest}")
    return 0


def cmd_trace_audit(args: argparse.Namespace) -> int:
    connection = connect(args.db)
    try:
        failures = evidence_failures(connection)
        row_count = write_trace_sample(connection, args.output)
    finally:
        connection.close()
    if failures or row_count != 10:
        print(f"TRACE AUDIT FAILED: rows={row_count}, errors={failures}")
        return 1
    print(f"TRACE AUDIT PASS: fixed_seed=mvp-v1, sample=10/10, output={args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Henren 全球 AI 基础设施产业链研究")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="建立 DuckDB 并载入种子数据")
    init_parser.set_defaults(func=cmd_init)

    check_parser = subparsers.add_parser("check", help="运行数据质量闸门")
    check_parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    check_parser.set_defaults(func=cmd_check)

    summary_parser = subparsers.add_parser("summary", help="查看分层覆盖")
    summary_parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    summary_parser.set_defaults(func=cmd_summary)

    universe_parser = subparsers.add_parser("universe", help="查看或导出 30 家初始表")
    universe_parser.add_argument("--output", type=Path)
    universe_parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    universe_parser.set_defaults(func=cmd_universe)

    rank_parser = subparsers.add_parser("rank", help="按有证据的完整评分排序")
    rank_parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    rank_parser.set_defaults(func=cmd_rank)

    acceptance_parser = subparsers.add_parser("acceptance", help="运行冻结的 MVP 验收合同")
    acceptance_parser.add_argument(
        "--output-dir", type=Path, default=Path("outputs") / "mvp"
    )
    acceptance_parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    acceptance_parser.set_defaults(func=cmd_acceptance)

    build_output = subparsers.add_parser("build", help="重建数据库并生成五个确定性研究输出")
    build_output.add_argument("--as-of", type=date.fromisoformat, required=True)
    build_output.add_argument(
        "--output-root", type=Path, default=Path("outputs") / "research"
    )
    build_output.add_argument("--no-init", action="store_true", help=argparse.SUPPRESS)
    build_output.set_defaults(func=cmd_build)

    trace_parser = subparsers.add_parser("trace-audit", help="生成并校验固定 10 条关系抽查")
    trace_parser.add_argument(
        "--output", type=Path, default=Path("outputs") / "mvp" / "relation_trace_sample.csv"
    )
    trace_parser.set_defaults(func=cmd_trace_audit)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))
