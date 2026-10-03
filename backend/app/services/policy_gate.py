"""代码层策略闸门（D7：AI 安全拦截必须在代码层，Prompt 只负责表达）。

判定**写死在代码里**，绝不交给模型判断 —— 这是安全边界，不是提示词技巧。

敏感类别（与 D7 的第 1 行一一对应，改这里就是改安全策略）：
    报价 / 折扣 / 合同承诺 / 大额订单 / 付款 / 退款 / 银行账户 / 赔偿 /
    法律承诺 / 重大投诉 / 特殊资源承诺 / 高价值客户关键承诺 / 异常资金

★「建议」≠「执行」：
    「建议可考虑 9 折」这类**建议语气**（不含对外承诺口吻）→ 放行（SUGGESTION）；
    「已经给你 8 折」「给你申请到 8 折」这类**承诺/执行口吻** → 必须人工（HUMAN_REQUIRED）。
    只有"报价 / 折扣"这两类会被建议语气放过，其余类别一命中就转人工 —— 宁可多转人工。

命中即 HUMAN_REQUIRED，**不调用 LLM**（省一次调用，也避免模型在被问到敏感话题时自由发挥）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class PolicyCategory(str, Enum):
    """敏感类别（D7 冻结清单）。"""

    BANK_ACCOUNT = "BANK_ACCOUNT"  # 银行账户
    LEGAL = "LEGAL"  # 法律承诺
    COMPENSATION = "COMPENSATION"  # 赔偿
    REFUND = "REFUND"  # 退款
    PAYMENT = "PAYMENT"  # 付款
    CONTRACT = "CONTRACT"  # 合同承诺
    ABNORMAL_FUNDS = "ABNORMAL_FUNDS"  # 异常资金
    LARGE_ORDER = "LARGE_ORDER"  # 大额订单
    COMPLAINT = "COMPLAINT"  # 重大投诉
    SPECIAL_RESOURCE = "SPECIAL_RESOURCE"  # 特殊资源承诺
    KEY_CUSTOMER_PROMISE = "KEY_CUSTOMER_PROMISE"  # 高价值客户关键承诺
    QUOTE = "QUOTE"  # 报价
    DISCOUNT = "DISCOUNT"  # 折扣


CATEGORY_LABELS: dict[PolicyCategory, str] = {
    PolicyCategory.BANK_ACCOUNT: "银行账户",
    PolicyCategory.LEGAL: "法律承诺",
    PolicyCategory.COMPENSATION: "赔偿",
    PolicyCategory.REFUND: "退款",
    PolicyCategory.PAYMENT: "付款",
    PolicyCategory.CONTRACT: "合同承诺",
    PolicyCategory.ABNORMAL_FUNDS: "异常资金",
    PolicyCategory.LARGE_ORDER: "大额订单",
    PolicyCategory.COMPLAINT: "重大投诉",
    PolicyCategory.SPECIAL_RESOURCE: "特殊资源承诺",
    PolicyCategory.KEY_CUSTOMER_PROMISE: "高价值客户关键承诺",
    PolicyCategory.QUOTE: "报价",
    PolicyCategory.DISCOUNT: "折扣",
}

# ★ 判定顺序 = 命中优先级（前面的先赢）。紧急/不可逆的类别排前面，
#   "报价 / 折扣"放最后（它们有建议语气的例外分支）。
RULES: tuple[tuple[PolicyCategory, tuple[re.Pattern[str], ...]], ...] = (
    (
        PolicyCategory.BANK_ACCOUNT,
        tuple(
            re.compile(p)
            for p in (
                r"银行账[号户]",
                r"对公账[号户]",
                r"开户行",
                r"卡号",
                r"收款码",
                r"支付宝账[号户]",
                r"微信收款",
                r"打(钱|款)到.{0,6}(账[号户]|卡)",
            )
        ),
    ),
    (
        PolicyCategory.LEGAL,
        tuple(
            re.compile(p)
            for p in (
                r"法律责任",
                r"起诉",
                r"诉讼",
                r"律师函",
                r"担保",
                r"保证书",
                r"法律承诺",
            )
        ),
    ),
    (
        PolicyCategory.COMPENSATION,
        tuple(re.compile(p) for p in (r"赔偿", r"赔付", r"补偿金", r"违约金", r"损失补偿")),
    ),
    (
        PolicyCategory.REFUND,
        tuple(re.compile(p) for p in (r"退款", r"退货", r"退钱", r"退回款", r"全额退")),
    ),
    (
        PolicyCategory.PAYMENT,
        tuple(
            re.compile(p)
            for p in (r"付款", r"打款", r"首付", r"分期", r"账期", r"预付", r"尾款", r"回款")
        ),
    ),
    (
        PolicyCategory.CONTRACT,
        tuple(
            re.compile(p)
            for p in (r"合同", r"签约", r"签协议", r"承诺书", r"盖章", r"补充协议")
        ),
    ),
    (
        PolicyCategory.ABNORMAL_FUNDS,
        tuple(
            re.compile(p)
            for p in (r"异常资金", r"资金异常", r"走账", r"垫资", r"大额转账", r"洗钱", r"套现")
        ),
    ),
    (
        PolicyCategory.LARGE_ORDER,
        tuple(
            re.compile(p)
            for p in (
                r"大额",
                r"大单",
                r"几十万",
                r"上百万",
                r"\d+\s*万[元块]?",
                r"批量采购",
                r"大批量",
            )
        ),
    ),
    (
        PolicyCategory.COMPLAINT,
        tuple(
            re.compile(p)
            for p in (r"投诉", r"举报", r"曝光", r"3\.?15", r"工商", r"监管部门", r"消费者协会")
        ),
    ),
    (
        PolicyCategory.SPECIAL_RESOURCE,
        tuple(
            re.compile(p)
            for p in (r"特殊资源", r"优先排产", r"插队", r"开绿灯", r"特批", r"内部名额", r"预留资源")
        ),
    ),
    (
        PolicyCategory.KEY_CUSTOMER_PROMISE,
        tuple(
            re.compile(p)
            for p in (
                r"(高价值|大客户|战略客户|VIP|重点客户|核心客户)[^。；]{0,12}(承诺|保证|一定|包|优先|特批)",
                r"关键承诺",
            )
        ),
    ),
    (
        PolicyCategory.QUOTE,
        tuple(
            re.compile(p)
            for p in (
                r"报价",
                r"报个价",
                r"多少钱",
                r"价格",
                r"单价",
                r"售价",
                r"卖多少",
            )
        ),
    ),
    (
        PolicyCategory.DISCOUNT,
        tuple(
            re.compile(p)
            for p in (
                r"折扣",
                r"打折",
                r"几折",
                r"\d+\s*折",  # 8 折 / 8折
                r"[一二三四五六七八九十两]\s*折",  # 八折 / 七折
                r"优惠力度",
                r"特价",
                r"让利",
            )
        ),
    ),
)

# 「不构成承诺」这类**否定式**说法不是承诺口吻，判定前先把它们摘掉，
# 否则"可以对比一下报价，不构成承诺"会被自己的字面"承诺"二字误杀。
NEGATED_PROMISE_PHRASES: tuple[str, ...] = (
    "不构成承诺",
    "不做承诺",
    "不代表承诺",
    "并非承诺",
    "不是承诺",
    "不视为承诺",
)


def _strip_negated_promises(text: str) -> str:
    # ★ 替换文本里必须**不含任何承诺类关键词**，否则"（非承诺）"里的"承诺"又会被认成承诺
    stripped = text
    for phrase in NEGATED_PROMISE_PHRASES:
        stripped = stripped.replace(phrase, "（无此意）")
    return stripped

# 建议语气：出现这些词且**没有**承诺口吻时，「报价 / 折扣」放行（D7 的「建议」≠「执行」）
SUGGESTION_MARKERS: tuple[str, ...] = (
    "建议",
    "可以考虑",
    "仅供参考",
    "供参考",
    "不构成承诺",
    "建议评估",
    "可以了解",
    "可以对比",
)

# 对外承诺 / 执行口吻：出现这些词就按"已经拍板"处理，建议语气也救不回来
COMMITMENT_MARKERS: tuple[str, ...] = (
    "已经",
    "已给",
    "已申请",
    "已经给你",
    "保证",
    "承诺",
    "一定",
    "答应",
    "包您",
    "包你",
    "就按",
    "直接给",
    "给你",
    "给您",
    "申请到",
    "批下来",
    "定了",
    "确认可以",
    "马上办",
)

# 只有这两类可以被"建议语气"放过；其余类别命中即转人工
SUGGESTION_ESCAPABLE: frozenset[PolicyCategory] = frozenset(
    {PolicyCategory.QUOTE, PolicyCategory.DISCOUNT}
)


@dataclass(frozen=True)
class PolicyDecision:
    """闸门结论（可直接序列化进接口响应，供界面与审计查看）。"""

    allowed: bool
    reason: str
    category: PolicyCategory | None = None
    label: str | None = None
    matched: str | None = None
    tone: str = "NONE"  # COMMITMENT / SUGGESTION / NONE

    @property
    def human_required(self) -> bool:
        return not self.allowed


def evaluate_policy(text: str | None) -> PolicyDecision:
    """对一段文本做敏感判定。命中 → allowed=False（调用方必须转人工且不调 LLM）。"""
    normalized = (text or "").strip()
    if not normalized:
        # 空文本不该走到这（接口层已校验）；放行但留痕，绝不编造判定结果
        return PolicyDecision(allowed=True, reason="文本为空，无敏感内容", tone="NONE")

    has_suggestion = any(marker in normalized for marker in SUGGESTION_MARKERS)
    has_commitment = any(
        marker in _strip_negated_promises(normalized) for marker in COMMITMENT_MARKERS
    )

    for category, patterns in RULES:
        for pattern in patterns:
            hit = pattern.search(normalized)
            if hit is None:
                continue

            label = CATEGORY_LABELS[category]
            if category in SUGGESTION_ESCAPABLE and has_suggestion and not has_commitment:
                return PolicyDecision(
                    allowed=True,
                    category=category,
                    label=label,
                    matched=hit.group(0),
                    tone="SUGGESTION",
                    reason=(
                        f"命中「{label}」但属于建议语气（命中词：{hit.group(0)}），"
                        "未构成对外承诺，按 D7 放行"
                    ),
                )
            return PolicyDecision(
                allowed=False,
                category=category,
                label=label,
                matched=hit.group(0),
                tone="COMMITMENT" if has_commitment else "EXECUTION",
                reason=(
                    f"命中敏感类别「{label}」（命中词：{hit.group(0)}），"
                    "按 D7 必须人工处理，不调用 LLM"
                ),
            )

    return PolicyDecision(allowed=True, reason="未命中任何敏感类别", tone="NONE")


__all__ = [
    "CATEGORY_LABELS",
    "COMMITMENT_MARKERS",
    "PolicyCategory",
    "PolicyDecision",
    "RULES",
    "SUGGESTION_ESCAPABLE",
    "SUGGESTION_MARKERS",
    "evaluate_policy",
]
