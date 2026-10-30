"""Routes a query to the executor that owns its dataset."""

from __future__ import annotations

from guardian.contracts.dataset import SYNTHETIC_DATASET_ID
from guardian.contracts.schemas import AggregateResult, DatasetDescription, StructuredQuery
from guardian.evidence.executor import MockQueryExecutor
from guardian.evidence.track3_executor import TRACK3_DATASET_ID, Track3QueryExecutor


class RoutingQueryExecutor:
    def __init__(self) -> None:
        self._by_id = {SYNTHETIC_DATASET_ID: MockQueryExecutor(), TRACK3_DATASET_ID: Track3QueryExecutor()}

    def for_dataset(self, dataset_id: str):
        if dataset_id not in self._by_id:
            raise KeyError(dataset_id)
        return self._by_id[dataset_id]

    def describe(self, dataset_id: str) -> DatasetDescription:
        return self.for_dataset(dataset_id).describe(dataset_id)

    def execute(self, query: StructuredQuery) -> AggregateResult:
        return self.for_dataset(query.dataset_id).execute(query)
