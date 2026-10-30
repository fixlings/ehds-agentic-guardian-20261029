# Health Data Navigator · Version 1

An accessible, dependency-free frontend for the existing EHDS Track 3 API.

This is **not** a second interpreter or a substitute for the Guardian and Evidence
packages. It sends the existing `AgentRequest` contract to the existing
`POST /v1/agent/request` endpoint, then renders the returned `AgentResponse`.
It does not generate numbers, authorise access, suppress data on behalf of the
backend, or send requests directly to a model provider.

## Start the UI and API on the same origin

Install the project dependencies as documented in the root README. From the
repository root, start the offline demonstration:

```bash
python -m uvicorn navigator_app:app --host 127.0.0.1 --port 8000
```

Then visit <http://127.0.0.1:8000/>. On Windows, use
`.\.venv\Scripts\python.exe -m uvicorn navigator_app:app --host 127.0.0.1 --port 8000`.

For the already configured **EU** Mistral runtime, start the server with
`EHDS_USE_LIVE_MODEL=1` in its environment **and** a server-side
`MISTRAL_API_KEY`. Do not enter the key in the browser, commit it or add it to
HTML/JavaScript. `EHDS_USE_LIVE_MODEL` is off by default, so offline tests
never call an external model. Even with live mode enabled, deterministic
forbidden-purpose requests deliberately bypass the model.

## What's covered

- Plain-language request entry with the existing role and stated-purpose fields.
- Three quick-fill demo prompts, matching the existing A/B/C demonstration.
- Validated structured interpretation of population, condition, measure and purpose.
- Distinct views for allow, scientific qualification, clarify, suppress and reject.
- Display of non-suppressed **synthetic** aggregate rows returned by the backend.
- Evidence Agent's unsupported claims and missing evidence; Guardian's reason
  code, purpose-change flag, explanation, and audit ID.
- In-memory follow-up session continuity and tab-local request history; no
  browser persistent storage and no patient-level data display.
- Visible, fail-closed handling for invalid session, model failure, deadline
  and network errors. It never fabricates a successful outcome on an error.
- Responsive desktop/mobile layout, keyboard focus, accessible form labels,
  readable status text, and no external web assets.

## Important limits

- This UI **does not implement authentication**. The role selector is an
  explicit self-declared demonstration role.
- The backend currently offers deterministic small-cell suppression, but
  cross-query disclosure-history/differencing protection has **not been
  verified**. Do not advertise the 72 − 71 attack as prevented by this build.
- The interface deliberately doesn't display `aggregate.population_n` or
  timing summary counters, because they can disclose suppressed populations.
- There is no production dataset, clinician portal, verified EHDS legal-article
  citation, or real user account. Only the seeded synthetic fixture is used.
- A request currently executes in one API call. The right-hand panel reviews
  the **returned** validated interpretation; it is not an approval gate before
  execution. Adding such a gate needs a two-stage backend contract.
- The screen reports `live_model` **per request**, not as a promise that every
  request used the model. Forbidden uses are rejected by deterministic checks.

## Quick checks

```bash
python -m pytest -q tests/ui
node --check web/app.js
```

Recommended presentation sequence:

1. Choose **A · Research statistics**, check it and show the parsed filters
   and returned non-suppressed synthetic rows.
2. Choose **B · Evidence check**, check it and open the evidence limitation.
3. In the same session choose **C · Purpose change**, then show the rejected
   purpose, lack of aggregate and audit reference. To demonstrate a purpose
   change specifically, do not use **Start a new session** between A and C.

## Team integration

This branch adds only `web/**`, `navigator_app.py`, and `tests/ui/**`.
Existing package ownership (`OWNERSHIP.md`), schemas and `/v1/agent/request`
behavior remain untouched. The Navigator is mounted *after* the API route
in a separate ASGI entry point, so the frontend uses a same-origin relative URL
and needs no CORS rule or new dependencies.
