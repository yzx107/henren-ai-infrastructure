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
    "capex_events": "capex_events.csv",
    "fundamental_signals": "fundamental_signals.csv",
    "expectation_signals": "expectation_signals.csv",
    "price_signals": "price_signals.csv",
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


def _backfill_temporal_fields(connection: duckdb.DuckDBPyConnection) -> None:
    end_of_day = "INTERVAL '23 hours 59 minutes 59 seconds'"
    connection.execute(
        f"""UPDATE sources
            SET first_available_at = CAST(disclosed_at AS TIMESTAMP) AT TIME ZONE 'UTC' + {end_of_day},
                ingested_at = CAST(accessed_at AS TIMESTAMP) AT TIME ZONE 'UTC' + {end_of_day},
                revision_id = 'v1'
            WHERE first_available_at IS NULL OR ingested_at IS NULL OR revision_id IS NULL"""
    )
    connection.execute(
        f"""UPDATE company_research_snapshot r
            SET first_available_at = greatest(
                    s.first_available_at,
                    CAST(r.as_of_date AS TIMESTAMP) AT TIME ZONE 'UTC' + {end_of_day}
                ),
                ingested_at = greatest(
                    s.ingested_at,
                    CAST(r.as_of_date AS TIMESTAMP) AT TIME ZONE 'UTC' + {end_of_day}
                ),
                revision_id = 'v1'
            FROM sources s
            WHERE r.source_id=s.source_id
              AND (r.first_available_at IS NULL OR r.ingested_at IS NULL OR r.revision_id IS NULL)"""
    )
    connection.execute(
        """UPDATE supply_chain_edges e
            SET first_available_at=s.first_available_at,
                ingested_at=s.ingested_at,
                revision_id='v1'
            FROM sources s
            WHERE e.source_id=s.source_id
              AND (e.first_available_at IS NULL OR e.ingested_at IS NULL OR e.revision_id IS NULL)"""
    )
    connection.execute(
        f"""UPDATE company_exposures e
            SET first_available_at=coalesce(
                    s.first_available_at,
                    CAST(e.as_of_date AS TIMESTAMP) AT TIME ZONE 'UTC' + {end_of_day}
                ),
                ingested_at=coalesce(
                    s.ingested_at,
                    CAST(e.as_of_date AS TIMESTAMP) AT TIME ZONE 'UTC' + {end_of_day}
                ),
                revision_id='v1'
            FROM sources s
            WHERE e.source_id=s.source_id
              AND (e.first_available_at IS NULL OR e.ingested_at IS NULL OR e.revision_id IS NULL)"""
    )


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
        _backfill_temporal_fields(connection)
        connection.execute("COMMIT")
        return counts
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
