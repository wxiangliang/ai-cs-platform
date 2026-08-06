"""能力规格活契约测试（skills/capabilities/）。

锁定四件事：
1. 八个规格 front-matter 齐全合法（id=文件名、status 合法）；
2. 落点锚点全部真实存在（模块/意图/配置/指标）——规格漂移 CI 必红；
3. playbook 参考件入口意图在目录（防规格与实现脱节的意图名复发）；
4. 状态快照锁定（partial 的只有 response_planner/purchase_assist——
   状态变化必须显式改本测试=有意识的决定）。
"""

from app.chat.skills.capability_loader import (
    load_capability_specs,
    validate_capability_anchors,
)


def test_all_specs_load_and_valid():
    specs = load_capability_specs()
    assert set(specs) == {
        "next_best_action", "promotion_guide", "response_planner",
        "customer_journey", "new_user_onboarding", "product_discovery",
        "product_recommendation", "purchase_assist",
    }


def test_anchors_all_exist():
    """落点锚点校验零漂移（模块被删/意图改名/配置移位在此暴露）。"""
    issues = validate_capability_anchors()
    assert issues == [], "\n".join(issues)


def test_status_snapshot():
    specs = load_capability_specs()
    partial = {k for k, v in specs.items() if v["status"] == "partial"}
    assert partial == {"response_planner", "purchase_assist"}
    assert not any(v["status"] == "deferred" for v in specs.values())  # 八能力全归位


def test_implemented_requires_anchors():
    """implemented/partial 必须声明至少一个锚点（不允许空口宣称已实现）。"""
    for cap_id, spec in load_capability_specs().items():
        anchors = spec["implemented_by"]
        assert any(
            anchors.get(k) for k in ("modules", "intents", "configs", "events", "metrics")
        ), cap_id
