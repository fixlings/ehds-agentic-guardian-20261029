/* Track 3 formal request door + staff desk. The rules engine owns every decision. */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  };
  const card = (title, warning = false) => {
    const c = el("section", warning ? "result-card warning" : "result-card");
    c.append(el("strong", "", title));
    return c;
  };
  const LABELS = { REJECT: "Rejected", APPROVE_STATISTICAL: "Approved: statistics", APPROVE_SPE: "Approved: secure workspace" };
  let samples = [];
  let last = null;

  function table(headers, rows) {
    const t = el("table", "result-table");
    const tr = el("tr");
    headers.forEach((h) => tr.append(el("th", "", h)));
    const thead = el("thead"); thead.append(tr); t.append(thead);
    const tbody = el("tbody");
    rows.forEach((r) => {
      const row = el("tr");
      r.forEach((v) => row.append(el("td", v === "<10" || v === "suppressed" ? "suppressed-cell" : "", v ?? "—")));
      tbody.append(row);
    });
    t.append(tbody);
    return t;
  }

  function answerTable(answer) {
    if (!answer || typeof answer !== "object") return el("p", "", "No numbers.");
    const entries = Object.entries(answer);
    const nested = entries.length && entries.every(([, v]) => v && typeof v === "object" && !Array.isArray(v));
    if (!nested) return table(["Measure", "Value"], entries.map(([k, v]) => [k.replaceAll("_", " "), v]));
    const cols = [...new Set(entries.flatMap(([, v]) => Object.keys(v)))];
    return table(["Group", ...cols.map((c) => c.replaceAll("_", " "))], entries.map(([g, v]) => [g, ...cols.map((c) => v[c])]));
  }

  async function post(path, body) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    return { status: res.status, body: await res.json().catch(() => ({})) };
  }

  function renderApplicant(out) {
    const section = $("formal-result");
    section.hidden = false;
    section.dataset.state = out.decision;
    $("formal-title").textContent = `${out.id}: ${LABELS[out.decision] || out.decision}`;
    $("formal-badge").textContent = `EHDS Art. ${(out.articles || []).join(", ")}`;
    const body = $("formal-body");
    body.replaceChildren();
    const letter = card("Letter to the applicant");
    letter.append(el("p", "", out.applicant_letter));
    letter.append(el("p", "", `Why: ${out.reason}`));
    body.append(letter);
    body.append(aiCard(out.ai_check));
    if (out.answer && out.answer.error) { const e = card("No numbers", true); e.append(el("p", "", out.answer.error)); body.append(e); return; }
    if (out.decision === "APPROVE_STATISTICAL") {
      const c = card("Privacy-protected statistics");
      c.append(answerTable(out.answer));
      if (out.privacy) c.append(el("p", "", `${out.privacy.hidden} of ${out.privacy.cells} cells hidden by the small-group rule (<10).`));
      body.append(c);
    }
    if (out.decision === "APPROVE_SPE" && out.workspace) {
      const c = card("Secure processing environment");
      c.append(el("p", "", `Workspace opened for permit ${out.id} with ${out.patient_count} pseudonymised patients. Data types: ${Object.keys(out.record_types || {}).join(", ") || "—"}.`));
      const q = el("textarea", "mono"); q.rows = 2; q.value = "SELECT count(DISTINCT patient_id) AS patients FROM cohort";
      const run = el("button", "secondary-button", "Run query inside the workspace"); run.type = "button";
      const dl = el("button", "text-button", "Try to download the data"); dl.type = "button";
      const msg = el("div");
      run.onclick = async () => {
        const r = await post(`/v1/workspaces/${encodeURIComponent(out.id)}/query`, { sql: q.value });
        msg.replaceChildren(r.status === 200 ? table(r.body.columns, r.body.rows) : el("p", "", `Blocked: ${r.body.detail}`));
        if (r.status === 200) msg.append(el("p", "", r.body.note));
      };
      dl.onclick = async () => {
        const r = await post(`/v1/workspaces/${encodeURIComponent(out.id)}/download`);
        msg.replaceChildren(el("p", "", `HTTP ${r.status} · ${r.body.message}`));
      };
      c.append(q, run, dl, msg);
      body.append(c);
    }
  }

  function renderStaff(out) {
    const body = $("staff-body");
    body.replaceChildren();
    const s = out.staff || {};
    const steps = card(`Decision trace for ${out.id}: ${out.decision}`);
    const ol = el("ol", "staff-steps");
    (s.steps || []).forEach((st) => {
      const li = el("li", st.ok ? "ok" : "fail", `${st.check} → ${st.result}${st.evidence ? ` (${st.evidence})` : ""}`);
      ol.append(li);
    });
    steps.append(ol);
    steps.append(el("p", "", `Processed in ${s.seconds} s.`));
    body.append(steps);
    if (s.flags && s.flags.length) {
      const f = card("Flags for staff review", true);
      const ul = el("ul"); s.flags.forEach((x) => ul.append(el("li", "", x))); f.append(ul);
      body.append(f);
    }
    if (s.recipe_summary) {
      const r = card(`How the definitions were read (counting ${s.recipe_summary.counting})`);
      r.append(table(["Term", "Meaning", "Source"], s.recipe_summary.terms.map((t) => [t.Term, t.Meaning, t.Source])));
      r.append(table(["Measure", "How it is computed"], s.recipe_summary.measures.map((m) => [m.Measure, m["How it is computed"]])));
      if (s.recipe_summary.grouped_by.length) r.append(el("p", "", `Grouped by: ${s.recipe_summary.grouped_by.join(", ")}`));
      s.recipe_summary.problems.forEach((p) => r.append(el("p", "", `Problem: ${p}`)));
      body.append(r);
    }
    if (out.verification) {
      const v = card("Independent double-check");
      v.append(el("p", "", `${out.verification.status}: ${out.verification.detail || "SQL engine and plain-Python reference agree."}`));
      body.append(v);
    }
    if (out.certificate) {
      const c = card("Result certificate");
      c.append(el("pre", "sql", JSON.stringify(out.certificate, null, 2)));
      body.append(c);
    }
    if (s.sql) {
      const q = card("Generated SQL (from the definitions, not hard-coded)");
      q.append(el("pre", "sql", Array.isArray(s.sql) ? s.sql.join("\n\n") : s.sql));
      body.append(q);
    }
  }

  async function loadAudit() {
    const res = await fetch("/v1/audit?n=15");
    const data = await res.json();
    const box = $("audit-body");
    box.replaceChildren(el("p", "", data.verify.ok ? `Chain intact: ${data.verify.entries} entries.` : `Chain BROKEN at entry ${data.verify.broken_at}.`));
    data.entries.slice().reverse().forEach((e) => box.append(el("div", "audit-row", `${e.ts || e.time || ""} ${e.event} ${e.request_id || e.id || ""} ${e.decision || ""} #${String(e.hash || "").slice(0, 12)}`)));
  }

  async function submit() {
    let req;
    try { req = JSON.parse($("formal-json").value); } catch { $("formal-note").textContent = "The request is not valid JSON."; return; }
    $("formal-note").textContent = "Checking (AI + rules)…";
    $("formal-run").disabled = true;
    const r = await post("/v1/requests", req).finally(() => { $("formal-run").disabled = false; });
    if (r.status !== 200) { $("formal-note").textContent = r.body.detail || `Error ${r.status}`; return; }
    last = r.body;
    $("formal-note").textContent = "Done. The Staff desk shows the full trace.";
    renderApplicant(last);
    renderStaff(last);
    loadAudit();
  }

  function aiCard(ai) {
    const c = card("AI check", ai && ai.prohibited);
    if (!ai || ai.status !== "ok") { c.append(el("p", "", `AI check ${ai ? ai.status : "off"}: the rules decided alone.`)); return c; }
    c.append(el("p", "", ai.prohibited ? `Forbidden use found: ${ai.category.replaceAll("_", " ")}.` : `No forbidden use found. Purpose: ${ai.allowed_purpose.replaceAll("_", " ")}.`));
    if (ai.quote) c.append(el("p", "", `Quote: "${ai.quote}" ${ai.quote_verified ? "(found in the request)" : "(not found in the request, ignored)"}`));
    if (ai.reason) c.append(el("p", "", ai.reason));
    c.append(el("p", "form-note", `Model: ${ai.model}. It only saw the request text, never patient data.`));
    return c;
  }

  function showView(name) {
    document.querySelectorAll(".page-view").forEach((v) => { v.hidden = v.id !== `view-${name}`; });
    document.querySelectorAll(".nav-button").forEach((b) => {
      const on = b.dataset.view === name;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
  }

  async function init() {
    document.querySelectorAll(".nav-button").forEach((b) => { b.onclick = () => showView(b.dataset.view); });
    fetch("/v1/requests/ai-status").then((r) => r.json()).then((s) => { $("model-mode").textContent = s.enabled ? "AI check: on" : "AI check: off (rules only)"; }).catch(() => {});
    samples = await (await fetch("/v1/requests/samples")).json();
    const sel = $("formal-sample");
    samples.forEach((s, i) => sel.append(new Option(`${s.id} · ${s.applicant?.organisation || ""} · ${s.requested_as}`, String(i))));
    const pick = () => { $("formal-json").value = JSON.stringify(samples[Number(sel.value)], null, 2); };
    sel.onchange = pick;
    pick();
    $("formal-run").onclick = submit;
    $("audit-refresh").onclick = loadAudit;
    loadAudit();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
