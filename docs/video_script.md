# 2-minute demo video script

About 1:45 at a calm pace. Same order as the recorded demo; run the requests without restarting the app.

| # | Screen | Voice-over |
|---|---|---|
| 1 | Submit request page, scroll through the form | Health Data Navigator: a Health Data Access Body for the European Health Data Space. An applicant fills in a simple form. Our Guardian agent and fixed legal rules check every request, and the stricter answer wins. |
| 2 | Start from R01 → Decide; statistics table with <10 | Request R01 asks for diabetes statistics for public health. It is approved under Articles 53 and 69. Groups below ten show as less than ten, and opted-out patients are removed before any calculation. |
| 3 | Start from R02 → Decide; rejection letter and manual review box | Here an insurer wants to set premiums. Rejected under Article 54, with a plain-language letter. The applicant can ask for a manual review. |
| 4 | D10 → Decide (114), then D11 → Decide: held back; request history | Now the classic trick. First: 114 women with breast cancer in Austria. Then the same question without Burgenland. Subtracting would reveal two women, so Guardian holds the number back. The applicant sees their full request history. |
| 5 | D09 → Decide; secure workspace; type SELECT * FROM cohort → Blocked | A justified records request opens a secure workspace. Pseudonymised records stay inside. There is no download, and only summary results can leave. Raw patient rows are blocked. |
| 6 | Staff desk; search D11; open it and scroll through the steps | On the Staff desk, every request can be searched and opened: the rule steps, the AI check, the generated SQL, an independent double check, and a hash-chained audit log. Staff decide manual reviews here. |
| 7 | Staff list and audit log; metrics caption | On the ten official requests: ten of ten decisions, six of six numbers, zero privacy leaks, under two seconds. What does not work yet: there are no logins, the history resets on restart, SAS, R and Python are only planned, and this recording runs on rules only, without a live AI key. |
