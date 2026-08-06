"""槽位抽取器（SlotExtractor）。

第一版支持的槽位：order_id、phone、product_name、color、quantity。
全部基于规则与正则实现，不调用 LLM、不依赖向量库。
抽取结果只包含命中的槽位（未命中的键不返回）。
"""

from typing import Any

from app.chat.slots import patterns

# 商品名误抽过滤词：这些词其实是疑问 / 意图词，不应作为商品名
_PRODUCT_NAME_STOPWORDS = frozenset(
    {"多少钱", "价格", "库存", "怎么样", "参数", "规格", "介绍", "详情", "有货", "退款", "退货", "换货"}
)


class SlotExtractor:
    """基于正则的槽位抽取器。"""

    def extract(self, text: str) -> dict[str, Any]:
        """从文本中抽取槽位。

        :param text: 归一化后的用户文本
        :return: 仅包含命中的槽位的 dict
        """
        slots: dict[str, Any] = {}
        if not text:
            return slots

        order_id = self._extract_order_id(text)
        if order_id:
            slots["order_id"] = order_id

        phone = self._first_group(patterns.PHONE_RE, text)
        if phone:
            slots["phone"] = phone

        quantity = self._extract_quantity(text)
        if quantity is not None:
            slots["quantity"] = quantity

        color = self._first_group(patterns.COLOR_RE, text)
        if color:
            slots["color"] = color

        product_name = self._extract_product_name(text)
        if product_name:
            slots["product_name"] = product_name

        # —— Stage 32 选品顾问槽位 ——
        budget = self._extract_budget(text)
        if budget is not None:
            slots["budget"] = budget

        category = self._first_group(patterns.CATEGORY_RE, text)
        if category:
            slots["category"] = category

        compare_items = self._extract_compare_items(text)
        if compare_items:
            slots["compare_items"] = compare_items

        # —— Stage 39 预约槽位 ——
        service_type = self._first_group(patterns.SERVICE_TYPE_RE, text)
        if service_type:
            slots["service_type"] = service_type
        appointment_time = self._first_group(patterns.APPOINTMENT_TIME_RE, text)
        if appointment_time:
            slots["appointment_time"] = appointment_time.strip()
        appointment_no = self._first_group(patterns.APPOINTMENT_NO_RE, text)
        if appointment_no:
            slots["appointment_no"] = appointment_no

        return slots

    def _extract_product_name(self, text: str) -> str | None:
        """抽取商品名：先试引导词模式，再试「商品名+意图词」前置模式。

        前置模式命中后剥离前缀礼貌语/代词与尾部语气字，
        剩余不足 2 字符或落在停用词表则放弃（避免把疑问词当商品名）。
        """
        candidate = self._first_group(patterns.PRODUCT_NAME_RE, text)
        if candidate and candidate not in _PRODUCT_NAME_STOPWORDS:
            return candidate

        m = patterns.PRODUCT_BEFORE_INTENT_RE.search(text)
        if not m:
            return None
        candidate = patterns.PRODUCT_PREFIX_NOISE_RE.sub("", m.group(1))
        candidate = candidate.strip(patterns.PRODUCT_SUFFIX_NOISE)
        if len(candidate) < 2 or candidate in _PRODUCT_NAME_STOPWORDS:
            return None
        return candidate

    @staticmethod
    def _first_group(regex: Any, text: str) -> str | None:
        """返回正则第一个捕获组，未命中返回 None。"""
        m = regex.search(text)
        return m.group(1) if m else None

    def _extract_order_id(self, text: str) -> str | None:
        """抽取订单号：优先带前缀的写法，其次独立数字串。

        无前缀匹配前先把手机号从文本中剔除：11 位手机号同样满足
        「8~24 位数字串」形态，不剔除会被误抽成订单号
        （用户补槽时发手机号 → 任务带着错误订单号进确认门，v2 修复）。
        """
        # 优先匹配“订单号: xxx”这类显式写法，准确率更高
        prefixed = patterns.ORDER_ID_WITH_PREFIX_RE.search(text)
        if prefixed:
            return prefixed.group(1)
        # 剔除手机号后，再匹配独立的长数字串
        text_without_phone = patterns.PHONE_RE.sub(" ", text)
        bare = patterns.ORDER_ID_BARE_RE.search(text_without_phone)
        return bare.group(1) if bare else None

    def _extract_budget(self, text: str) -> int | None:
        """抽取预算（归一化为元的整数）：「预算3000」「2千以内」「不超过3000元」。

        手机号/订单号先剔除，防止长数字串被误当预算。
        """
        cleaned = patterns.PHONE_RE.sub(" ", text)
        cleaned = patterns.ORDER_ID_WITH_PREFIX_RE.sub(" ", cleaned)
        m = (
            patterns.BUDGET_WITH_PREFIX_RE.search(cleaned)
            or patterns.BUDGET_LIMIT_RE.search(cleaned)
            or patterns.BUDGET_WITH_SUFFIX_RE.search(cleaned)
        )
        if not m:
            return None
        try:
            value = float(m.group(1)) * patterns._BUDGET_UNITS.get(m.group(2) or "", 1)
        except ValueError:
            return None
        # 预算合理域校验（1 元~100 万）：排除年份/单号残段误抽
        return int(value) if 1 <= value <= 1_000_000 else None

    def _extract_compare_items(self, text: str) -> str | None:
        """抽取对比两项，剥噪声后规范为「A|B」；任一侧过短放弃。"""
        m = patterns.COMPARE_ITEMS_RE.search(text)
        if not m:
            return None
        left = patterns.COMPARE_PREFIX_NOISE_RE.sub("", m.group(1).strip()).strip()
        right = patterns.COMPARE_SUFFIX_NOISE_RE.sub("", m.group(2).strip()).strip()
        right = right.strip(patterns.PRODUCT_SUFFIX_NOISE)
        if len(left) < 2 or len(right) < 2:
            return None
        return f"{left}|{right}"

    def _extract_quantity(self, text: str) -> int | None:
        """抽取数量：优先“数字+量词”，其次“买/数量+数字”。"""
        m = patterns.QUANTITY_RE.search(text) or patterns.QUANTITY_WITH_PREFIX_RE.search(text)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None


# 模块级单例
slot_extractor = SlotExtractor()
