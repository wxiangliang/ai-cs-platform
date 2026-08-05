from __future__ import annotations

from .schemas import ProductCandidate, RankedProduct, RecommendationRequest


class RuleBasedRanker:
    """Cold-start ranker. Replace only after real behavior data is available."""

    def rank(
        self,
        request: RecommendationRequest,
        candidates: list[ProductCandidate],
    ) -> list[RankedProduct]:
        ranked: list[RankedProduct] = []

        for candidate in candidates:
            facts = candidate.facts
            match = self._match_score(request, facts)
            budget = self._budget_score(request, facts)
            feature = self._feature_score(request, facts)
            availability = 1.0 if facts.get("stock_status") == "IN_STOCK" else 0.0
            preference = self._preference_score(request, facts)
            commercial = max(0.0, min(candidate.commercial_weight, 1.0))

            score_breakdown = {
                "need_match": match * 0.45,
                "budget_fit": budget * 0.15,
                "feature_fit": feature * 0.15,
                "availability": availability * 0.10,
                "preference_fit": preference * 0.10,
                "commercial_weight": commercial * 0.05,
            }
            score = sum(score_breakdown.values())

            ranked.append(
                RankedProduct(
                    product_id=candidate.product_id,
                    score=score,
                    score_breakdown=score_breakdown,
                    reasons=self._reasons(request, facts),
                    tradeoffs=self._tradeoffs(request, facts),
                    fact_snapshot={
                        "price": facts.get("price"),
                        "stock_status": facts.get("stock_status"),
                    },
                )
            )

        return sorted(ranked, key=lambda item: item.score, reverse=True)

    @staticmethod
    def _match_score(request: RecommendationRequest, facts: dict) -> float:
        return float(facts.get("need_match_score", 0.5))

    @staticmethod
    def _budget_score(request: RecommendationRequest, facts: dict) -> float:
        budget_max = request.hard_constraints.get("budget_max")
        price = facts.get("price")
        if budget_max is None or price is None:
            return 0.5
        return max(0.0, min(1.0, 1 - float(price) / max(float(budget_max), 1.0) + 0.5))

    @staticmethod
    def _feature_score(request: RecommendationRequest, facts: dict) -> float:
        desired = set(request.preferences.get("nice_to_have_features", []))
        if not desired:
            return 0.5
        actual = set(facts.get("features", []))
        return len(desired & actual) / len(desired)

    @staticmethod
    def _preference_score(request: RecommendationRequest, facts: dict) -> float:
        preferred_brand = request.preferences.get("brand_preference")
        if not preferred_brand:
            return 0.5
        return 1.0 if facts.get("brand") == preferred_brand else 0.0

    @staticmethod
    def _reasons(request: RecommendationRequest, facts: dict) -> list[str]:
        return list(facts.get("recommendation_reasons", []))

    @staticmethod
    def _tradeoffs(request: RecommendationRequest, facts: dict) -> list[str]:
        return list(facts.get("tradeoffs", []))
