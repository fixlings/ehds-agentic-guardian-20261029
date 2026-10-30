"""Secure processing environment (SPE): one read-only DuckDB file per permit.

Only the selected patients and the record types the request names are copied.
Patient IDs are kept (pseudonymised data). Downloads are always refused and
logged; researchers can only run read-only queries inside the workspace.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import duckdb

from . import audit, data

ROOT = Path(os.environ.get("EHDS_WORKSPACE_DIR", Path(__file__).resolve().parent.parent / "workspaces"))
RECORD_WORDS = {
    "conditions": r"\bconditions?\b|\bdiagnos[ie]s\b",
    "medications": r"\bmedications?\b|\bprescriptions?\b|\bdrugs?\b",
    "observations": r"\bobservations?\b|\blab(?:oratory)? values?\b|\bmeasurements?\b|\bvalues\b",
    "encounters": r"\bencounters?\b|\bhospital stays?\b|\bvisits?\b|\badmissions?\b",
}


def record_types(req: dict, rc: dict) -> dict:
    """Which tables (and which LOINC codes for observations) the request names."""
    q = req.get("question", "")
    types: dict = {}
    for table, pat in RECORD_WORDS.items():
        if re.search(pat, q, re.I):
            types[table] = None
    for t in req.get("requested_records") or []:
        if t in RECORD_WORDS:
            types.setdefault(t, None)
    loincs = sorted({c["loinc"] for c in rc["concepts"].values()
                     if c["kind"] == "observation" and re.search(re.escape(c["name"]), q, re.I)})
    if loincs:
        types["observations"] = loincs
    return types


def path(permit: str) -> Path:
    return ROOT / f"{re.sub(r'[^A-Za-z0-9_-]', '_', permit)}.duckdb"


def create(permit: str, patient_ids: list[str], types: dict) -> dict:
    ROOT.mkdir(parents=True, exist_ok=True)
    p = path(permit)
    p.unlink(missing_ok=True)
    src = data.connection()
    ids = src.execute("SELECT unnest(?) AS patient_id", [list(patient_ids)]).df()
    out = duckdb.connect(str(p))
    out.register("ids", ids)
    out.execute("CREATE TABLE cohort AS SELECT patient_id FROM ids")
    copied = {}
    for table, loincs in types.items():
        df = src.execute(f"SELECT * FROM {table} WHERE patient_id IN (SELECT unnest(?))", [list(patient_ids)]).df()
        if loincs:
            df = df[df["loinc"].isin(loincs)]
        out.register("tmp", df)
        out.execute(f"CREATE TABLE {table} AS SELECT * FROM tmp")
        out.unregister("tmp")
        copied[table] = len(df)
    out.close()
    audit.log("workspace_created", permit=permit, patients=len(patient_ids), tables=copied)
    return {"permit": permit, "file": p.name, "patients": len(patient_ids), "rows": copied,
            "downloads": "blocked"}


def info(permit: str) -> dict:
    """Tables, columns and row counts of an open workspace (no data rows)."""
    p = path(permit)
    if not p.exists():
        raise FileNotFoundError(permit)
    con = duckdb.connect(str(p), read_only=True, config={"enable_external_access": False})
    try:
        tables = []
        for (name,) in con.execute("SELECT table_name FROM information_schema.tables ORDER BY table_name").fetchall():
            cols = [r[0] for r in con.execute(f'DESCRIBE "{name}"').fetchall()]
            rows = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            tables.append({"name": name, "columns": cols, "rows": rows})
    finally:
        con.close()
    return {"permit": permit, "tables": tables, "downloads": "blocked"}


def run_query(permit: str, sql: str):
    """Read-only query inside the workspace. Only a single SELECT is allowed."""
    s = sql.strip().rstrip(";")
    if ";" in s or not re.match(r"(?is)^\s*(select|with)\b", s) or re.search(
            r"(?i)\b(copy|export|attach|install|load|read_\w+|write|pragma|httpfs|glob)\b", s):
        audit.log("workspace_query_blocked", permit=permit, sql=sql[:200])
        raise PermissionError("Only a single read-only SELECT inside the workspace is allowed.")
    con = duckdb.connect(str(path(permit)), read_only=True, config={"enable_external_access": False})
    try:
        df = con.execute(s).df()
    finally:
        con.close()
    audit.log("workspace_query", permit=permit, sql=sql[:200], rows=len(df))
    return df


SUMMARY = re.compile(r"(?i)\b(count|avg|sum|min|max|median|stddev\w*|quantile\w*)\s*\(")


def _ids(permit: str) -> set[str]:
    con = duckdb.connect(str(path(permit)), read_only=True, config={"enable_external_access": False})
    try:
        tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        ids = {str(r[0]) for r in con.execute("SELECT patient_id FROM cohort").fetchall()}
        if "encounters" in tables:
            ids |= {str(r[0]) for r in con.execute("SELECT encounter_id FROM encounters").fetchall()}
    finally:
        con.close()
    return ids


def view(permit: str, sql: str):
    """Query whose result is shown outside the workspace: summaries only, never rows or IDs."""
    if not SUMMARY.search(sql):
        audit.log("workspace_view_blocked", permit=permit, sql=sql[:200], why="no summary")
        raise PermissionError("Only summary results (count, avg, sum, min, max ...) can be shown. "
                              "Patient-level rows stay inside the secure processing environment.")
    df = run_query(permit, sql)
    ids = _ids(permit)
    cols = [str(c).lower() for c in df.columns]
    if any("patient_id" in c or "encounter_id" in c for c in cols) or any(
            str(v) in ids for v in df.astype(str).to_numpy().ravel()):
        audit.log("workspace_view_blocked", permit=permit, sql=sql[:200], why="identifiers in result")
        raise PermissionError("The result contains patient or encounter IDs. Identifiers never leave the "
                              "secure processing environment.")
    return df


def download(permit: str, what: str = "all") -> dict:
    audit.log("download_blocked", permit=permit, what=what)
    return {"allowed": False,
            "message": "Blocked: data must stay inside the secure processing environment (Art. 73). "
                       "Only reviewed, anonymised results may leave through the export airlock."}
