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

  function renderApplicant(out, req = {}) {
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
    if (req.requested_as === "health data access application" && out.decision === "APPROVE_STATISTICAL") letter.append(el("p", "form-note", "You asked for records in a secure workspace. You receive statistics instead, because the request does not explain why statistics are not enough."));
    if (out.answer && out.answer.error) { const e = card("Not processed", true); e.append(el("p", "", out.answer.error)); body.append(e); return; }
    if (out.decision === "APPROVE_STATISTICAL") {
      const c = card("Privacy-protected statistics");
      c.append(answerTable(out.answer));
      if (out.privacy) c.append(el("p", "", `${out.privacy.hidden} of ${out.privacy.cells} cells hidden by the small-group rule (<10).`));
      (out.held_back || []).forEach((h) => c.append(el("p", "form-note", `Held back: ${h.cell}. Compared with your earlier request ${h.earlier_request} it would reveal a group of fewer than 10 people.`)));
      body.append(c);
      if (out.evidence) body.append(evidenceCard(out.evidence));
    }
    if (out.decision === "APPROVE_SPE") {
      if (out.workspace) body.append(workspacePanel(out));
      else { const w = card("Secure workspace not opened", true); w.append(el("p", "", "The cohort could not be confirmed. A staff member will review the request.")); body.append(w); }
    }
  }

  const EXAMPLES = {
    cohort: ["Count patients", "SELECT count(*) AS patients FROM cohort"],
    conditions: ["Diagnoses", "SELECT icd10, count(DISTINCT patient_id) AS patients FROM conditions GROUP BY icd10 ORDER BY patients DESC"],
    medications: ["Medicines", "SELECT atc, name, count(DISTINCT patient_id) AS patients FROM medications GROUP BY atc, name ORDER BY patients DESC"],
    observations: ["Lab values", "SELECT loinc, name, round(avg(value), 2) AS mean_value, count(*) AS measurements FROM observations GROUP BY loinc, name"],
    encounters: ["Hospital stays", "SELECT type, count(*) AS stays FROM encounters GROUP BY type"],
  };

  function workspacePanel(out) {
    const w = out.workspace;
    const permit = encodeURIComponent(w.permit);
    const c = el("section", "workspace");
    const head = el("div", "ws-head");
    head.append(el("span", "ws-title", `Secure workspace · permit ${w.permit}`));
    c.append(head, el("p", "ws-note", "Pseudonymised records (no names) for your approved cohort. You analyse them here; the data cannot be downloaded. Only reviewed, anonymised results may leave."));
    const tiles = el("div", "ws-tiles");
    const tile = (k, v) => { const t = el("div", "ws-tile"); t.append(el("b", "", v), el("span", "", k)); tiles.append(t); };
    tile("patients", w.patients);
    Object.entries(w.rows || {}).forEach(([t, n]) => tile(`${t} rows`, n));
    c.append(tiles);
    const schema = el("div", "ws-schema");
    c.append(el("span", "ws-label", "Tables"), schema);
    const q = el("textarea", "ws-sql"); q.rows = 3; q.value = EXAMPLES.cohort[1]; q.setAttribute("aria-label", "Query");
    const chips = el("div", "ws-chips");
    const res = el("div", "ws-result");
    fetch(`/v1/workspaces/${permit}`).then((r) => r.json()).then((info) => {
      (info.tables || []).forEach((t) => {
        const row = el("div", "ws-table");
        row.append(el("b", "", t.name), el("span", "", `${t.rows} rows · ${t.columns.join(", ")}`));
        schema.append(row);
        const ex = EXAMPLES[t.name];
        if (ex) { const b = el("button", "ws-chip", ex[0]); b.type = "button"; b.onclick = () => { q.value = ex[1]; run.click(); }; chips.append(b); }
      });
    });
    const run = el("button", "secondary-button", "Run query"); run.type = "button";
    run.onclick = async () => {
      const r = await post(`/v1/workspaces/${permit}/query`, { sql: q.value });
      if (r.status !== 200) { res.replaceChildren(el("p", "ws-blocked", `Blocked: ${r.body.detail}`)); return; }
      res.replaceChildren(table(r.body.columns, r.body.rows), el("p", "form-note", `${r.body.total_rows} rows${r.body.total_rows > r.body.rows.length ? ` (first ${r.body.rows.length} shown)` : ""}. ${r.body.note}`));
    };
    const lock = el("div", "ws-lock", "Download is not possible. There is no export: data and patient IDs never leave this workspace (Art. 73). Only summary results are shown here.");
    const actions = el("div", "ws-actions"); actions.append(run);
    const tools = el("div", "ws-tools");
    tools.append(el("span", "ws-label", "Analysis tools"), el("span", "ws-planned", "Planned"));
    ["SQL (available now)", "SAS", "PSPP", "R", "Python"].forEach((t, i) => tools.append(el("span", i ? "ws-tool off" : "ws-tool", t)));
    tools.append(el("p", "ws-note", "Planned: SAS, PSPP, R and Python sessions inside this workspace, with the same rules: no download, only reviewed summary results leave."));
    c.append(lock, el("span", "ws-label", "Try a query"), chips, q, actions, res, tools);
    return c;
  }

  const pill = (decision) => el("span", `pill pill-${decision}`, LABELS[decision] || decision);

  function requestCard(req, received) {
    const c = card("The request");
    const dl = el("dl", "req-grid");
    const a = req.applicant || {};
    const add = (k, v) => { if (!v) return; dl.append(el("dt", "", k)); const dd = el("dd"); if (v instanceof Node) dd.append(v); else dd.textContent = v; dl.append(dd); };
    add("Received", received ? new Date(received).toLocaleString() : "");
    add("Organisation", a.organisation); add("Contact", a.name); add("Country", a.country);
    add("Asked for", `${req.requested_as || ""} (${req.requested_format || ""})`);
    add("Records", (req.requested_records || []).join(", "));
    add("Purpose", req.purpose); add("Question", req.question);
    const ol = el("ol", "req-defs"); (req.definitions || []).forEach((d) => ol.append(el("li", "", d)));
    add("Definitions", ol);
    c.append(dl);
    return c;
  }

  function decisionCard(out) {
    const c = card("Decision");
    const top = el("div", "dec-top"); top.append(pill(out.decision), el("span", "form-note", `EHDS Art. ${(out.articles || []).join(", ")}`));
    c.append(top, el("p", "", out.reason), el("p", "form-note", `Letter sent: ${out.applicant_letter}`));
    if (out.answer && out.answer.error) c.append(el("p", "ws-blocked", out.answer.error));
    else if (out.decision === "APPROVE_STATISTICAL") c.append(answerTable(out.answer));
    (out.held_back || []).forEach((h) => c.append(el("p", "form-note", `Held back: ${h.cell} (would reveal fewer than 10 people compared with ${h.earlier_request}).`)));
    if (out.workspace) c.append(el("p", "", `Secure workspace ${out.workspace.permit}: ${out.workspace.patients} patients, ${Object.entries(out.workspace.rows || {}).map(([t, n]) => `${t} ${n} rows`).join(", ") || "cohort only"}. Downloads blocked.`));
    return c;
  }

  function renderStaff(out, req, received) {
    const body = $("staff-body");
    body.replaceChildren();
    const s = out.staff || {};
    body.append(requestCard(req || {}, received), decisionCard(out));
    const steps = card("How the decision was made");
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
    if (out.evidence) body.append(evidenceCard(out.evidence));
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

  let staffRows = [];
  async function loadStaff() {
    staffRows = await (await fetch("/v1/staff/requests")).json();
    drawStaff();
  }

  function drawStaff() {
    const q = $("s-search").value.toLowerCase().trim();
    const dec = $("s-decision").value, kind = $("s-kind").value, rev = $("s-review").checked;
    const rows = staffRows.filter((r) => (!q || `${r.id} ${r.organisation} ${r.purpose}`.toLowerCase().includes(q))
      && (!dec || r.decision === dec) && (!kind || r.asked === kind) && (!rev || r.flags > 0));
    $("s-count").textContent = `${rows.length} of ${staffRows.length} requests`;
    const tb = $("s-rows");
    tb.replaceChildren();
    rows.forEach((r) => {
      const tr = el("tr", "s-row"); tr.tabIndex = 0;
      tr.onclick = () => openStaff(r.seq);
      tr.onkeydown = (e) => { if (e.key === "Enter") openStaff(r.seq); };
      const org = el("td"); org.append(el("b", "", r.organisation || "—"), el("small", "", r.purpose));
      const d = el("td"); d.append(pill(r.decision));
      tr.append(el("td", "", new Date(r.received).toLocaleTimeString()), el("td", "s-id", r.id), org, el("td", "", r.asked), d,
        el("td", r.flags ? "s-flag" : "", r.flags ? `${r.flags} flag${r.flags > 1 ? "s" : ""}` : "—"));
      tb.append(tr);
    });
    $("s-empty").hidden = rows.length > 0;
  }

  async function openStaff(seq) {
    const e = await (await fetch(`/v1/staff/requests/${seq}`)).json();
    renderStaff(e.result, e.request, e.received);
    showView("staff");
    $("staff-list").hidden = true;
    $("staff-detail").hidden = false;
    window.scrollTo(0, 0);
  }

  function showList() { $("staff-detail").hidden = true; $("staff-list").hidden = false; loadStaff(); loadAudit(); }

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
    const note = $("formal-note");
    note.textContent = "Done. ";
    const open = el("button", "text-button", "Review it on the Staff desk →"); open.type = "button";
    open.onclick = () => openStaff(last.seq);
    note.append(open);
    renderApplicant(last, req);
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
    "Records: which patients (edit the end)": ["Cohort = adults with ", "Report the size of the cohort you would make available."],
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

  const JUST = /aggregates? (?:are|is) not enough|statistics (?:are|is) not (?:enough|sufficient)/i;
  const REC_WORDS = {
    conditions: /diagnos|condition|icd/i,
    medications: /medic|drug|prescri|\batc\b/i,
    observations: /\blab|hba1c|observation|loinc|values?\b/i,
    encounters: /hospital|stay|encounter|admission/i,
  };

  function splitPurpose(p) {
    const parts = (p || "").match(/[^.!?]+[.!?]*/g) || [];
    const i = parts.findIndex((x) => JUST.test(x));
    if (i < 0) return [p || "", ""];
    const why = parts.slice(i).join("").trim().replace(/^statistics (?:are|is) not (?:enough|sufficient):\s*/i, "");
    return [parts.slice(0, i).join("").trim(), why];
  }

  function ensureCohortLines() {
    const vals = [...$("f-defs").querySelectorAll("input")].map((i) => i.value.trim().toLowerCase());
    if (!vals.some((v) => v.startsWith("cohort"))) defRow("Cohort = adults with ");
    if (!vals.some((v) => v.includes("size of the cohort"))) defRow("Report the size of the cohort you would make available.");
  }

  let currentId = "";
  function fillForm(s) {
    currentId = s.id || "";
    const a = s.applicant || {};
    $("f-org").value = a.organisation || "";
    $("f-name").value = a.name || "";
    $("f-country").value = a.country || "AT";
    document.querySelector(`input[name="f-kind"][value="${s.requested_as === "health data access application" ? "records" : "stats"}"]`).checked = true;
    const records = s.requested_as === "health data access application";
    const [purpose, why] = records ? splitPurpose(s.purpose) : [s.purpose || "", ""];
    $("f-purpose").value = purpose;
    $("f-question").value = s.question || "";
    $("f-why").value = why;
    const recs = s.requested_records ||
      (records ? Object.keys(REC_WORDS).filter((k) => REC_WORDS[k].test(s.question || "")) : []);
    document.querySelectorAll('input[name="f-rec"]').forEach((c) => { c.checked = recs.includes(c.value); });
    toggleRecords();
    $("f-defs").replaceChildren();
    (s.definitions || []).forEach((d) => defRow(d));
    preview();
  }

  function readForm() {
    const records = document.querySelector('input[name="f-kind"]:checked').value === "records";
    const why = records ? $("f-why").value.trim() : "";
    const recs = [...document.querySelectorAll('input[name="f-rec"]:checked')].map((c) => c.value);
    return {
      id: currentId || `REQ-${String(Date.now()).slice(-6)}`,
      submitted: new Date().toISOString(),
      applicant: { name: $("f-name").value.trim(), organisation: $("f-org").value.trim(), country: $("f-country").value },
      requested_as: records ? "health data access application" : "health data request",
      requested_format: records ? "pseudonymised records" : "anonymised statistics",
      purpose: why ? `${$("f-purpose").value.trim()} ${JUST.test(why) ? why : `Statistics are not enough: ${why}`}` : $("f-purpose").value,
      ...(records && recs.length ? { requested_records: recs } : {}),
      question: $("f-question").value,
      definitions: [...$("f-defs").querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean),
    };
  }

  function toggleRecords() { $("f-records-box").hidden = document.querySelector('input[name="f-kind"]:checked').value !== "records"; }

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
    document.querySelectorAll(".nav-button").forEach((b) => { b.onclick = () => { showView(b.dataset.view); if (b.dataset.view === "staff") showList(); }; });
    $("staff-back").onclick = showList;
    $("s-search").oninput = drawStaff;
    ["s-decision", "s-kind", "s-review"].forEach((id) => { $(id).onchange = drawStaff; });
    $("s-load").onclick = async () => {
      $("s-load").disabled = true; $("s-count").textContent = "Running the example requests…";
      for (const smp of samples) await post("/v1/requests", smp);
      $("s-load").disabled = false; loadStaff(); loadAudit();
    };
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
    document.querySelectorAll('input[name="f-rec"]').forEach((c) => { c.onchange = preview; });
    ["f-org", "f-name", "f-country", "f-purpose", "f-question", "f-why"].forEach((id) => { $(id).oninput = preview; $(id).onchange = preview; });
    document.querySelectorAll('input[name="f-kind"]').forEach((r) => { r.onchange = () => { toggleRecords(); if (r.value === "records") ensureCohortLines(); preview(); }; });
    samples = await (await fetch("/v1/requests/samples")).json();
    const sel = $("formal-sample");
    sel.append(new Option("Empty form", "blank"));
    const official = document.createElement("optgroup"); official.label = "Official Track 3 requests";
    const demo = document.createElement("optgroup"); demo.label = "Test requests";
    samples.forEach((s, i) => (s.id.startsWith("R") ? official : demo).append(new Option(`${s.id} · ${s.applicant?.organisation || ""}`, String(i))));
    sel.append(official, demo);
    sel.onchange = () => fillForm(sel.value === "blank" ? { definitions: [""], requested_records: ["conditions"] } : samples[Number(sel.value)]);
    sel.value = "0";
    sel.onchange();
    $("formal-run").onclick = submit;
    $("audit-refresh").onclick = loadAudit;
    loadAudit();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
