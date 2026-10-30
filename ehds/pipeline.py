"""End-to-end handling of one request: judge -> compute -> protect -> answer."""

from __future__ import annotations

import time

from . import ai_check, audit, certificate, engine, evidence, memory, privacy, recipe, reference, workspace
from .judge import decide


def handle(req: dict, create_workspace: bool = True, ai_client=None) -> dict:
    t0 = time.perf_counter()
    earlier = memory.history(req)
    ai = ai_check.review(req, ai_client, earlier)
    d = ai_check.apply(decide(req), ai, req)
    granted = [h for h in earlier if h["decision"] != "REJECT"]
    if granted and d.decision == "REJECT" and "54" in d.articles:
        d.flags.append(f'Purpose change: this organisation was granted {granted[-1]["id"]} for "{granted[-1]["purpose"][:120]}", '
                       "and now asks for a forbidden use. Refused.")
    out: dict = {
        "id": req.get("id"),
        "decision": d.decision,
        "articles": d.articles,
        "reason": d.reason,
        "applicant_letter": d.applicant_text,
        "ai_check": ai,
        "staff": {"matched": d.matched, "flags": list(d.flags), "earlier_requests": earlier},
    }
    try:
        _compute(req, d, out, create_workspace, ai_client)
    except Exception as exc:  # noqa: BLE001 — a bad definition must give a clear answer, not a crash
        out["answer"] = {"error": "The definitions could not be turned into a calculation. A staff member will review the request."}
        out.pop("workspace", None)
        out["staff"]["flags"].append(f"Calculation failed ({type(exc).__name__}); staff review needed.")
    memory.remember_request(req, d.decision, d.articles)
    out["certificate"] = certificate.issue(req, out)
    out["staff"]["seconds"] = round(time.perf_counter() - t0, 3)
    audit.log("decision", request_id=req.get("id"), decision=d.decision, articles=d.articles,
              double_check=out.get("verification", {}).get("status"), certificate=out["certificate"]["id"],
              ai_check=ai.get("status"), flags=out["staff"]["flags"], seconds=out["staff"]["seconds"])
    return out


def _compute(req: dict, d, out: dict, create_workspace: bool, ai_client=None) -> None:
    if d.decision != "REJECT":
        rc = recipe.parse(req)
        out["staff"]["recipe"] = rc
        if rc["errors"] or rc["unparsed"]:
            out["staff"]["flags"].append("Some definitions could not be interpreted; staff review recommended.")
        if d.decision == "APPROVE_STATISTICAL":
            answer, trace = engine.run(rc)
            if not answer or all(v == {} for v in answer.values()):
                answer = {"error": "The definitions do not say what to report (add a 'Report ...' line). No numbers were released."}
            out["verification"] = reference.verify_statistics(rc, answer, trace["raw_counts_staff_only"])
            if out["verification"]["status"] != "match":
                out["staff"]["flags"].append(out["verification"]["detail"])
            if out["verification"]["status"] == "mismatch":
                answer = {"error": "Answer withheld: the two independent computations disagree."}
            leaks = privacy.check_answer(answer)
            if leaks:
                answer = {"error": "Answer withheld by privacy gate."}
                out["staff"]["flags"] += leaks
            if "error" not in answer:
                held = memory.guard(req, answer, trace["released_groups"])
                if held:
                    out["held_back"] = held
                    out["staff"]["flags"].append(f"{len(held)} number(s) held back: compared with an earlier release they would "
                                                 "reveal a group below 10 (differencing).")
                out["evidence"] = evidence.investigate(req, answer, ai_client)
            out["answer"] = answer
            out["staff"]["sql"] = trace["sql"]
            out["staff"]["raw_counts_staff_only"] = trace["raw_counts_staff_only"]
        else:
            ids = engine.cohort_ids(rc)
            out["verification"] = reference.verify_cohort(rc, ids)
            out["patient_count"] = len(ids)
            out["answer"] = {"cohort_size": len(ids)}
            out["record_types"] = workspace.record_types(req, rc)
            if out["verification"]["status"] != "match":
                out["staff"]["flags"].append(out["verification"]["detail"])
            elif create_workspace:
                out["workspace"] = workspace.create(req["id"], ids, out["record_types"])
