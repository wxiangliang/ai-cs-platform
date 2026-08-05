#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

SEED = 20260805
rng = random.Random(SEED)

BASE = Path('/mnt/data')
V41 = BASE / 'intent_train_v41_clean_nodup.csv'
V42 = BASE / 'intent_train_v42_project.csv'
OUT_FULL = BASE / 'intent_train_v43_clean_nodup_mode_annotated.csv'
OUT_BUSINESS = BASE / 'intent_train_v43_project_business.csv'
OUT_MODE = BASE / 'conversation_mode_train_v1.csv'
OUT_AUDIT = BASE / 'conversation_mode_train_v1_audit.csv'
OUT_README = BASE / 'intent_mode_v43_README.md'
OUT_MANIFEST = BASE / 'intent_mode_v43_manifest.json'
OUT_ZIP = BASE / 'intent_mode_v43_package.zip'

SOCIAL_INTENTS_V41 = {'CHITCHAT.GREETING', 'CHITCHAT.THANKS', 'META.BOT_IDENTITY'}
SOCIAL_INTENTS_V42 = {'CHITCHAT.GENERAL', 'CHITCHAT.THANKS', 'META.BOT_IDENTITY'}
UNRESOLVED_INTENTS = {'META.UNKNOWN'}
CONTEXT_ONLY_INTENTS = {'META.SLOT_ONLY', 'META.CLARIFY_REPLY', 'META.CORRECTION'}
SAFETY_INTENTS = {'SAFETY.ABUSE'}
PROJECT_EXCLUDED_INTENTS = SOCIAL_INTENTS_V42 | UNRESOLVED_INTENTS
TARGET_PER_MODE = 3600


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[Dict[str, object]]) -> None:
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, '') for k in fields})


def norm_text(text: str) -> str:
    text = (text or '').strip().lower()
    text = re.sub(r'\s+', '', text)
    text = re.sub(r'[，。！？!?、；;：:,.~～…“”"\'‘’（）()【】\[\]<>《》_-]+', '', text)
    return text


def stable_split(key: str) -> str:
    n = int(hashlib.sha1(key.encode('utf-8')).hexdigest()[:8], 16) % 100
    if n < 80:
        return 'train'
    if n < 90:
        return 'val'
    return 'test'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------- 1) Full contract annotation ----------
fields41, rows41 = read_csv(V41)
annotated_rows: List[Dict[str, object]] = []
for row in rows41:
    intent = row.get('intent', '')
    sample_type = row.get('sample_type', '')
    classifier_ok = row.get('classifier_use', '').lower() == 'true' and row.get('trainable_for_classifier', '').lower() == 'true'

    if intent in SOCIAL_INTENTS_V41:
        mode = 'SOCIAL_ONLY'
        mode_trainable = classifier_ok
        mode_source = 'intent_mapping'
        mode_reason = 'social_or_identity_intent'
    elif intent in UNRESOLVED_INTENTS:
        mode = 'UNRESOLVED'
        mode_trainable = False
        mode_source = 'manual_exclusion'
        mode_reason = 'heterogeneous_unknown_not_a_mode_training_label'
    elif intent in CONTEXT_ONLY_INTENTS:
        mode = 'NOT_APPLICABLE'
        mode_trainable = False
        mode_source = 'pipeline_contract'
        mode_reason = 'handled_by_rule_or_context_control_before_mode_gate'
    elif intent in SAFETY_INTENTS:
        mode = 'NOT_APPLICABLE'
        mode_trainable = False
        mode_source = 'pipeline_contract'
        mode_reason = 'handled_by_guardrail_before_mode_gate'
    else:
        mode = 'TASK_ONLY'
        mode_trainable = classifier_ok and sample_type not in {'multi_intent', 'abuse_with_business_intent'}
        mode_source = 'intent_mapping'
        mode_reason = 'business_or_service_control_intent'

    business_use = (
        classifier_ok
        and intent not in SOCIAL_INTENTS_V41
        and intent not in UNRESOLVED_INTENTS
        and intent not in CONTEXT_ONLY_INTENTS
        and intent not in SAFETY_INTENTS
    )
    if business_use:
        exclusion = ''
    elif intent in SOCIAL_INTENTS_V41:
        exclusion = 'routed_by_conversation_mode_gate'
    elif intent in UNRESOLVED_INTENTS:
        exclusion = 'use_confidence_rejection_not_heterogeneous_unknown_class'
    elif intent in CONTEXT_ONLY_INTENTS:
        exclusion = 'handled_by_rule_context_layer'
    elif intent in SAFETY_INTENTS:
        exclusion = 'handled_by_guardrail'
    else:
        exclusion = 'existing_row_not_trainable_or_eval_only'

    new_row = dict(row)
    new_row.update({
        'conversation_mode_label': mode,
        'conversation_mode_trainable': str(bool(mode_trainable)),
        'conversation_mode_label_source': mode_source,
        'conversation_mode_reason': mode_reason,
        'business_intent_classifier_use': str(bool(business_use)),
        'business_intent_exclusion_reason': exclusion,
        'dataset_version': 'v43_mode_gate',
    })
    annotated_rows.append(new_row)

full_fields = fields41 + [
    'conversation_mode_label',
    'conversation_mode_trainable',
    'conversation_mode_label_source',
    'conversation_mode_reason',
    'business_intent_classifier_use',
    'business_intent_exclusion_reason',
    'dataset_version',
]
write_csv(OUT_FULL, full_fields, annotated_rows)


# ---------- 2) Business-only project dataset ----------
fields42, rows42 = read_csv(V42)
business_rows = [r for r in rows42 if r.get('intent', '') not in PROJECT_EXCLUDED_INTENTS]
write_csv(OUT_BUSINESS, fields42, business_rows)


# ---------- 3) Conversation mode dataset ----------
mode_fields = [
    'id', 'text', 'conversation_mode', 'split', 'source', 'source_intent',
    'base_id', 'generation_family', 'label_source', 'review_status', 'note'
]
mode_rows: List[Dict[str, object]] = []
seen_texts: Dict[str, str] = {}


def add_mode_row(row: Dict[str, object]) -> bool:
    text = str(row.get('text', '')).strip()
    key = norm_text(text)
    if not key or len(key) < 1:
        return False
    prior = seen_texts.get(key)
    if prior is not None:
        # Never allow the same normalized text under two labels, and deduplicate same-label rows.
        return False
    seen_texts[key] = str(row['conversation_mode'])
    mode_rows.append(row)
    return True


# TASK_ONLY: stratified 144 examples per business intent from v42 = 3600 total.
by_intent: Dict[str, List[Dict[str, str]]] = defaultdict(list)
for r in business_rows:
    by_intent[r['intent']].append(r)

for intent in sorted(by_intent):
    candidates = list(by_intent[intent])
    rng.shuffle(candidates)
    quota = TARGET_PER_MODE // len(by_intent)
    selected = candidates[:quota]
    for i, r in enumerate(selected, 1):
        add_mode_row({
            'id': f'mode_task_{len(mode_rows)+1:05d}',
            'text': r['text'],
            'conversation_mode': 'TASK_ONLY',
            'split': r.get('split', '') if r.get('split', '') in {'train','val','test'} else stable_split(r['text']),
            'source': 'intent_train_v42_project',
            'source_intent': intent,
            'base_id': '',
            'generation_family': 'existing_business_example',
            'label_source': 'intent_mapping',
            'review_status': 'weak_label_needs_sample_audit',
            'note': '业务意图样本；Mode Gate 只判断是否进入业务链路。',
        })

# In case integer quota or accidental duplicates leave a gap, fill from remaining business rows.
if sum(1 for r in mode_rows if r['conversation_mode']=='TASK_ONLY') < TARGET_PER_MODE:
    all_candidates = list(business_rows)
    rng.shuffle(all_candidates)
    for r in all_candidates:
        if sum(1 for x in mode_rows if x['conversation_mode']=='TASK_ONLY') >= TARGET_PER_MODE:
            break
        add_mode_row({
            'id': f'mode_task_{len(mode_rows)+1:05d}',
            'text': r['text'],
            'conversation_mode': 'TASK_ONLY',
            'split': r.get('split', '') if r.get('split', '') in {'train','val','test'} else stable_split(r['text']),
            'source': 'intent_train_v42_project',
            'source_intent': r['intent'],
            'base_id': '',
            'generation_family': 'existing_business_fill',
            'label_source': 'intent_mapping',
            'review_status': 'weak_label_needs_sample_audit',
            'note': '用于补足 TASK_ONLY 配额。',
        })

# SOCIAL_ONLY: keep all existing social and identity examples first.
for r in rows42:
    if r.get('intent') in SOCIAL_INTENTS_V42:
        add_mode_row({
            'id': f'mode_social_{len(mode_rows)+1:05d}',
            'text': r['text'],
            'conversation_mode': 'SOCIAL_ONLY',
            'split': r.get('split', '') if r.get('split', '') in {'train','val','test'} else stable_split(r['text']),
            'source': 'intent_train_v42_project',
            'source_intent': r['intent'],
            'base_id': '',
            'generation_family': 'existing_social_example',
            'label_source': 'intent_mapping',
            'review_status': 'weak_label_needs_sample_audit',
            'note': '高频闲聊/身份询问；命中后跳过业务意图二判。',
        })

social_stems = {
    'greeting': ['你好', '您好', '嗨', '哈喽', '早上好', '下午好', '晚上好', '有人吗', '在吗', '客服在不在', '很高兴见到你', '又见面了'],
    'thanks': ['谢谢你', '多谢啦', '辛苦了', '感谢你的帮助', '麻烦你了', '谢谢客服', '真的很感谢', '太感谢了', '刚才多亏你了', '谢谢你耐心解释'],
    'goodbye': ['先聊到这里', '我先走了', '回头再聊', '拜拜', '再见啦', '晚安', '下次再聊', '今天先这样', '我要去忙了', '改天再找你聊天'],
    'compliment': ['你回复得挺快', '你很有耐心', '你说话挺温柔', '你还挺聪明', '这个客服不错', '你服务态度很好', '跟你聊天挺舒服', '你解释得很清楚', '你反应真快', '你还蛮有趣的'],
    'mood': ['我今天心情不错', '今天有点累', '最近有点忙', '今天挺开心的', '我有点无聊', '刚下班好累', '今天心情一般', '最近睡得不太好', '今天特别放松', '这两天有点烦'],
    'weather': ['今天真热', '外面下雨了', '今天风好大', '天气不错', '最近有点冷', '台北今天好闷', '这两天天气怪怪的', '今天阳光很好', '外面突然降温了', '刚才下了一场大雨'],
    'casual': ['你吃饭了吗', '你会休息吗', '你每天都在线吗', '你平时忙不忙', '你会不会觉得无聊', '你喜欢聊天吗', '你会记得我们聊过什么吗', '你能听懂方言吗', '你会讲笑话吗', '你今天心情怎么样'],
    'laughter': ['哈哈', '哈哈哈', '笑死我了', '嘿嘿', '有点意思', '太逗了', '哈哈你真逗', '好好笑', '这回答挺有趣', '我被你逗笑了'],
    'daily': ['我刚喝完咖啡', '今天终于放假了', '我正在等公交', '刚刚差点迟到', '我准备去吃饭', '我今天起得很早', '周末过得真快', '我刚运动回来', '今天工作终于结束了', '我正在回家的路上'],
}

social_frames = [
    ('plain', '{base}'),
    ('customer_service', '客服，{base}'),
    ('by_the_way', '对了，{base}'),
    ('casual_intro', '没别的事，{base}'),
    ('speaking_of', '说起来，{base}'),
    ('small_talk', '顺便聊一句，{base}'),
]
social_endings = {
    'greeting': ['。', '呀。', '～', '！', '，有人回应吗？', '，很高兴见到你。'],
    'thanks': ['。', '呀。', '！', '，真的谢谢。', '😊', '，你辛苦了。'],
    'goodbye': ['。', '啦。', '，拜拜。', '～', '，祝你顺利。', '，下次见。'],
    'compliment': ['。', '呀。', '！', '😊', '，值得表扬。', '，继续保持。'],
    'mood': ['。', '呀。', '呢。', '，随便聊聊。', '～', '，想找个人说说话。'],
    'weather': ['。', '呀。', '呢。', '～', '，挺适合聊天的。', '，你那边呢？'],
    'casual': ['？', '呀？', '呢？', '，我有点好奇。', '😊', '，随便问问。'],
    'laughter': ['。', '😂', '，有点意思。', '，真逗。', '哈哈。', '～'],
    'daily': ['。', '呀。', '呢。', '，跟你随便聊聊。', '～', '，今天还挺充实。'],
}

social_candidates: List[Tuple[str,str,str]] = []
for family, stems in social_stems.items():
    for s in stems:
        group = f'{family}:{s}'
        for frame_name, frame in social_frames:
            for ending in social_endings[family]:
                text = frame.format(base=s) + ending
                social_candidates.append((text, family, group))
rng.shuffle(social_candidates)
for text, family, group in social_candidates:
    if sum(1 for r in mode_rows if r['conversation_mode']=='SOCIAL_ONLY') >= TARGET_PER_MODE:
        break
    add_mode_row({
        'id': f'mode_social_{len(mode_rows)+1:05d}',
        'text': text,
        'conversation_mode': 'SOCIAL_ONLY',
        'split': stable_split(group),
        'source': 'generated_mode_v1',
        'source_intent': '',
        'base_id': '',
        'generation_family': f'social_{family}',
        'label_source': 'synthetic_rule',
        'review_status': 'synthetic_needs_human_spotcheck',
        'note': '纯社交表达，不含业务动作、槽位或商品事实请求。',
    })

# MIXED: one substantive social clause + one business clause. Keep source task split.
task_rows = [r for r in mode_rows if r['conversation_mode']=='TASK_ONLY']
social_clauses = [
    ('weather', '今天天气不错'), ('weather', '今天真的好热'), ('mood', '我今天有点累'),
    ('mood', '最近工作挺忙的'), ('compliment', '你回复得挺快'), ('compliment', '你还挺有耐心'),
    ('thanks', '谢谢你刚才的帮助'), ('thanks', '辛苦你了'), ('laughter', '哈哈你挺有意思'),
    ('daily', '我刚喝完咖啡'), ('daily', '我刚下班'), ('greeting', '下午好'),
]
mixed_patterns = [
    ('social_first_duile', '{social}，对了，{task}'),
    ('social_first_shunbian', '{social}，顺便{task}'),
    ('social_first_buguo', '{social}，不过我还想问一下：{task}'),
    ('social_first_then_task', '{social}。还有一件事，{task}'),
    ('task_first_thanks', '{task}，处理完先谢谢你啦'),
    ('task_first_chat', '{task}。话说回来，{social}'),
    ('greeting_task', '{social}，麻烦帮我处理一下：{task}'),
    ('light_interruption', '{social}哈哈，另外{task}'),
]
# Build deterministic candidates; each business base is used at most once in mode dataset.
for idx, task in enumerate(task_rows):
    if sum(1 for r in mode_rows if r['conversation_mode']=='MIXED') >= TARGET_PER_MODE:
        break
    fam, social = social_clauses[idx % len(social_clauses)]
    pat_name, pattern = mixed_patterns[(idx // len(social_clauses)) % len(mixed_patterns)]
    text = pattern.format(social=social, task=task['text'])
    add_mode_row({
        'id': f'mode_mixed_{len(mode_rows)+1:05d}',
        'text': text,
        'conversation_mode': 'MIXED',
        'split': task['split'],
        'source': 'generated_mode_v1',
        'source_intent': task['source_intent'],
        'base_id': task['id'],
        'generation_family': f'mixed_{pat_name}_{fam}',
        'label_source': 'synthetic_composition',
        'review_status': 'synthetic_needs_human_spotcheck',
        'note': '包含可独立回应的闲聊子句和明确业务子句；业务段必须继续进入意图流水线。',
    })

# OOS: explicit non-customer-service tasks. Build topic-template families.
oos_topics = {
    'coding': [
        ('帮我写一个{obj}程序', ['Python爬虫','Java排序','前端倒计时','SQL报表','Go并发下载器','C++小游戏']),
        ('这段{obj}代码为什么报错', ['Python','JavaScript','Java','SQL','Rust','Vue']),
        ('教我怎么用{obj}', ['Docker','Kubernetes','Git','Linux命令','正则表达式','Excel宏']),
    ],
    'study': [
        ('帮我解一道{obj}题', ['高等数学','概率论','物理','化学','线性代数','几何']),
        ('替我写一篇关于{obj}的作业', ['人工智能','环境保护','中国历史','市场营销','心理学','文学']),
        ('给我讲讲{obj}知识点', ['牛顿定律','微积分','英语语法','世界历史','统计学','电路原理']),
    ],
    'finance': [
        ('帮我分析一下{obj}', ['台积电股票','比特币走势','黄金价格','美股大盘','基金配置','汇率变化']),
        ('现在适合买{obj}吗', ['股票','比特币','黄金','基金','债券','外汇']),
        ('帮我做一个{obj}投资计划', ['十万元','退休金','每月定投','短线交易','家庭资产','教育基金']),
    ],
    'medical': [
        ('我{obj}应该吃什么药', ['头痛','胃痛','发烧','失眠','咳嗽','过敏']),
        ('帮我判断{obj}是不是生病了', ['一直心跳快','皮肤发红','腰很痛','眼睛干涩','晚上睡不着','喉咙不舒服']),
        ('给我一个{obj}治疗方案', ['感冒','失眠','颈椎痛','皮肤过敏','胃炎','偏头痛']),
    ],
    'legal': [
        ('帮我写一份{obj}', ['租房合同','离婚协议','劳动仲裁申请','借款协议','律师函','诉状']),
        ('这个{obj}问题违法吗', ['公司扣工资','邻居噪音','网络转载','合同违约','偷拍视频','拖欠房租']),
        ('帮我分析{obj}法律责任', ['交通事故','版权纠纷','劳动争议','房屋买卖','网络侵权','借贷纠纷']),
    ],
    'travel': [
        ('帮我安排一个{obj}旅行计划', ['日本五日','台南两日','欧洲十日','花莲周末','韩国自由行','美国西海岸']),
        ('推荐几个{obj}景点', ['台北','东京','巴黎','高雄','京都','纽约']),
        ('去{obj}要准备什么', ['日本','泰国','冰岛','新加坡','加拿大','澳大利亚']),
    ],
    'food': [
        ('推荐一家{obj}餐厅', ['台北火锅','台中烧肉','高雄海鲜','附近素食','生日聚餐','深夜宵夜']),
        ('教我做{obj}', ['红烧肉','蛋糕','牛肉面','珍珠奶茶','披萨','咖喱饭']),
        ('帮我设计一份{obj}菜单', ['减脂餐','家庭聚会','儿童午餐','素食晚餐','露营料理','早餐']),
    ],
    'writing': [
        ('帮我写一篇{obj}', ['科幻小说','求职自传','毕业演讲','生日祝福','道歉信','爱情故事']),
        ('替我润色这份{obj}', ['论文','履历','广告文案','演讲稿','小说','社交媒体贴文']),
        ('给我想几个{obj}', ['品牌名字','游戏角色名','论文标题','短视频脚本','婚礼誓词','产品口号']),
    ],
    'translation': [
        ('把这段话翻译成{obj}', ['英文','日文','韩文','法文','德文','西班牙文']),
        ('帮我检查{obj}语法', ['英文邮件','日文句子','法文作文','韩文简介','德文简历','西班牙文对话']),
        ('教我一句{obj}怎么说', ['日语谢谢','英语道歉','韩语你好','法语再见','德语早安','西班牙语多少钱']),
    ],
    'career': [
        ('帮我准备{obj}面试', ['产品经理','后端工程师','会计','销售','教师','数据分析师']),
        ('帮我修改{obj}履历', ['软件工程师','运营','设计师','应届生','转职者','管理岗']),
        ('我该不该{obj}', ['转行','创业','读研究所','去海外工作','做自由职业','找一份兼职']),
    ],
    'relationship': [
        ('我和{obj}吵架了怎么办', ['男朋友','女朋友','同事','家人','朋友','室友']),
        ('帮我分析{obj}为什么不回我', ['朋友','对象','同事','客户','同学','前任']),
        ('怎么跟{obj}沟通比较好', ['主管','伴侣','父母','孩子','室友','朋友']),
    ],
    'news': [
        ('告诉我今天的{obj}新闻', ['科技','国际','财经','体育','娱乐','AI']),
        ('最近{obj}发生了什么', ['美国大选','人工智能行业','国际局势','芯片产业','电影市场','足球联赛']),
        ('帮我总结一下{obj}', ['今日新闻','本周科技动态','最近财经事件','国际大事','体育赛况','娱乐热点']),
    ],
    'media': [
        ('帮我生成一张{obj}图片', ['猫咪海报','城市夜景','动漫头像','产品宣传图','婚礼邀请卡','科幻飞船']),
        ('推荐一部{obj}电影', ['科幻','爱情','悬疑','喜剧','动画','纪录片']),
        ('帮我剪一个{obj}视频', ['旅行','生日','产品介绍','毕业纪念','婚礼','短视频']),
    ],
    'general': [
        ('告诉我{obj}是什么', ['量子计算','黑洞','区块链','相对论','文艺复兴','机器学习']),
        ('帮我查一下{obj}', ['明天天气','现在几点','世界杯赛程','美元汇率','最近展览','附近停车场']),
        ('为什么{obj}', ['天空是蓝色的','猫会呼噜','海水是咸的','人会做梦','树叶会变黄','飞机能飞']),
    ],
}
oos_frames = [
    ('plain', '{base}'),
    ('question', '请问，{base}'),
    ('unrelated', '我有个和订单无关的问题：{base}'),
    ('pause_shop', '先不聊商品了，{base}'),
    ('additional', '另外想问一下，{base}'),
    ('pause_service', '客服业务先放一边，{base}'),
]
oos_endings = ['', '，谢谢。', '，请尽量详细说明。']
oos_candidates: List[Tuple[str,str,str]] = []
for topic, templates in oos_topics.items():
    for ti, (template, objects) in enumerate(templates):
        family_key = f'{topic}:{ti}:{template}'
        for obj in objects:
            base_text = template.format(obj=obj)
            for frame_name, frame in oos_frames:
                for ending in oos_endings:
                    text = frame.format(base=base_text) + ending
                    oos_candidates.append((text, topic, family_key))
rng.shuffle(oos_candidates)
for text, topic, family_key in oos_candidates:
    if sum(1 for r in mode_rows if r['conversation_mode']=='OOS') >= TARGET_PER_MODE:
        break
    add_mode_row({
        'id': f'mode_oos_{len(mode_rows)+1:05d}',
        'text': text,
        'conversation_mode': 'OOS',
        'split': stable_split(family_key),
        'source': 'generated_mode_v1',
        'source_intent': '',
        'base_id': '',
        'generation_family': f'oos_{topic}',
        'label_source': 'synthetic_rule',
        'review_status': 'synthetic_needs_human_spotcheck',
        'note': '有明确任务，但不属于本客服业务能力范围；不得归为闲聊。',
    })

# Final deterministic ordering by mode and split, then stable text hash.
mode_order = {'TASK_ONLY':0, 'SOCIAL_ONLY':1, 'MIXED':2, 'OOS':3}
split_order = {'train':0, 'val':1, 'test':2}
mode_rows.sort(key=lambda r: (mode_order[str(r['conversation_mode'])], split_order.get(str(r['split']),9), hashlib.sha1(str(r['text']).encode('utf-8')).hexdigest()))
for i, r in enumerate(mode_rows, 1):
    r['id'] = f'mode_v1_{i:05d}'
write_csv(OUT_MODE, mode_fields, mode_rows)


# ---------- 4) Audit ----------
audit_counter = Counter((str(r['conversation_mode']), str(r['split']), str(r['source'])) for r in mode_rows)
audit_rows = []
for (mode, split, source), count in sorted(audit_counter.items()):
    audit_rows.append({'conversation_mode':mode, 'split':split, 'source':source, 'count':count})
write_csv(OUT_AUDIT, ['conversation_mode','split','source','count'], audit_rows)

# Validation
mode_counts = Counter(str(r['conversation_mode']) for r in mode_rows)
split_counts = Counter(str(r['split']) for r in mode_rows)
label_split_counts = Counter((str(r['conversation_mode']), str(r['split'])) for r in mode_rows)
assert mode_counts == Counter({'TASK_ONLY':TARGET_PER_MODE, 'SOCIAL_ONLY':TARGET_PER_MODE, 'MIXED':TARGET_PER_MODE, 'OOS':TARGET_PER_MODE}), mode_counts
assert len(mode_rows) == len({norm_text(str(r['text'])) for r in mode_rows})
assert all(r['split'] in {'train','val','test'} for r in mode_rows)
assert not any(r['intent'] in PROJECT_EXCLUDED_INTENTS for r in business_rows)

# ---------- 5) README + manifest ----------
manifest = {
    'generated_at': '2026-08-05',
    'seed': SEED,
    'inputs': {
        V41.name: {'rows': len(rows41), 'sha256': sha256(V41)},
        V42.name: {'rows': len(rows42), 'sha256': sha256(V42)},
    },
    'outputs': {
        OUT_FULL.name: {'rows': len(annotated_rows), 'sha256': sha256(OUT_FULL)},
        OUT_BUSINESS.name: {'rows': len(business_rows), 'classes': len(set(r['intent'] for r in business_rows)), 'sha256': sha256(OUT_BUSINESS)},
        OUT_MODE.name: {'rows': len(mode_rows), 'mode_counts': dict(mode_counts), 'split_counts': dict(split_counts), 'sha256': sha256(OUT_MODE)},
        OUT_AUDIT.name: {'rows': len(audit_rows), 'sha256': sha256(OUT_AUDIT)},
    },
    'project_excluded_intents': sorted(PROJECT_EXCLUDED_INTENTS),
    'mode_label_contract': {
        'SOCIAL_ONLY': '纯闲聊/身份社交，跳过业务 Intent LLM 二判',
        'TASK_ONLY': '纯业务或服务控制请求，进入现有意图流水线',
        'MIXED': '闲聊子句 + 业务子句，需分段且业务优先',
        'OOS': '明确任务但超出客服业务范围',
        'UNCERTAIN': '推理拒识状态，不作为训练标签',
    },
    'label_split_counts': {f'{m}/{s}': c for (m,s),c in sorted(label_split_counts.items())},
    'validation': {
        'normalized_text_duplicates': 0,
        'cross_label_text_collisions': 0,
        'business_project_contains_excluded_intents': False,
    },
}
OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

readme = f'''# Intent / Conversation Mode 数据集 v43

生成日期：2026-08-05  
随机种子：`{SEED}`

## 为什么要重新生成

引入 `Conversation Mode Gate` 后，闲聊不再只是业务 Intent 分类器中的普通类别：

```text
规则/护栏 → Mode Gate(SOCIAL_ONLY/TASK_ONLY/MIXED/OOS)
          → 只有 TASK_ONLY 和 MIXED 的业务段进入 Intent + KNN + Meta + LLM 二判
```

因此原来的两份数据需要做兼容升级，但不能直接覆盖原文件：

1. 完整契约文件保留全部历史行并新增 Mode 标注，便于回放和审计；
2. 项目投喂文件移除闲聊、身份询问和异质 `META.UNKNOWN`；
3. 新增独立四分类 Mode Gate 数据集。

## 文件说明

### `{OUT_FULL.name}`

- 行数：{len(annotated_rows)}（与 v4.1 完全一致，不删除历史样本）
- 新增字段：
  - `conversation_mode_label`
  - `conversation_mode_trainable`
  - `conversation_mode_label_source`
  - `conversation_mode_reason`
  - `business_intent_classifier_use`
  - `business_intent_exclusion_reason`
  - `dataset_version`
- 用途：单一事实来源、审计、后续人工修订。

### `{OUT_BUSINESS.name}`

- 行数：{len(business_rows)}
- Intent 类别数：{len(set(r['intent'] for r in business_rows))}
- 字段仍保持 `text,intent,split,source`，兼容现有项目导入。
- 移除：{', '.join(sorted(PROJECT_EXCLUDED_INTENTS))}
- `META.UNKNOWN` 不再作为一个混合文本类别训练；未知由阈值/margin/KNN/LLM 失败后产生。

### `{OUT_MODE.name}`

- 总行数：{len(mode_rows)}
- 四类各 {TARGET_PER_MODE} 行：`SOCIAL_ONLY` / `TASK_ONLY` / `MIXED` / `OOS`
- `UNCERTAIN` 是推理时拒识结果，不是训练标签。
- 所有文本做归一化去重；不存在同一句跨标签冲突。
- `MIXED` 与 `OOS` 主要为合成冷启动数据，字段 `review_status` 已明确标记，不能把离线高分视为上线依据。

## 推荐训练方式

- Mode Gate：四分类 LR 作为基线，LightGBM/SetFit Head 作对照；不要直接用 Accuracy 决策上线。
- 首要指标：
  - `SOCIAL_ONLY precision`（业务误吞为闲聊必须极低）
  - `MIXED recall`（不能漏掉业务子句）
  - `OOS precision/recall`
  - `intent_llm_skipped_by_mode`
- 初始上线只接管高置信 `SOCIAL_ONLY`；`MIXED/OOS` 先影子观察。
- 活动任务中的 `SOCIAL_ONLY` 应由策略层输出 `SOCIAL_HOLD`，保持任务并重新提示 pending slot；它不是 Mode 训练标签。

## 标签注意事项

- `META.UNKNOWN` 原样保留在完整契约文件中，但 `conversation_mode_trainable=False`。
- 安全辱骂样本由 guardrail 先处理，不进入 Mode Gate 训练。
- `META.SLOT_ONLY / META.CLARIFY_REPLY / META.CORRECTION` 属于上下文控制样本，也不进入 Mode Gate。
- 合成数据只用于固化管线与特征契约，需通过影子日志和人工审核逐步替换。

## 可复现

运行：

```bash
python generate_intent_mode_v43.py
```

详细计数和 SHA-256 见 `{OUT_MANIFEST.name}`，分布见 `{OUT_AUDIT.name}`。
'''
OUT_README.write_text(readme, encoding='utf-8')

with zipfile.ZipFile(OUT_ZIP, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
    for p in [OUT_FULL, OUT_BUSINESS, OUT_MODE, OUT_AUDIT, OUT_README, OUT_MANIFEST, Path(__file__)]:
        zf.write(p, arcname=p.name)

print(json.dumps(manifest, ensure_ascii=False, indent=2))
print('ZIP', OUT_ZIP, OUT_ZIP.stat().st_size)
