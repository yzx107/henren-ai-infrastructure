from __future__ import annotations

import csv
import hashlib
import random
from pathlib import Path

import duckdb

from .db import PROJECT_ROOT


TRACE_HEADERS = [
    "edge_id", "source_company", "relation_type", "target_company", "product",
    "source_id", "source_url", "disclosed_at", "valid_from", "source_locator",
    "archived_path_or_sha256", "auditor_result", "auditor_note",
]


def sample_trace_rows(
    connection: duckdb.DuckDBPyConnection, sample_size: int = 10, seed: str = "mvp-v1"
) -> list[tuple[object, ...]]:
    edge_ids = [
        row[0]
        for row in connection.execute(
            "SELECT edge_id FROM supply_chain_edges ORDER BY edge_id"
        ).fetchall()
    ]
    if len(edge_ids) < sample_size:
        selected = edge_ids
    else:
        selected = random.Random(seed).sample(edge_ids, sample_size)
    rows = connection.execute(
        """SELECT e.edge_id, sc.company_name, e.relation_type, tc.company_name,
                  e.product, e.source_id, s.url, e.disclosed_at, e.valid_from,
                  v.source_locator,
                  v.archived_path || '#sha256=' || v.content_sha256,
                  v.auditor_result, v.auditor_note
           FROM supply_chain_edges e
           JOIN company_master sc ON sc.company_id=e.source_company_id
           JOIN company_master tc ON tc.company_id=e.target_company_id
           JOIN sources s ON s.source_id=e.source_id
           LEFT JOIN source_evidence v ON v.edge_id=e.edge_id AND v.source_id=e.source_id"""
    ).fetchall()
    by_id = {row[0]: row for row in rows}
    return [by_id[edge_id] for edge_id in selected if edge_id in by_id]


def evidence_failures(connection: duckdb.DuckDBPyConnection) -> list[str]:
    failures: list[str] = []
    rows = connection.execute(
        """SELECT e.edge_id, e.source_id, v.source_id, v.source_locator,
                  v.archived_path, v.content_sha256, v.auditor_result
           FROM supply_chain_edges e
           LEFT JOIN source_evidence v ON v.edge_id=e.edge_id"""
    ).fetchall()
    for edge_id, edge_source, evidence_source, locator, archive, expected_hash, result in rows:
        if edge_source != evidence_source or not locator or not archive or not expected_hash:
            failures.append(f"{edge_id}: evidence metadata incomplete")
            continue
        path = PROJECT_ROOT / archive
        if not path.is_file():
            failures.append(f"{edge_id}: archive missing")
            continue
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            failures.append(f"{edge_id}: SHA-256 mismatch")
        if result != "PASS":
            failures.append(f"{edge_id}: auditor_result={result}")
    return failures


def write_trace_sample(connection: duckdb.DuckDBPyConnection, output: Path) -> int:
    rows = sample_trace_rows(connection)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(TRACE_HEADERS)
        writer.writerows(rows)
    return len(rows)
