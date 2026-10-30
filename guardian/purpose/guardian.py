"""Deterministic purpose guardian. No language-model calls."""

from __future__ import annotations

from guardian.contracts.schemas import (
    AgentRequest,
    ArticleMapping,
    Interpretation,
    PolicyDecision,
    PurposeCategory,
    ReasonCode,
    RequesterRole,
    SessionContext,
)
from guardian.purpose.policy_loader import load_policy_minimum

_CATEGORY_KEY_BY_PRECEDENCE: dict[str, str | None] = {
    "prompt_injection": "prompt_injection",
    "identity_disclosure": "identity_disclosure",
    "role_forbidden": None,
    "purpose_forbidden_insurance": "insurance",
    "purpose_forbidden_employment": "employment",
    "purpose_forbidden_marketing": "marketing",
    "allowed_public_health": None,
}

_REASON_BY_PRECEDENCE: dict[str, ReasonCode] = {
    "prompt_injection": ReasonCode.prompt_injection,
    "identity_disclosure": ReasonCode.purpose_forbidden_identity,
    "role_forbidden": ReasonCode.role_forbidden,
    "purpose_forbidden_insurance": ReasonCode.purpose_forbidden_insurance,
    "purpose_forbidden_employment": ReasonCode.purpose_forbidden_employment,
    "purpose_forbidden_marketing": ReasonCode.purpose_forbidden_marketing,
    "allowed_public_health": ReasonCode.allowed_public_health,
}

_PURPOSE_BY_CATEGORY_KEY: dict[str, PurposeCategory] = {
    "prompt_injection": PurposeCategory.unknown,
    "identity_disclosure": PurposeCategory.identity_disclosure,
    "insurance": PurposeCategory.insurance,
    "employment": PurposeCategory.employment,
    "marketing": PurposeCategory.marketing,
}

_ROLE_TO_PURPOSE: dict[RequesterRole, PurposeCategory] = {
    RequesterRole.insurer: PurposeCategory.insurance,
    RequesterRole.employer: PurposeCategory.employment,
    RequesterRole.marketer: PurposeCategory.marketing,
}

_FORBIDDEN_PROPOSED: dict[PurposeCategory, ReasonCode] = {
    PurposeCategory.insurance: ReasonCode.purpose_forbidden_insurance,
    PurposeCategory.employment: ReasonCode.purpose_forbidden_employment,
    PurposeCategory.marketing: ReasonCode.purpose_forbidden_marketing,
    PurposeCategory.identity_disclosure: ReasonCode.purpose_forbidden_identity,
}


class PurposeGuardian:
    """Evaluate requests against policy_minimum.json with optional model fail-closed flags."""

    def __init__(self) -> None:
        self._policy = load_policy_minimum()

    def evaluate(
        self,
        request: AgentRequest,
        session: SessionContext,
        interpretation: Interpretation,
    ) -> PolicyDecision:
        corpus = f"{request.message}\n{request.stated_purpose}".casefold()
        categories: dict[str, list[str]] = self._policy["categories"]
        roles_reject: set[str] = set(self._policy["roles_always_reject"])

        matched_precedence: str | None = None
        matched_phrase: str | None = None

        for step in self._policy["precedence"]:
            if step == "role_forbidden":
                if request.role.value in roles_reject:
                    matched_precedence = step
                    matched_phrase = f"role={request.role.value}"
                    break
                continue

            if step == "allowed_public_health":
                matched_precedence = step
                break

            category_key = _CATEGORY_KEY_BY_PRECEDENCE.get(step)
            if category_key is None:
                continue

            for phrase in categories.get(category_key, []):
                if phrase.casefold() in corpus:
                    matched_precedence = step
                    matched_phrase = phrase
                    break
            if matched_precedence is not None:
                break

        if matched_precedence is None:
            matched_precedence = "allowed_public_health"

        rule_outcome, reason_code, purpose_category, evidence = self._decision_from_precedence(
            matched_precedence,
            matched_phrase,
            request.role,
        )

        if rule_outcome == "allow":
            fail_closed = self._fail_closed_reject(interpretation)
            if fail_closed is not None:
                reason_code, purpose_category, evidence = fail_closed
                rule_outcome = "reject"

        purpose_changed = (
            session.purpose_category != PurposeCategory.unknown
            and session.purpose_category != purpose_category
        )

        rule_id = (
            f"allow.public_health"
            if reason_code == ReasonCode.allowed_public_health
            else f"deny.{reason_code.value}"
        )

        return PolicyDecision(
            outcome=rule_outcome,
            rule_id=rule_id,
            reason_code=reason_code,
            purpose_category=purpose_category,
            purpose_changed=purpose_changed,
            role=request.role,
            evidence=evidence,
            article_mapping=ArticleMapping(status="unresolved"),
            authoritative=True,
        )

    def _decision_from_precedence(
        self,
        precedence: str,
        matched_phrase: str | None,
        role: RequesterRole,
    ) -> tuple[str, ReasonCode, PurposeCategory, list[str]]:
        reason_code = _REASON_BY_PRECEDENCE[precedence]
        if precedence == "allowed_public_health":
            return (
                "allow",
                reason_code,
                PurposeCategory.public_health_research,
                ["stated purpose is public-health surveillance"],
            )

        if precedence == "role_forbidden":
            purpose_category = _ROLE_TO_PURPOSE.get(role, PurposeCategory.unknown)
            detail = matched_phrase or f"role={role.value}"
            return ("reject", reason_code, purpose_category, [f"matched: {detail}"])

        category_key = _CATEGORY_KEY_BY_PRECEDENCE[precedence]
        assert category_key is not None
        purpose_category = _PURPOSE_BY_CATEGORY_KEY[category_key]
        detail = matched_phrase or category_key
        return (
            "reject",
            reason_code,
            purpose_category,
            [f"matched: {detail}"],
        )

    def _fail_closed_reject(
        self,
        interpretation: Interpretation,
    ) -> tuple[ReasonCode, PurposeCategory, list[str]] | None:
        if interpretation.injection_suspected:
            return (
                ReasonCode.prompt_injection,
                PurposeCategory.unknown,
                ["model flag: injection_suspected"],
            )
        if interpretation.identity_disclosure_suspected:
            return (
                ReasonCode.purpose_forbidden_identity,
                PurposeCategory.identity_disclosure,
                ["model flag: identity_disclosure_suspected"],
            )
        forbidden_reason = _FORBIDDEN_PROPOSED.get(interpretation.proposed_purpose)
        if forbidden_reason is not None:
            purpose = interpretation.proposed_purpose
            return (
                forbidden_reason,
                purpose,
                [f"model proposed purpose: {purpose.value}"],
            )
        return None
