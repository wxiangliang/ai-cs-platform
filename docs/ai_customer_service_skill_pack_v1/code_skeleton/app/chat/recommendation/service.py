from __future__ import annotations

from collections.abc import Callable

from .constraint_filter import filter_candidates
from .ranker import RuleBasedRanker
from .schemas import (
    ProductCandidate,
    RecommendationRequest,
    RecommendationResult,
)


class ProductRecommendationService:
    def __init__(
        self,
        recall: Callable[[RecommendationRequest], list[ProductCandidate]],
        ranker: RuleBasedRanker | None = None,
    ) -> None:
        self._recall = recall
        self._ranker = ranker or RuleBasedRanker()

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        recalled = self._recall(request)
        accepted, rejected = filter_candidates(request, recalled)
        ranked = self._ranker.rank(request, accepted)[: request.max_items]

        return RecommendationResult(
            items=ranked,
            recalled_count=len(recalled),
            filtered_count=len(rejected),
            filter_reasons=rejected,
            reason_codes=["hard_filter_applied", "rule_ranker_v1"],
        )
