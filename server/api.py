"""Formal Track 3 door: official JSON request -> decision, articles, protected numbers or SPE.

Wraps the deterministic engine in `ehds/` (judge -> recipe -> SQL -> privacy gate / workspace).
Rules decide; an optional AI check (ehds/ai_check.py) can only make a decision stricter.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ehds import ai_check, audit, explain, workspace
from ehds.pipeline import handle

REQUESTS_DIR = Path(__file__).resolve().parents[1] / "requests"
REQUIRED = ("purpose", "question", "requested_as")
MAX_SPE_ROWS = 20
HISTORY: list[dict] = []  # in-memory staff list; cleared on restart

router = APIRouter(prefix="/v1", tags=["track3"])


class WorkspaceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str


def _samples() -> list[dict]:
    files = sorted(REQUESTS_DIR.glob("R*.json")) + sorted((REQUESTS_DIR / "demo").glob("D*.json"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


@router.get("/requests/samples")
def samples() -> list[dict]:
    return _samples()


@router.get("/requests/ai-status")
def ai_status() -> dict:
    return {"enabled": ai_check.enabled()}


@router.post("/requests")
def submit(req: dict) -> JSONResponse:
    missing = [k for k in REQUIRED if not isinstance(req.get(k), str) or not req[k].strip()]
    if not req.get("definitions"):
        missing.append("definitions")
    if missing:
        raise HTTPException(400, f"Missing or empty fields: {', '.join(missing)}")
    req = dict(req)
    req.setdefault("id", f"WEB-{uuid.uuid4().hex[:8]}")
    req.setdefault("applicant", {"organisation": "Web applicant", "country": "AT"})
    out = handle(req)
    staff = out.get("staff", {})
    staff.pop("raw_counts_staff_only", None)
    staff["steps"] = explain.decision_steps(req, out)
    if staff.get("recipe"):
        staff["recipe_summary"] = explain.recipe_summary(staff["recipe"])
    is_stat = out.get("decision") == "APPROVE_STATISTICAL" and out.get("answer")
    out["privacy"] = explain.privacy_summary(out["answer"]) if is_stat else None
    out["seq"] = len(HISTORY) + 1
    enc = jsonable_encoder(out)
    HISTORY.append({"seq": out["seq"], "received": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "request": jsonable_encoder(req), "result": enc})
    return JSONResponse(enc)


def _summary(e: dict) -> dict:
    req, out = e["request"], e["result"]
    flags = [f for f in out.get("staff", {}).get("flags", [])
             if not f.startswith(("AI check is off", "AI check unavailable"))]
    records = str(req.get("requested_as", "")).lower().startswith("health data access")
    return {"seq": e["seq"], "received": e["received"], "id": out.get("id"),
            "organisation": (req.get("applicant") or {}).get("organisation", ""),
            "country": (req.get("applicant") or {}).get("country", ""),
            "purpose": str(req.get("purpose", ""))[:240], "asked": "Records" if records else "Statistics",
            "decision": out.get("decision"), "flags": len(flags), "appeal": bool(e.get("appeal"))}


class AppealBody(BaseModel):
    comment: str = ""


@router.post("/requests/{seq}/appeal")
def appeal(seq: int, body: AppealBody) -> dict:
    """The applicant asks a person to review a rejection. It shows up on the Staff desk."""
    if not 1 <= seq <= len(HISTORY):
        raise HTTPException(404, "Unknown request.")
    e = HISTORY[seq - 1]
    if e["result"].get("decision") != "REJECT":
        raise HTTPException(409, "Only rejected requests can be sent for manual review.")
    if e.get("appeal"):
        raise HTTPException(409, "Manual review was already requested.")
    e["appeal"] = {"comment": body.comment.strip()[:2000],
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    audit.log("manual_review_requested", request=e["result"].get("id"), seq=seq)
    return {"status": "requested", "seq": seq}


@router.get("/staff/requests")
def staff_requests() -> list[dict]:
    return [_summary(e) for e in reversed(HISTORY)]


@router.get("/staff/requests/{seq}")
def staff_request(seq: int) -> dict:
    if not 1 <= seq <= len(HISTORY):
        raise HTTPException(404, "Unknown request.")
    return HISTORY[seq - 1]


@router.get("/workspaces/{permit_id}")
def workspace_info(permit_id: str) -> dict:
    try:
        return workspace.info(permit_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "No workspace for this permit.") from exc


@router.post("/workspaces/{permit_id}/download")
def download(permit_id: str) -> JSONResponse:
    return JSONResponse(status_code=403, content=workspace.download(permit_id))


@router.post("/workspaces/{permit_id}/query")
def workspace_query(permit_id: str, body: WorkspaceQuery) -> dict:
    try:
        df = workspace.view(permit_id, body.sql)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "No workspace for this permit.") from exc
    head = df.head(MAX_SPE_ROWS)
    return {"columns": list(head.columns), "rows": jsonable_encoder(head.astype(object).where(head.notna(), None).values.tolist()),
            "total_rows": len(df), "note": "Summary only. Patient-level data cannot leave the secure processing environment."}


@router.get("/audit")
def audit_log(n: int = 30) -> dict:
    return {"verify": audit.verify(), "entries": audit.tail(min(max(n, 1), 200))}
