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
