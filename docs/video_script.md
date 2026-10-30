# 2-minute demo script

1. **Problem (15 s).** Health data access bodies get many requests. Each must be checked against the EHDS law, answered with safe numbers, and never leak a patient.
2. **R01, statistics (20 s).** Submit request → R01 → Decide. Approved (Art. 53, 69). Table with `<10` for small groups. "AI check" box: allowed purpose, public health.
3. **D02, pharma sales reps (20 s).** Rejected (Art. 54). The AI quotes the exact sentence that shows marketing. The AI only saw the request text, never patient data.
4. **D03, "does my neighbour have diabetes" (10 s).** Rejected: re-identifying a person.
5. **R05, secure workspace (25 s).** Approved (Art. 53, 66, 68, 73). Run a query inside the workspace; "Try to download" → blocked (HTTP 403).
6. **Staff desk (20 s).** Every step: AI check, rule checks, how definitions were read, generated SQL, independent double-check, certificate, hash-chained audit log.
7. **Trust (10 s).** The AI can only make decisions stricter. If it is down, rules decide alone. `make eval`: 10/10 decisions, 6/6 numbers, 0 leaks.
