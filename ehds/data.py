"""Loads the five CSV files into an in-process DuckDB database.

Opted-out patients (Art. 71) are removed once, here, in the views every query
uses. The raw tables are prefixed ``raw_`` and are never queried by the engine.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TABLES = ["patients", "conditions", "medications", "observations", "encounters"]

_lock = threading.Lock()


def _load(con: duckdb.DuckDBPyConnection, data_dir: Path) -> None:
    for t in TABLES:
        path = (data_dir / f"{t}.csv").as_posix()
        con.execute(
            f"CREATE TABLE raw_{t} AS SELECT * FROM read_csv('{path}', header=true, all_varchar=true)"
        )
    con.execute(
        """
        CREATE VIEW patients AS
        SELECT patient_id, sex, CAST(birth_date AS DATE) AS birth_date,
               2025 - CAST(substr(birth_date, 1, 4) AS INTEGER) AS age,
               region, data_holder
        FROM raw_patients WHERE lower(opted_out) <> 'true'
        """
    )
    con.execute(
        """
        CREATE VIEW conditions AS
        SELECT c.patient_id, c.icd10, c.description, CAST(c.onset_date AS DATE) AS onset_date
        FROM raw_conditions c JOIN patients p USING (patient_id)
        """
    )
    con.execute(
        """
        CREATE VIEW medications AS
        SELECT m.patient_id, m.atc, m.name, CAST(m.start_date AS DATE) AS start_date,
               CAST(nullif(m.end_date, '') AS DATE) AS end_date
        FROM raw_medications m JOIN patients p USING (patient_id)
        """
    )
    con.execute(
        """
        CREATE VIEW observations AS
        SELECT o.patient_id, o.loinc, o.name, CAST(o.value AS DOUBLE) AS value, o.unit,
               CAST(o.date AS DATE) AS date
        FROM raw_observations o JOIN patients p USING (patient_id)
        """
    )
    con.execute(
        """
        CREATE VIEW encounters AS
        SELECT e.encounter_id, e.patient_id, e.data_holder, e.type,
               CAST(e.start_date AS DATE) AS start_date, CAST(e.end_date AS DATE) AS end_date,
               nullif(e.primary_icd10, '') AS primary_icd10
        FROM raw_encounters e JOIN patients p USING (patient_id)
        """
    )


@lru_cache(maxsize=None)
def connection(data_dir: str = str(DATA_DIR)) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    _load(con, Path(data_dir))
    return con


def query(sql: str, params: list | None = None):
    """Runs a read query on the opt-out-filtered views and returns a DataFrame."""
    with _lock:
        return connection().execute(sql, params or []).df()


def domain(column: str) -> list[str]:
    """All values a grouping column can take, from the full dataset (so empty groups still show)."""
    table = {"region": "raw_patients", "data_holder": "raw_patients", "sex": "raw_patients"}[column]
    with _lock:
        rows = connection().execute(f"SELECT DISTINCT {column} FROM {table} ORDER BY 1").fetchall()
    return [r[0] for r in rows]
