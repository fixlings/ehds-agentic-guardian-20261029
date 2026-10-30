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
    letter.append(el("p", "form-note", aiSummary(out.ai_check)));
    if (out.answer && out.answer.error) { const e = card("No numbers", true); e.append(el("p", "", out.answer.error)); body.append(e); return; }
    if (out.decision === "APPROVE_STATISTICAL") {
      const c = card("Privacy-protected statistics");
      c.append(answerTable(out.answer));
      if (out.privacy) c.append(el("p", "", `${out.privacy.hidden} of ${out.privacy.cells} cells hidden by the small-group rule (<10).`));
      (out.held_back || []).forEach((h) => c.append(el("p", "form-note", `Held back: ${h.cell}. Compared with your earlier request ${h.earlier_request} it would reveal a group of fewer than 10 people.`)));
      body.append(c);
      if (out.evidence) body.append(evidenceCard(out.evidence));
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
    body.append(aiCard(out.ai_check));
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
    const req = readForm();
    if (!req.applicant.organisation || !req.purpose.trim() || !req.question.trim()) { $("formal-note").textContent = "Please fill in organisation, purpose and question."; return; }
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

  function aiSummary(ai) {
    if (!ai || ai.status !== "ok") return "Checked by fixed rules.";
    if (ai.prohibited && ai.quote_verified) return `Automated check: the purpose looks like ${ai.category.replaceAll("_", " ")}.`;
    const flagged = (ai.checks || []).some((k) => k.risk !== "low");
    return flagged ? "Automated check: some points were passed to staff for review." : "Automated check: no concerns found.";
  }

  function aiCard(ai) {
    const c = card("AI check", ai && ai.prohibited);
    if (!ai || ai.status !== "ok") { c.append(el("p", "", `AI check ${ai ? ai.status : "off"}: the rules decided alone.`)); return c; }
    c.append(el("p", "", ai.prohibited ? `Forbidden use found: ${ai.category.replaceAll("_", " ")}.` : `No forbidden use found. Purpose: ${ai.allowed_purpose.replaceAll("_", " ")}.`));
    if (ai.quote) c.append(el("p", "", `Quote: "${ai.quote}" ${ai.quote_verified ? "(found in the request)" : "(not found in the request, ignored)"}`));
    if (ai.reason) c.append(el("p", "", ai.reason));
    if (ai.checks && ai.checks.length) {
      const ul = el("ul", "ai-checks");
      ai.checks.forEach((k) => {
        const li = el("li", `risk-${k.risk}`, `${k.risk === "low" ? "✓" : "!"} ${k.label} `);
        li.append(el("strong", "", k.risk));
        if (k.finding) li.append(el("span", "", ` · ${k.finding}`));
        if (k.quote && k.risk !== "low") li.append(el("em", "", ` "${k.quote}"${k.quote_verified ? "" : " (not in the request, ignored)"}`));
        ul.append(li);
      });
      c.append(ul);
    }
    c.append(el("p", "form-note", `Model: ${ai.model}. It only saw the request, never patient data. A finding changes the decision only if its quote is in the request.`));
    return c;
  }

  function evidenceCard(ev) {
    const c = card("What these numbers can and cannot show", ev.supports === "no");
    if (ev.status === "ok") {
      c.append(el("p", "", `Supports the likely conclusion: ${ev.supports}.`));
      if (ev.can_show) c.append(el("p", "", `Can show: ${ev.can_show}`));
      if (ev.cannot_show) c.append(el("p", "", `Cannot show: ${ev.cannot_show}`));
      if (ev.next_steps && ev.next_steps.length) {
        const ul = el("ul"); ev.next_steps.forEach((s) => ul.append(el("li", "", s))); c.append(el("p", "", "For stronger evidence:")); c.append(ul);
      }
    }
    const ul = el("ul"); (ev.caveats || []).forEach((s) => ul.append(el("li", "", s))); c.append(ul);
    c.append(el("p", "form-note", ev.status === "ok" ? `Model: ${ev.model}. It saw only this protected result, never patient data.` : "Fixed caveats (AI evidence review off)."));
    return c;
  }

  const COUNTRIES = [["AT", "Austria"], ["BE", "Belgium"], ["BG", "Bulgaria"], ["HR", "Croatia"], ["CY", "Cyprus"], ["CZ", "Czechia"], ["DK", "Denmark"], ["EE", "Estonia"], ["FI", "Finland"], ["FR", "France"], ["DE", "Germany"], ["GR", "Greece"], ["HU", "Hungary"], ["IE", "Ireland"], ["IT", "Italy"], ["LV", "Latvia"], ["LT", "Lithuania"], ["LU", "Luxembourg"], ["MT", "Malta"], ["NL", "Netherlands"], ["PL", "Poland"], ["PT", "Portugal"], ["RO", "Romania"], ["SK", "Slovakia"], ["SI", "Slovenia"], ["ES", "Spain"], ["SE", "Sweden"], ["CH", "Switzerland (non-EU)"], ["GB", "United Kingdom (non-EU)"], ["US", "United States (non-EU)"]];
  const ACTIVE = "A medication is active on 31 December 2025 if start_date is on or before that day and end_date is empty or on or after that day.";
  const TEMPLATES = {
    "Who is counted": ["Adult = aged 18 or older on 31 December 2025.", "Women = sex 'female'.", "Men = sex 'male'."],
    "Diseases (ICD-10)": ["Type 2 diabetes = any condition with an ICD-10 code starting with E11.", "Breast cancer = any condition with an ICD-10 code starting with C50.", "Atrial fibrillation = any condition with an ICD-10 code starting with I48.", "Hypertension = any condition with an ICD-10 code starting with I10.", "COPD = any condition with an ICD-10 code starting with J44.", "Depression = any condition with an ICD-10 code starting with F32."],
    "Medicines (ATC)": ["Oral anticoagulant = ATC code starting with B01AA or B01AF.", "SGLT2 inhibitor = ATC code starting with A10BK.", "ACE inhibitor = ATC code starting with C09AA; ARB = ATC code starting with C09CA.", ACTIVE],
    "Lab values and measurements": ["Use the latest HbA1c (LOINC 4548-4) per patient.", "Use the latest BMI (LOINC 39156-5) per patient; patients without a BMI are left out."],
    "Hospital stays": ["Inpatient stay = encounter with type 'inpatient'.", "Count stays (not patients) whose start_date is in 2025."],
    "Age groups": ["Age bands on 31 December 2025: 18-29, 30-39, 40-49, 50-59, 60-69, 70-79, 80+.", "Age bands on 31 December 2025: 18-49, 50-64, 65+.", "Age bands on 31 December 2025: 65-74, 75-84, 85+."],
    "What to report (edit the end)": ["Report per region: ", "Report per band: ", "Report per region and age band: ", "Report: "],
  };

  function defRow(text = "") {
    const li = el("li", "def-row");
    const inp = el("input"); inp.type = "text"; inp.value = text; inp.setAttribute("aria-label", "Definition");
    const rm = el("button", "icon-button", "✕"); rm.type = "button"; rm.title = "Remove line";
    rm.onclick = () => { li.remove(); preview(); };
    inp.oninput = preview;
    li.append(inp, rm);
    $("f-defs").append(li);
    return inp;
  }

  let currentId = "";
  function fillForm(s) {
    currentId = s.id || "";
    const a = s.applicant || {};
    $("f-org").value = a.organisation || "";
    $("f-name").value = a.name || "";
    $("f-country").value = a.country || "AT";
    document.querySelector(`input[name="f-kind"][value="${s.requested_as === "health data access application" ? "records" : "stats"}"]`).checked = true;
    $("f-purpose").value = s.purpose || "";
    $("f-question").value = s.question || "";
    $("f-defs").replaceChildren();
    (s.definitions || []).forEach((d) => defRow(d));
    preview();
  }

  function readForm() {
    const records = document.querySelector('input[name="f-kind"]:checked').value === "records";
    return {
      id: currentId || `REQ-${String(Date.now()).slice(-6)}`,
      submitted: new Date().toISOString(),
      applicant: { name: $("f-name").value.trim(), organisation: $("f-org").value.trim(), country: $("f-country").value },
      requested_as: records ? "health data access application" : "health data request",
      requested_format: records ? "pseudonymised records" : "anonymised statistics",
      purpose: $("f-purpose").value,
      question: $("f-question").value,
      definitions: [...$("f-defs").querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean),
    };
  }

  function preview() { $("f-json").textContent = JSON.stringify(readForm(), null, 2); }

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
    COUNTRIES.forEach(([c, n]) => $("f-country").append(new Option(n, c)));
    const pickDef = $("f-def-pick");
    pickDef.append(new Option("+ Add a ready-made line…", ""));
    Object.entries(TEMPLATES).forEach(([g, items]) => {
      const og = document.createElement("optgroup"); og.label = g;
      items.forEach((t) => og.append(new Option(t.length > 90 ? `${t.slice(0, 87)}…` : t, t)));
      pickDef.append(og);
    });
    pickDef.onchange = () => { if (!pickDef.value) return; const inp = defRow(pickDef.value); pickDef.value = ""; preview(); inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); };
    $("f-def-blank").onclick = () => { defRow("").focus(); preview(); };
    ["f-org", "f-name", "f-country", "f-purpose", "f-question"].forEach((id) => { $(id).oninput = preview; $(id).onchange = preview; });
    document.querySelectorAll('input[name="f-kind"]').forEach((r) => { r.onchange = preview; });
    samples = await (await fetch("/v1/requests/samples")).json();
    const sel = $("formal-sample");
    sel.append(new Option("Empty form", "blank"));
    const official = document.createElement("optgroup"); official.label = "Official Track 3 requests";
    const demo = document.createElement("optgroup"); demo.label = "Test requests";
    samples.forEach((s, i) => (s.id.startsWith("R") ? official : demo).append(new Option(`${s.id} · ${s.applicant?.organisation || ""}`, String(i))));
    sel.append(official, demo);
    sel.onchange = () => fillForm(sel.value === "blank" ? { definitions: [""] } : samples[Number(sel.value)]);
    sel.value = "0";
    sel.onchange();
    $("formal-run").onclick = submit;
    $("audit-refresh").onclick = loadAudit;
    loadAudit();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
