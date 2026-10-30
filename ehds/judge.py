"""Decides REJECT / APPROVE_STATISTICAL / APPROVE_SPE with deterministic rules.

Order (challenge README):
1. Prohibited purpose -> REJECT (Art. 54)
2. Health data request -> APPROVE_STATISTICAL (Art. 53, 69)
3. Access application that explains why statistics are not enough -> APPROVE_SPE (Art. 53, 66, 68, 73)
4. Any other access application -> APPROVE_STATISTICAL (Art. 53, 66, 69)

No AI is involved: every rule is a readable pattern and the matched phrase is
quoted back to the applicant. Request text can not change these rules, so
prompt injection has nothing to steer; it is only flagged for staff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Each prohibited category needs ALL of its pattern groups to match somewhere in
# the purpose + question text. Pairing a topic with an action keeps research
# about e.g. smoking or insurance systems from being rejected by accident.
PROHIBITED = [
    {
        "id": "insurance",
        "label": "setting insurance premiums, cover or credit terms based on health data",
        "groups": [
            r"\binsur\w*|\bunderwrit\w*|\bcredit\b|\bloans?\b|\bbanks?\b|\bmortgages?\b|\blenders?\b",
            r"\bpremium\w*|\bpric\w*|\btariff\w*|\bunderwrit\w*|\brisk[- ]rat\w*|\bexclud\w*|\bcontributions?\b|\bcustomers?\b|\bconditions of loans?\b"
            r"|\bfees?\b|\bmortgages?\b|\bdecide who\b|\bwho gets\b|\bmembers?\b|\bpolic(?:y|ies)holders?\b|\bcover(?:age)?\b",
        ],
    },
    {
        "id": "marketing",
        "label": "advertising or marketing",
        "groups": [
            r"\badvertis\w*|\bmarketing\b|\bpromot\w*|\bsales\b|\bsales ?reps?\b|\brepresentatives\b|\bcampaign\w*"
            r"|\btarget(?:ed|ing)? (?:physicians|doctors|customers|consumers)|\bboost (?:sales|revenue|prescriptions)|\bmarket share\b",
        ],
        "exclude": r"\bhealth promotion\b|\bpromot\w* (?:health|prevention|screening|vaccination)",
    },
    {
        "id": "employment",
        "label": "decisions about employees, job applicants or job offers",
        "groups": [
            r"\bemploye\w*|\bstaff list|\bworkforce|\bjob (?:offer|applicant)\w*|\bhr department|\brecruit\w*|\bhiring\b|\bworkers?\b|\bour staff\b",
            r"\bcontracts?\b|\bhir\w*|\bdismiss\w*|\bfir(?:e|ing)\b|\bpromotion decisions?|\bselect\w*|\bscreen\w*|\bmatch\w*|\bsick leave|\brenew\w*|\bjob offers?\b"
            r"|\blet (?:\w+ )?go\b|\blay ?offs?\b|\bredundan\w*|\bwho to (?:keep|hire|fire)",
        ],
    },
    {
        "id": "harmful_products",
        "label": "developing or marketing harmful products (tobacco, nicotine, alcohol, illicit drugs, weapons, addictive products)",
        "groups": [
            r"\btobacco|\bnicotine|\bcigarett\w*|\bvap(?:e|ing)\b|\balcohol\w*|\bbeer\b|\bspirits\b|\bvodka|\bwhisk(?:e)?y|\bliquor|\bweapon\w*|\bgambling|\billicit drugs?",
            r"\bdevelop\w*|\bflavou?rs?\b|\bproducts?\b|\blaunch\w*|\bposition\w*|\bsell\w*|\bmarket\b|\bbrand\w*",
        ],
        "exclude": r"\bcessation\b|\bquit(?:ting)?\b|\bprevent\w* (?:smoking|alcohol|addiction)",
    },
    {
        "id": "individual_decisions",
        "label": "decisions detrimental to individuals or re-identifying people",
        "groups": [
            r"\bre-?identif\w*|\bidentify (?:individual|specific|the) (?:patients?|persons?|people)|\bmatch\w* (?:the )?(?:records|data) with\b|\bwho has been diagnosed\b|\bnames? and addresses\b|\bdeny\w* (?:treatment|benefits?)"
            r"|\bmy (?:neighbou?r|colleague|employee|boss|wife|husband|partner|ex)\b|\bwhether (?:\w+ ){1,3}(?:has|had|is)\b|\bdoes (?:\w+ ){1,3}have\b|\blist of (?:the )?(?:patients|names)\b",
        ],
    },
]

SPE_JUSTIFICATION = re.compile(
    r"aggregates? (?:are|is) not enough|statistics (?:are|is) not (?:enough|sufficient)"
    r"|aggregated? (?:data|statistics) (?:are|is|would be) (?:not|in)sufficient"
    r"|(?:training|model|analysis) needs? (?:individual|patient)[- ]level|cannot be answered with (?:statistics|aggregates)",
    re.I,
)

INJECTION = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above|the) (?:instructions|rules)|system prompt|you are now"
    r"|disregard (?:the )?(?:rules|instructions)|approve this request|override (?:the )?(?:rules|decision)"
    r"|<\s*/?\s*(?:system|instructions?)\s*>",
    re.I,
)

ALLOWED_PURPOSE_HINTS = [
    (r"\bresearch\w*|\bstud(?:y|ies)\b|\bepidemiolog\w*", "scientific research (Art. 53)"),
    (r"\bpublic health\b|\bprevention\b|\bquality programme|\bimprov\w* (?:care|quality)", "public health and improving care (Art. 53)"),
    (r"\bpolic(?:y|ies)\b|\bplanning\b|\bplan\b", "policy-making and planning (Art. 53)"),
    (r"\bofficial statistics\b|\bstatistics office|\bannual (?:health )?report", "official statistics (Art. 53)"),
    (r"\bteaching\b|\beducation\b|\bmodule\b", "education and teaching (Art. 53)"),
    (r"\btrain\w*|\bvalidat\w*|\balgorithm\w*|\bmodel\b|\bprediction\b", "training and testing algorithms (Art. 53)"),
]


@dataclass
class Decision:
    decision: str
    articles: list[str]
    reason: str
    applicant_text: str
    matched: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _text(req: dict) -> str:
    return " ".join([req.get("purpose", ""), req.get("question", "")])


def _find(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.I)
    return m.group(0) if m else None


def prohibited_hits(text: str) -> list[dict]:
    hits = []
    for cat in PROHIBITED:
        if cat.get("exclude") and re.search(cat["exclude"], text, re.I):
            continue
        quotes = [_find(g, text) for g in cat["groups"]]
        if all(quotes):
            hits.append({"category": cat["id"], "label": cat["label"], "quotes": quotes})
    return hits


def decide(req: dict) -> Decision:
    text = _text(req)
    flags = []
    if INJECTION.search(" ".join([text, " ".join(req.get("definitions", []))])):
        flags.append("Possible prompt injection in the request text. Rules still decided; please review.")

    hits = prohibited_hits(text)
    if hits:
        labels = "; ".join(h["label"] for h in hits)
        quotes = ", ".join(f'"{q}"' for h in hits for q in h["quotes"])
        return Decision(
            "REJECT",
            ["54"],
            f"Prohibited purpose (Art. 54): {labels}. Matched in the request: {quotes}.",
            "Your request is rejected. The EHDS forbids using health data for "
            f"{labels} (Art. 54). No data was accessed. You may submit a new request for an allowed purpose "
            "such as research, public health, policy-making, official statistics or education (Art. 53).",
            hits,
            flags,
        )

    allowed = [label for pat, label in ALLOWED_PURPOSE_HINTS if re.search(pat, text, re.I)]
    purpose_note = ", ".join(dict.fromkeys(allowed)) or "an allowed purpose under Art. 53"
    if not allowed:
        return Decision(
            "REJECT",
            ["53"],
            "No permitted purpose (Art. 53) could be recognised in the purpose or question.",
            "Your request is rejected because it does not state a purpose the EHDS allows (Art. 53): research, "
            "public health, policy-making, official statistics, education or developing algorithms. No data was "
            "accessed. Please submit a new request that explains what the data will be used for.",
            [{"category": "no_permitted_purpose", "quotes": []}],
            flags,
        )

    kind = req.get("requested_as", "").strip().lower()
    if kind == "health data request":
        return Decision(
            "APPROVE_STATISTICAL",
            ["53", "69"],
            f"Allowed purpose: {purpose_note}. Anonymised statistics answer the question (Art. 69).",
            "Your request is approved. You receive anonymised statistics. Groups with fewer than 10 patients "
            "are shown as '<10' to protect privacy.",
            flags=flags,
        )
    if kind != "health data access application":
        flags.append(f"Unknown requested_as '{req.get('requested_as')}'; treated as an access application.")
    just = SPE_JUSTIFICATION.search(text)
    if just:
        return Decision(
            "APPROVE_SPE",
            ["53", "66", "68", "73"],
            f"Allowed purpose: {purpose_note}. The applicant explains why statistics are not enough "
            f'("{just.group(0)}"), so pseudonymised data is justified (Art. 66). Access only under a data permit '
            "inside a secure processing environment (Art. 68, 73).",
            "Your application is approved under a data permit. The selected pseudonymised records are available "
            "in a secure processing environment. You can analyse them there but cannot download them; only "
            "reviewed, anonymised results may leave.",
            [{"category": "spe_justification", "quotes": [just.group(0)]}],
            flags,
        )
    return statistics_instead(purpose_note, flags)


def statistics_instead(purpose_note: str, flags: list[str], why: str = "") -> Decision:
    return Decision(
        "APPROVE_STATISTICAL",
        ["53", "66", "69"],
        f"Allowed purpose: {purpose_note}. {why}The application does not explain why statistics are not enough, so "
        "pseudonymised records are not justified (Art. 66). Approved as anonymised statistics instead (Art. 69).",
        "Your application is approved as anonymised statistics instead of records, because the question can be "
        "answered with statistics (Art. 66). If you need individual records, apply again and explain why "
        "statistics are not enough.",
        flags=flags,
    )
