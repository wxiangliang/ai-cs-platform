"""守护测试：SetFit 标签空间必须被 SkillRegistry 全覆盖。

模型能输出的每个意图都要有对应 Skill（否则识别对了却回兜底话术）。
以训练数据集 v42 的标签集合为准（与模型 labels.json 一致）。
"""

import csv
from pathlib import Path

from app.chat.skills.registry import skill_registry

DATA = Path("docs/intent/intent_train_v42_project.csv")


def test_all_trainable_intents_have_skills():
    labels = {r["intent"] for r in csv.DictReader(DATA.open(encoding="utf-8"))}
    assert len(labels) == 29, f"标签数变化（{len(labels)}），确认数据集与本测试是否需要同步"

    fallback_id = skill_registry.get("__no_such_intent__").skill_id
    missing = []
    for label in sorted(labels):
        if label == "META.UNKNOWN":
            continue  # UNKNOWN 本身就是兜底 Skill
        skill = skill_registry.get(label)
        if skill.skill_id == fallback_id:
            missing.append(label)
    assert not missing, f"以下意图缺少 Skill 注册（会错误地回兜底话术）：{missing}"


def test_registry_and_skill_md_two_way_coverage():
    """注册表 ↔ skills/ md 双向覆盖锁（Stage 33 后统一，例外显式列出）。

    只有 md 没有注册表的例外：上下文控制意图（规则层+状态机处理，
    不需要独立回复模板）。新增意图必须三步齐：taxonomy → registry → md
    （skills/README.md 第 2 节）。"""
    from app.chat.skills.loader import load_skill_declarations
    from app.chat.skills.registry import _SKILLS

    registry_intents = set(_SKILLS.keys())
    md_intents = set(load_skill_declarations().keys())
    assert registry_intents - md_intents == set(), (
        f"注册表意图缺 skills/ md 声明：{sorted(registry_intents - md_intents)}"
    )
    assert md_intents - registry_intents == {"META.SLOT_ONLY", "META.CORRECTION"}, (
        f"md 例外集合漂移：{sorted(md_intents - registry_intents)}"
    )


def test_capabilities_subdir_not_loaded():
    """skills/capabilities/ 是能力规格（无 front-matter），loader glob
    非递归天然不加载——本测试防止未来有人把 glob 改成递归。"""
    from app.chat.skills.loader import SKILLS_DIR, load_skill_declarations

    assert (SKILLS_DIR / "capabilities").is_dir()
    decls = load_skill_declarations()
    assert not any("next_best_action" in key.lower() for key in decls)
    assert not any("customer_journey" in key.lower() for key in decls)
