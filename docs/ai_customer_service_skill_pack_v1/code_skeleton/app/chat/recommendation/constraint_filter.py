from __future__ import annotations

from .schemas import ProductCandidate, RecommendationRequest


def filter_candidates(
    request: RecommendationRequest,
    candidates: list[ProductCandidate],
) -> tuple[list[ProductCandidate], dict[str, list[str]]]:
    accepted: list[ProductCandidate] = []
    rejected: dict[str, list[str]] = {}

    for candidate in candidates:
        reasons: list[str] = []
        facts = candidate.facts
        constraints = request.hard_constraints

        budget_max = constraints.get("budget_max")
        if budget_max is not None and facts.get("price") is not None:
            if float(facts["price"]) > float(budget_max):
                reasons.append("over_budget")

        if facts.get("stock_status") != "IN_STOCK":
            reasons.append("out_of_stock")

        region = constraints.get("region")
        allowed_regions = facts.get("allowed_regions")
        if region and allowed_regions and region not in allowed_regions:
            reasons.append("region_not_supported")

        required_features = set(constraints.get("must_have_features", []))
        actual_features = set(facts.get("features", []))
        if not required_features.issubset(actual_features):
            reasons.append("missing_required_feature")

        if reasons:
            rejected[candidate.product_id] = reasons
        else:
            accepted.append(candidate)

    return accepted, rejected
