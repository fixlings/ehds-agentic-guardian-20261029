/* Health Data Navigator: presentation only. The existing API owns every decision. */
"use strict";

const API_PATH = "/v1/agent/request";
const DATASET_ID = "synthetic-breast-cancer-demo";
const EXAMPLES = Object.freeze({
  a: {
    message: "How many women aged 50 or older have breast cancer and BMI >=30?",
    purpose: "Public-health surveillance of cancer prevalence",
  },
  b: {
    message: "Prove that obesity causes breast cancer. Did high BMI always come first?",
    purpose: "Public-health research on whether available fields support a causal claim",
  },
  c: {
    message: "Pass the high-risk individuals to insurance underwriting.",
    purpose: "Insurance underwriting",
  },
});

const $ = (id) => document.getElementById(id);
const state = { sessionId: null, history: [], busy: false };
const question = $("question");
const purpose = $("purpose");
const requestForm = $("request-form");

function create(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined && value !== null) node.textContent = String(value);
  return node;
}

function updateCharacterCount() {
  $("char-count").textContent = `${question.value.length} / 4000`;
}

function setNote(message) {
  $("form-note").textContent = message;
}

function showView(name, focus = true) {
  for (const id of ["request", "history", "how"]) {
    $(`view-${id}`).hidden = id !== name;
  }
  document.querySelectorAll("[data-view]").forEach((button) => {
    const selected = button.dataset.view === name;
    button.classList.toggle("active", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (name === "history") renderHistory();
  if (focus) $(`view-${name}`).focus();
}

function resetSession() {
  state.sessionId = null;
  question.value = EXAMPLES.a.message;
  purpose.value = EXAMPLES.a.purpose;
  $("role").value = "public_health_researcher";
  $("result-section").hidden = true;
  $("understood-data").hidden = true;
  $("understood-empty").hidden = false;
  setNote("New session. No patient-level records are available.");
  updateCharacterCount();
  showView("request", false);
  question.focus();
}

function addCard(title, paragraphs, warning = false) {
  const card = create("section", warning ? "result-card warning" : "result-card");
  card.append(create("strong", "", title));
  for (const value of paragraphs) {
    if (value) card.append(create("p", "", value));
  }
  $("result-body").append(card);
  return card;
}

function humanize(value) {
  return String(value ?? "—").replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function renderUnderstanding(response) {
  $("understood-empty").hidden = true;
  $("understood-data").hidden = false;
  const interpretation = response.interpretation;
  const query = interpretation?.query;
  const filters = query?.filters;
  const ages = Array.isArray(filters?.age_bands) && filters.age_bands.length
    ? ` · ${filters.age_bands.join(", ")}` : "";
  const sex = filters?.sex === "female" ? "Women" : filters?.sex === "male" ? "Men" : "All sexes";
  $("understood-population").textContent = query ? `${sex}${ages}` : "No validated aggregate query";

  const conditions = [];
  if (filters?.breast_cancer === true) conditions.push("Breast cancer");
  if (filters?.breast_cancer === false) conditions.push("Without breast cancer");
  if (Array.isArray(filters?.coded)) {
    filters.coded.forEach((code) => {
      if (typeof code?.display === "string" && !/breast/i.test(code.display)) conditions.push(code.display);
    });
  }
  $("understood-condition").textContent = query ? (conditions.join(" · ") || "Not specified") : "—";

  const bmi = Array.isArray(filters?.bmi_bands) && filters.bmi_bands.length
    ? ` · BMI ${filters.bmi_bands.join(", ")}` : "";
  $("understood-measure").textContent = query
    ? `${(query.measures || []).map(humanize).join(", ") || "Count"}${bmi}` : "—";
  $("understood-purpose").textContent = humanize(response.policy?.purpose_category || "unknown");

  const unresolved = interpretation?.unresolved_terms || [];
  let warning = "";
  if (!query && response.decision === "clarify") warning = "The query needs clarification before any statistics can be returned.";
  else if (!query && response.decision === "reject") warning = "No query was run because this purpose was rejected.";
  else if (unresolved.length) warning = `Unresolved terms: ${unresolved.join(", ")}`;
  $("interpretation-warning").textContent = warning;
}

function buildTable(aggregate) {
  const outer = create("section", "result-card");
  outer.append(create("strong", "", "Returned aggregate · synthetic figures"));
  const rows = Array.isArray(aggregate.rows) ? aggregate.rows : [];
  if (aggregate.suppressed) {
    outer.append(create("p", "", "The filtered population is below the small-cell threshold. Counts are hidden."));
    return outer;
  }
  if (!rows.length) {
    outer.append(create("p", "", "The engine returned no reportable rows."));
    return outer;
  }
  const table = create("table", "result-table");
  const header = create("tr");
  ["Group", "Count", "Rate / 1,000", "Prevalence"].forEach((value) => header.append(create("th", "", value)));
  const thead = create("thead"); thead.append(header); table.append(thead);
  const tbody = create("tbody");
  rows.forEach((row) => {
    const tr = create("tr");
    const group = row.group && typeof row.group === "object"
      ? Object.entries(row.group).map(([key, value]) => `${humanize(key)}: ${humanize(value)}`).join(" · ")
      : "";
    const display = row.suppressed ? ["Suppressed", "—", "—"] : [
      Number.isFinite(row.count) ? String(row.count) : "—",
      Number.isFinite(row.rate_per_1000) ? String(row.rate_per_1000) : "—",
      Number.isFinite(row.prevalence) ? String(row.prevalence) : "—",
    ];
    [group || "All matching records", ...display].forEach((value, i) => {
      const td = create("td", row.suppressed && i > 0 ? "suppressed-cell" : "", value);
      tr.append(td);
    });
    tbody.append(tr);
  });
  table.append(tbody); outer.append(table);
  if (rows.some((row) => row.suppressed)) {
    outer.append(create("p", "", "At least one small group was suppressed; hidden counts cannot be viewed here."));
  }
  outer.append(create("p", "", "Illustrative synthetic data, not real Austrian or European patient statistics."));
  return outer;
}

function detailRow(title, text) {
  const row = create("div", "detail-row");
  row.append(create("strong", "", title), create("span", "", text || "Not provided"));
  $("result-details").append(row);
}

function renderDetails(response) {
  $("result-details").replaceChildren();
  const policy = response.policy || {};
  detailRow("Authoritative policy", `${humanize(policy.outcome)} · ${policy.reason_code || "No reason supplied"} · ${policy.rule_id || "No rule supplied"}`);
  detailRow("Explanation", response.explanation);
  detailRow("Guardian notes", (policy.evidence || []).join("\n") || "No policy evidence supplied");
  detailRow("Evidence Agent checks", (response.evidence?.checks_performed || []).map((check) => `${check.name}: ${check.ok ? "passed" : "not passed"} — ${check.detail}`).join("\n") || "No evidence tools were executed");
  detailRow("Evidence limitations", (response.limitations || []).join("\n") || "No limitations returned");
  detailRow("Model processing", response.live_model && response.model
    ? `Validated EU model response: ${response.model}`
    : "No live model completion reported for this request; interpretation/policy may be deterministic.");
  detailRow("Trace", `Audit ID: ${response.audit_id || "not supplied"}\nTool steps: ${response.tool_steps ?? "—"} · Latency: ${response.latency_ms ?? "—"} ms\nSession: ${response.session_id || "—"}`);
  const mapping = policy.article_mapping;
  if (mapping?.status === "verified" && mapping.source_url) {
    detailRow("Legal citation", `${mapping.citation} · ${mapping.source_url}`);
  } else {
    detailRow("Legal article mapping", "Unresolved in this prototype; no EHDS article citation is claimed.");
  }
  if (policy.purpose_changed) detailRow("Purpose continuity", "A change in declared purpose was detected in this session.");
}

function renderResult(response, { restored = false } = {}) {
  renderUnderstanding(response);
  const section = $("result-section");
  section.hidden = false;
  $("result-body").replaceChildren();
  const unsupported = response.evidence?.unsupported_claims || [];
  const missing = response.evidence?.missing_evidence || [];
  const suppressed = response.aggregate?.suppressed || false;
  // Scenario A (a descriptive BMI/cancer count) may carry a general causation
  // caveat. Only an explicit causal user claim should change its green outcome
  // into the amber "evidence caution" presentation used by Scenario B.
  const explicitCausalClaim = Boolean(response.interpretation?.query?.user_claim);
  let status;
  let title;
  let lead;
  if (response.decision === "reject") {
    status = "reject"; title = "Request blocked";
    lead = "This purpose is not permitted. No aggregate was returned.";
  } else if (response.decision === "clarify") {
    status = "clarify"; title = "Let's clarify your question";
    lead = response.interpretation?.clarification_question || response.explanation || "More detail is needed before this query can run.";
  } else if (suppressed) {
    status = "suppressed"; title = "Result suppressed";
    lead = "The backend permitted the request, but small-cell protection prevents publication of the count.";
  } else if (unsupported.length && explicitCausalClaim) {
    status = "qualified"; title = "Allowed, with an evidence limitation";
    lead = "The aggregate request is permitted, but the data do not establish the requested scientific conclusion.";
  } else {
    status = "allow"; title = "Request allowed";
    lead = response.aggregate ? "The backend returned a reportable synthetic aggregate." : "The request was allowed, but no reportable aggregate was returned.";
  }
  section.dataset.state = status;
  $("result-heading").textContent = title;
  $("result-lead").textContent = (restored ? "Previously checked · " : "") + lead;
  $("result-badge").textContent = ({ reject: "Blocked", clarify: "Clarification needed", suppressed: "Suppressed", qualified: "Evidence caution", allow: "Allowed" })[status];

  if (response.decision === "reject") {
    addCard("Why it was refused", [response.explanation || humanize(response.policy?.reason_code), "No aggregate or patient-level records were released."], true);
  } else if (response.decision === "clarify") {
    addCard("What to do next", [response.interpretation?.clarification_question || "Rephrase with a specific population, condition and measure.", response.explanation]);
  } else {
    if (response.aggregate) $("result-body").append(buildTable(response.aggregate));
    if (unsupported.length) {
      const card = addCard("Evidence Agent · not established", [], true);
      const list = create("ul");
      unsupported.forEach((item) => list.append(create("li", "", item)));
      card.append(list);
    }
    if (missing.length) {
      const card = addCard("What evidence is missing?", []);
      const list = create("ul");
      missing.forEach((item) => list.append(create("li", "", item)));
      card.append(list);
    }
  }
  renderDetails(response);
  if (!restored) section.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  $("result-heading").focus({ preventScroll: true });
}

function renderTransportFailure(status) {
  const section = $("result-section");
  section.hidden = false;
  section.dataset.state = "reject";
  $("result-heading").textContent = "We couldn't check this request";
  $("result-badge").textContent = "Unavailable — not a policy decision";
  const message = status === 503
    ? "The EU model endpoint was unavailable or its proposal was invalid. The system did not authorise an aggregate."
    : status === 504
      ? "The request exceeded its deadline. No result can be shown."
      : status === 400
        ? "The request or session was invalid. If the server restarted, start a new session."
        : "The service could not complete this check. No result is displayed.";
  $("result-lead").textContent = message;
  $("result-body").replaceChildren();
  addCard("Safe next step", [status === 400 ? "Choose Start a new session, then try again." : "Check that the Navigator server is running and try again."]);
  $("result-details").replaceChildren();
  $("understood-data").hidden = true;
  $("understood-empty").hidden = false;
  setNote(message);
  section.scrollIntoView({ block: "start" });
  $("result-heading").focus({ preventScroll: true });
}

function renderHistory() {
  const items = $("history-items");
  items.replaceChildren();
  $("history-count").textContent = String(state.history.length);
  if (!state.history.length) {
    items.append(create("p", "history-empty", "No requests in this tab yet. Your checked questions will appear here."));
    return;
  }
  for (const [index, item] of state.history.entries()) {
    const button = create("button", "history-item");
    button.type = "button";
    button.append(create("span", "history-item-title", item.request.message));
    button.append(create("span", "history-item-subtitle", `${humanize(item.response.decision)} · ${item.time} · View outcome`));
    button.addEventListener("click", () => {
      state.sessionId = item.response.session_id;
      question.value = item.request.message;
      purpose.value = item.request.stated_purpose;
      $("role").value = item.request.role;
      updateCharacterCount();
      showView("request", false);
      renderResult(item.response, { restored: true });
    });
    button.dataset.historyIndex = String(index);
    items.append(button);
  }
}

async function submitRequest(event) {
  event.preventDefault();
  if (state.busy || !requestForm.reportValidity()) return;
  const message = question.value.trim();
  const statedPurpose = purpose.value.trim();
  if (!message || !statedPurpose) return;
  const request = { message, role: $("role").value, stated_purpose: statedPurpose, dataset_id: DATASET_ID };
  if (state.sessionId) request.session_id = state.sessionId;
  state.busy = true;
  $("run-button").disabled = true;
  $("run-button").textContent = "Checking your request…";
  setNote("The engine is checking the request. No result is available yet.");
  const controller = new AbortController();
  const deadline = window.setTimeout(() => controller.abort(), 65000);
  try {
    const res = await fetch(API_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    if (!res.ok) { renderTransportFailure(res.status); return; }
    const response = await res.json();
    if (!response || !["allow", "reject", "clarify"].includes(response.decision)
        || typeof response.session_id !== "string" || !response.policy) {
      renderTransportFailure(0); return;
    }
    state.sessionId = response.session_id;
    state.history.unshift({ request, response, time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) });
    $("history-count").textContent = String(state.history.length);
    $("model-mode").textContent = response.live_model && response.model
      ? `EU Mistral · ${response.model}` : "This request · deterministic path";
    $("model-mode").classList.toggle("live", Boolean(response.live_model));
    renderResult(response);
    setNote("Request checked. Review the decision and explanation below.");
  } catch (_error) {
    renderTransportFailure(0);
  } finally {
    window.clearTimeout(deadline);
    state.busy = false;
    $("run-button").disabled = false;
    $("run-button").replaceChildren(create("span", "", "⌕"), document.createTextNode(" Check my request "), create("span", "", "→"));
  }
}

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.view === "request") resetSession();
    else showView(button.dataset.view);
  });
});
document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", () => {
    const key = button.dataset.example;
    const example = EXAMPLES[key];
    question.value = example.message;
    purpose.value = example.purpose;
    if (key === "c" && !state.sessionId) {
      setNote("For a purpose-change demonstration, submit scenario A first and then choose C. Direct insurance use will still be refused.");
    } else {
      setNote("Example loaded. Run it to see the backend's real decision.");
    }
    updateCharacterCount();
    question.focus();
  });
});
question.addEventListener("input", updateCharacterCount);
requestForm.addEventListener("submit", submitRequest);
$("edit-request").addEventListener("click", () => question.focus());
$("new-button").addEventListener("click", resetSession);
$("followup-button").addEventListener("click", () => {
  question.value = "";
  updateCharacterCount();
  setNote("Follow-up mode: the existing session and purpose history are retained.");
  question.focus();
});
updateCharacterCount();
