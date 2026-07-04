"""构建意图训练数据集 v42（项目对齐版）。

输入：docs/intent/intent_train_v41_clean_nodup.csv（原始，只读）
输出：docs/intent/intent_train_v42_project.csv（标签与 intent_taxonomy.md 完全一致）

处理规则（见 docs/intent/README.md 第 2/3 节）：
1. 只保留 trainable_for_classifier=True；
2. 剔除 SAFETY.ABUSE（护栏职责）与上下文敏感微量类（SLOT_ONLY/CLARIFY_REPLY/CORRECTION）；
3. 按 taxonomy 2.1 别名对照表映射标签；
4. 程序化生成 FAQ.GENERAL 样本（v41 完全缺失该意图）；
5. 文本去重；train_seed 归入 train。

用法：uv run python scripts/build_intent_dataset.py [--extra 增量标注文件.csv ...]
      增量文件格式：text,intent[,split]（split 缺省 train），用于线上 bad case 回流
      （SETFIT_LOW_CONF / 人工纠正样本，见 docs/testing/intent_eval_set.md 第 4 节）。
"""

import argparse
import csv
import random
from collections import Counter
from pathlib import Path

SRC = Path("docs/intent/intent_train_v41_clean_nodup.csv")
DST = Path("docs/intent/intent_train_v42_project.csv")

# v41 → taxonomy 规范码映射（taxonomy 2.1 别名对照表）
LABEL_MAP = {
    "ORDER.QUERY": "ORDER.QUERY_STATUS",
    "ORDER.CHANGE_INFO": "ORDER.CHANGE_ADDRESS",
    "PRODUCT.ASK_ATTR": "PRODUCT.ASK_INFO",
    "PROMOTION.NEGOTIATE": "PRODUCT.ASK_PRICE",
    "META.HANDOFF_REQUEST": "META.TRANSFER_HUMAN",
    "CHITCHAT.GREETING": "CHITCHAT.GENERAL",
}

# 剔除的标签：护栏职责 / 上下文敏感（由规则+状态机判定，不进语义模型）
DROP_LABELS = {"SAFETY.ABUSE", "META.SLOT_ONLY", "META.CLARIFY_REPLY", "META.CORRECTION"}

# split 归一
SPLIT_MAP = {"train_seed": "train"}


def generate_faq_general(n: int = 600, seed: int = 42) -> list[str]:
    """程序化生成 FAQ.GENERAL 样本：政策/规则类问法 × 主题组合。

    风格对齐 v41 语料（口语前后缀）；主题刻意避开与既有意图强冲突的表达
    （如具体运费计算归 LOGISTICS.SHIPPING_FEE、开发票动作归 PAYMENT.INVOICE）。
    """
    prefixes = ["", "请问", "麻烦问下", "想问一下", "你好，", "客服，", "就是", "那个",
                "在吗，", "问一下哈，", "我想了解下", "咨询下"]
    suffixes = ["", "呢", "哈", "谢谢", "？", "，可以说下吗", "，麻烦了", "帮我看下", "，谢啦"]
    cores = [
        # 退换货政策总述（区别于 AFTERSALE.RETURN 的"我要退货"动作）
        "退换货政策是什么", "你们退换货有什么规定", "七天无理由退货是什么意思",
        "什么情况不支持退货", "退换货的条件是什么", "无理由退货的规则说一下",
        "退货政策能介绍下吗", "换货有什么要求",
        # 保修政策（区别于 AFTERSALE.REPAIR 的"我要报修"）
        "保修政策是怎样的", "保修期一般是多久", "保修范围包括哪些", "人为损坏保修吗",
        "过保之后维修收费吗", "全国联保吗", "保修卡丢了还能保修吗",
        # 会员/积分规则
        "会员等级怎么划分", "会员有什么权益", "怎么成为会员", "会员可以退吗",
        "积分怎么获得", "积分会过期吗", "积分有什么用", "积分规则说明一下",
        "会员日是哪天", "升级会员有什么条件",
        # 价保/平台规则
        "价保政策是什么", "买贵了可以退差价吗", "价格保护期是多久",
        "评价可以修改吗", "订单申诉的流程是什么", "你们平台规则在哪里看",
        # 服务信息
        "客服的工作时间是几点到几点", "你们营业时间是什么时候", "有线下门店吗",
        "支持门店自提吗", "自提点在哪里", "节假日发货吗有什么规定",
        # 隐私/账号
        "账号怎么注销", "个人信息怎么保护的", "隐私政策在哪里看",
    ]
    rng = random.Random(seed)
    pool = {f"{p}{c}{s}" for p in prefixes for c in cores for s in suffixes}
    samples = rng.sample(sorted(pool), min(n, len(pool)))
    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--extra", nargs="*", default=[],
        help="增量标注 CSV（text,intent[,split]），bad case 回流入口",
    )
    args = parser.parse_args()

    rows = list(csv.DictReader(SRC.open(encoding="utf-8")))

    out: list[dict[str, str]] = []
    seen_texts: set[str] = set()
    dropped = Counter()

    for r in rows:
        if r["trainable_for_classifier"] != "True":
            dropped["not_trainable"] += 1
            continue
        label = r["intent"]
        if label in DROP_LABELS:
            dropped[f"label:{label}"] += 1
            continue
        label = LABEL_MAP.get(label, label)
        text = r["text"].strip()
        if not text or text in seen_texts:
            dropped["dup_or_empty"] += 1
            continue
        seen_texts.add(text)
        split = SPLIT_MAP.get(r["split"], r["split"])
        out.append({"text": text, "intent": label, "split": split, "source": r["source"] or "v41"})

    # 增量标注合并（回流样本；意图码必须是 taxonomy 规范码）
    for extra_path in args.extra:
        extra_count = 0
        for r in csv.DictReader(Path(extra_path).open(encoding="utf-8")):
            text = (r.get("text") or "").strip()
            intent = (r.get("intent") or "").strip()
            if not text or not intent or text in seen_texts:
                continue
            seen_texts.add(text)
            out.append({
                "text": text, "intent": intent,
                "split": (r.get("split") or "train").strip(),
                "source": f"extra:{Path(extra_path).name}",
            })
            extra_count += 1
        print(f"merged {extra_count} rows from {extra_path}")

    # 生成 FAQ.GENERAL（85/7.5/7.5 分配 split）
    rng = random.Random(7)
    faq_samples = [t for t in generate_faq_general() if t not in seen_texts]
    for text in faq_samples:
        roll = rng.random()
        split = "train" if roll < 0.85 else ("val" if roll < 0.925 else "test")
        out.append({"text": text, "intent": "FAQ.GENERAL", "split": split, "source": "generated_v42"})

    DST.parent.mkdir(parents=True, exist_ok=True)
    with DST.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "intent", "split", "source"])
        writer.writeheader()
        writer.writerows(out)

    labels = Counter(r["intent"] for r in out)
    splits = Counter(r["split"] for r in out)
    print(f"written {len(out)} rows -> {DST}")
    print(f"classes: {len(labels)}")
    for k, v in labels.most_common():
        print(f"  {k:32s} {v}")
    print("splits:", dict(splits))
    print("dropped:", dict(dropped))


if __name__ == "__main__":
    main()
