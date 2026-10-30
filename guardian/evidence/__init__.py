"""Evidence investigator and mock query executor. No language-model calls."""

from guardian.evidence.executor import MockQueryExecutor
from guardian.evidence.investigator import EvidenceInvestigator

__all__ = ["MockQueryExecutor", "EvidenceInvestigator"]
