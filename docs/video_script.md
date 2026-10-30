# 2-minute video script

1. **(0:00) Problem.** "Under the EHDS, a health data access body must decide quickly and safely who gets which health data. We built that desk."
2. **(0:15) Submit request.** Open http://127.0.0.1:8000. Pick R01 → Decide. Show: "Approved: statistics", Art. 53/69, the table per federal state, `<10` cells.
3. **(0:35) Reject.** Pick R04 (advertising) → Rejected, Art. 54, plain-language letter. No data touched.
4. **(0:50) Secure workspace.** Pick R05 → Approved: secure workspace, Art. 53/66/68/73. Run the count query inside (1,152 patients). Click "Try to download" → blocked (HTTP 403, Art. 73).
5. **(1:10) Staff desk.** Rule-by-rule trace with quotes, how each definition was read, generated SQL, independent double-check, hash-chained audit log.
6. **(1:30) Ask a question.** "How many women aged 50 or older have breast cancer?" → count on the same 20,000 patients, Art. 53. Then "Pass the high-risk individuals to insurance underwriting" → blocked, purpose change detected, Art. 54.
7. **(1:50) Proof.** Terminal: `make eval` → 10/10 decisions, 6/6 numbers, 0 leaks, 10/10 consistency, < 1 s.
