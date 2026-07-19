from __future__ import annotations

import csv
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "runtime" / "ai_chain.duckdb"
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
SEED_DIR = PROJECT_ROOT / "data" / "seed"

SEEDS = {
    "sources": "sources.csv",
    "company_master": "company_master.csv",
    "security_master": "security_master.csv",
    "company_research_snapshot": "company_research_snapshot.csv",
    "supply_chain_edges": "supply_chain_edges.csv",
    "source_evidence": "source_evidence.csv",
    "hypotheses": "hypotheses.csv",
    "company_exposures": "company_exposures.csv",
}


def connect(path: Path = DEFAULT_DB_PATH) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def _read_csv(path: Path) -> tuple[list[str], list[tuple[object, ...]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Missing CSV header: {path}")
        rows = [tuple(value if value != "" else None for value in row.values()) for row in reader]
    return reader.fieldnames, rows


def initialize(path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    connection = connect(path)
    try:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        connection.execute("BEGIN")
        for table, filename in SEEDS.items():
            columns, rows = _read_csv(SEED_DIR / filename)
            connection.execute(f"DELETE FROM {table}")
            if rows:
                placeholders = ", ".join("?" for _ in columns)
                column_sql = ", ".join(columns)
                connection.executemany(
                    f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})", rows
                )
            counts[table] = len(rows)
        connection.execute("COMMIT")
        return counts
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
