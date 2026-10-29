# File ownership

Coordinator owns the contract. Workers implement their packages and do not edit each other's files. If a contract gap blocks you, stop and report it; do not quietly redefine types.

| Path | Owner |
| --- | --- |
| `guardian/contracts/**` | Coordinator |
| `tests/acceptance/**` | Coordinator |
| `examples/**` | Coordinator |
| `OWNERSHIP.md`, `pyproject.toml`, `README.md` | Coordinator |
| `guardian/nlp/**`, `tests/nlp/**` | NLP worker |
| `guardian/evidence/**`, `tests/evidence/**` | Evidence worker |
| `guardian/purpose/**`, `tests/purpose/**` | Purpose worker |
| `guardian/api/**`, `tests/integration/**` | Coordinator, after the three packages exist |

NLP may append aliases in `guardian/contracts/lexicon.json` only for phrases already present in `tests/acceptance/prompts.json`. Do not add codes. Purpose and evidence do not edit the lexicon.

## API

`POST /v1/agent/request` with `AgentRequest`. HTTP 200 returns `AgentResponse` for allow, reject, and clarify. Reject is not an HTTP error. `400` invalid body or unknown `session_id`. `503` EU model unavailable. `504` deadline exceeded.

Omitted `session_id` starts a session. A follow-up must send the returned `session_id`. The response always includes machine-readable `policy` and a plain-language `explanation`. `synthetic` is always true for this demo.

## Orchestration (coordinator will wire this)

1. Load or create `SessionContext`.
2. `PurposeGuardian` may be run on the raw text first. Keyword rejects do not need a model call.
3. `NaturalLanguageInterpreter.interpret` builds a validated `Interpretation`. Budget: `code_lookup` counts as one tool step.
4. `PurposeGuardian.evaluate` is authoritative. Model flags may only add a reject.
5. On reject or clarify, do not call the executor. Still write an `AuditRecord`.
6. On allow with a query: `EvidenceInvestigator.investigate` may `describe_dataset` then `execute_aggregate` (two steps). Total tool steps across the request ≤ 3. One retry, and only for the model call.
7. Explanation text is assembled from the policy, the evidence assessment, and `CAUSATION_LIMITATION_TEXT`. It must not contradict `policy.outcome`.

## Normalizer rules (NLP)

Implement a deterministic normalizer that reads `guardian/contracts/lexicon.json` and the raw message. It must pass the acceptance prompts offline, without Mistral.

- Casefold. Longest alias wins. Match on word boundaries so `bc` does not fire inside another word. Multi-word aliases match as phrases.
- A span inside a negation cue (`not including`, `without`, `ohne`, `nicht`, `nich`, `kein`, `keine`, `excluding`) is negated.
- Negated male with no positive female phrase sets `sex=female`. Negated female sets `sex=male`. "nich die männer" is this case.
- `ohne Adipositas` / "BMI is not obese" fills `exclude_bmi_bands`, not `bmi_bands`.
- `without breast cancer` / `ohne brustkrebs` / `kein brustkrebs` sets `breast_cancer=false`.
- Elevated-BMI aliases expand to overweight and obese.
- `BMI>=30` and its listed aliases set band `obese` only. They do not add ICD-10 E66.
- Disease aliases add an allowlisted `CodedFilter` with `source_text` equal to the matched span. Display and code come from the lexicon, never from the model.
- If `per 1000` or `pro 1000` is present, the measure is `rate_per_1000` even if "prevalence" is also present. Otherwise use the first matching measure cue. Default `count`.
- Generic words (`krebs`, `cancer`, `gewicht`, `weight`) stay in `unresolved_terms` unless a longer alias consumed that span.
- Regex `unknown_code_pattern`: a code token that is not in the lexicon goes to `unresolved_terms` and must not appear on the query.
- A code may be emitted only when an allowlisted alias matched. Discard model-invented codes.
- Causal cues set `StructuredQuery.user_claim` to the raw message.
- If nothing clinical matched, `needs_clarification=true` and `query=null`.
- Follow-up turns inherit prior filters from `session.turns[-1].query` and override fields mentioned in the new message.

Mistral, when used, proposes purpose suspicion flags and a JSON interpretation. Local code re-validates. Host is `https://api.eu.mistral.ai` only. Call `list_models` before a live request. Default model `mistral-small-2603`. If that id is absent, use another id from the EU list and report the substitution. Never call the global host. One retry. Tests use a fake `ChatClient`. Mark live tests with `@pytest.mark.live`.

## Policy rules (purpose)

Read `guardian/contracts/policy_minimum.json`. Casefold substring match over `message + "\n" + stated_purpose`.

Apply `precedence` top to bottom. First match wins as `reason_code`. `roles_always_reject` maps to `role_forbidden`.

Model fail-closed: if rules would allow, but `injection_suspected` or `identity_disclosure_suspected` is true, or `proposed_purpose` is in `forbidden_proposed_purposes`, reject with the matching reason. The model must not turn a reject into allow.

`purpose_changed` is true when `session.purpose_category` is not `unknown` and differs from the category of this decision.

`article_mapping.status` is `unresolved` with empty citation fields. Do not invent EHDS article numbers.

On every decision, append an `AuditRecord`.

## Executor and evidence

`MockQueryExecutor` implements `QueryExecutor` against a seeded fixture that satisfies `FIXTURE_INVARIANTS`. Same query and fixture version always yield the same `query_fingerprint` from `guardian.contracts.hashing.query_fingerprint`.

Filter rows with AND across populated filters. ICD codes match exact or prefix (`C50` matches `C50.9`). A negated code excludes matching rows. LOINC `39156-5` does not drop rows; BMI bands do that. Do not return `row_id`.

If a group's count is below `SUPPRESSION_THRESHOLD`, set that row's `count` to null and `suppressed` true. If the filtered population is below the threshold, suppress the whole result's counts. `AggregateResult.synthetic` is true. Notes must say the figures are synthetic.

`timing` on the result counts `measurement_timing` inside the filtered population.

`EvidenceInvestigator`:

1. `describe` (tool step).
2. If `max_steps` < 2, do not execute. Record missing evidence.
3. Otherwise `execute` (tool step).
4. `causation_supported` is always false.
5. When `user_claim` is set, or the query has breast cancer and a BMI band, add `CAUSATION_LIMITATION_TEXT` to `unsupported_claims` and to the explanation material.
6. `temporal_feasibility` is `insufficient` when the filtered timing summary has `unknown > 0` or `after_diagnosis > 0` and the user asked about order or causation. It is `sufficient_for_order` only when the filtered rows are exclusively `before_diagnosis`. It is `not_applicable` when the query does not ask about time or causation and does not combine breast cancer with BMI.
7. `association_supported` is true only when a non-suppressed count was returned. Say that this is an association in a synthetic fixture, not a causal finding.
8. Publish `guardian/evidence/expected_counts.json` for three queries you choose, including one suppressed group, using `StructuredQuery` objects.

No Docker, no database, no network except the NLP worker's Mistral EU client.
