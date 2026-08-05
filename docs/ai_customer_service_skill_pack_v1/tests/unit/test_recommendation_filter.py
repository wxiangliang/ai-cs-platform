from app.chat.recommendation.constraint_filter import filter_candidates
from app.chat.recommendation.schemas import ProductCandidate, RecommendationRequest


def test_hard_constraints_filter_over_budget_and_stock() -> None:
    request = RecommendationRequest(
        category="air_conditioner",
        hard_constraints={"budget_max": 3000, "region": "TW"},
    )
    candidates = [
        ProductCandidate(
            product_id="A",
            facts={
                "price": 2800,
                "stock_status": "IN_STOCK",
                "allowed_regions": ["TW"],
            },
        ),
        ProductCandidate(
            product_id="B",
            facts={
                "price": 3500,
                "stock_status": "IN_STOCK",
                "allowed_regions": ["TW"],
            },
        ),
        ProductCandidate(
            product_id="C",
            facts={
                "price": 2500,
                "stock_status": "OUT_OF_STOCK",
                "allowed_regions": ["TW"],
            },
        ),
    ]

    accepted, rejected = filter_candidates(request, candidates)
    assert [item.product_id for item in accepted] == ["A"]
    assert rejected["B"] == ["over_budget"]
    assert rejected["C"] == ["out_of_stock"]
