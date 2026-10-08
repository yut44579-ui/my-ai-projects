"""TASK-004 AC4：代码层策略闸门（D7）独立单测。

★ 每类敏感词至少一条用例 + 断言"不会去调 LLM"（本文件是纯函数单测，天然不碰网络；
  接口层的"未调用 LLM" 由 tests/test_ai_reply_api.py 用 monkeypatch 计数断言）。
★ 另断言「建议」≠「执行」：建议语气放行、承诺口吻转人工。
"""

from __future__ import annotations

import pytest

from app.services.policy_gate import (
    CATEGORY_LABELS,
    PolicyCategory,
    evaluate_policy,
)

# 每一类敏感词的用例（类别 → 至少一句会命中的话）
SENSITIVE_CASES: list[tuple[PolicyCategory, str]] = [
    (PolicyCategory.BANK_ACCOUNT, "麻烦把款项打到我们公司银行账户"),
    (PolicyCategory.BANK_ACCOUNT, "对公账号发我一下"),
    (PolicyCategory.LEGAL, "不解决的话我们要走法律途径起诉"),
    (PolicyCategory.LEGAL, "需要你们出具担保"),
    (PolicyCategory.COMPENSATION, "这次延期你们要赔偿我们的损失"),
    (PolicyCategory.COMPENSATION, "违约金怎么算"),
    (PolicyCategory.REFUND, "产品不好用，我要退款"),
    (PolicyCategory.REFUND, "能不能全额退"),
    (PolicyCategory.PAYMENT, "付款方式能不能改成分期"),
    (PolicyCategory.PAYMENT, "账期能不能给到 60 天"),
    (PolicyCategory.CONTRACT, "合同条款里这条要改一下"),
    (PolicyCategory.CONTRACT, "本周可以签约吗"),
    (PolicyCategory.ABNORMAL_FUNDS, "这笔大额转账走账不太正常"),
    (PolicyCategory.ABNORMAL_FUNDS, "先帮我垫资一下"),
    (PolicyCategory.LARGE_ORDER, "我们准备下 50 万元的大额订单"),
    (PolicyCategory.LARGE_ORDER, "这是批量采购，量很大"),
    (PolicyCategory.COMPLAINT, "再不处理我就要投诉了"),
    (PolicyCategory.COMPLAINT, "我要去举报你们"),
    (PolicyCategory.SPECIAL_RESOURCE, "能不能给我开绿灯优先排产"),
    (PolicyCategory.SPECIAL_RESOURCE, "内部名额能不能留一个"),
    (PolicyCategory.KEY_CUSTOMER_PROMISE, "我们是大客户，你们得保证优先供货"),
    (PolicyCategory.KEY_CUSTOMER_PROMISE, "这是关键承诺吗"),
    (PolicyCategory.QUOTE, "请给我一份正式报价"),
    (PolicyCategory.QUOTE, "这个产品多少钱"),
    (PolicyCategory.DISCOUNT, "老客户有没有折扣"),
    (PolicyCategory.DISCOUNT, "能不能打个八折"),
]


@pytest.mark.parametrize(("category", "text"), SENSITIVE_CASES)
def test_each_sensitive_category_is_blocked(category: PolicyCategory, text: str) -> None:
    """每一类敏感词都必须转人工（allowed=False），且不带"建议"例外。"""
    decision = evaluate_policy(text)
    assert decision.allowed is False, f"{text!r} 应当被拦下"
    assert decision.human_required is True
    assert decision.category is not None
    assert decision.label == CATEGORY_LABELS[decision.category]
    assert decision.matched, "必须记录命中的具体词，便于审计与界面显示"
    assert "人工" in decision.reason or "转人工" in decision.reason


def test_every_category_has_at_least_one_case() -> None:
    """AC4 的"每类敏感词至少一条用例"：清单与用例表必须一一覆盖。"""
    covered = {category for category, _ in SENSITIVE_CASES}
    assert covered == set(PolicyCategory), f"这些类别还没有用例：{set(PolicyCategory) - covered}"


# ─────────────── 「建议」≠「执行」（D7 原文口径） ───────────────


@pytest.mark.parametrize(
    "text",
    [
        "建议可考虑 9 折",
        "建议给 8 折，仅供参考",
        "这个价格建议可以先了解一下，仅供参考",
        "可以对比一下市面上的报价，不构成承诺",
    ],
)
def test_suggestion_tone_is_allowed(text: str) -> None:
    """建议语气（无对外承诺口吻）→ 放行。"""
    decision = evaluate_policy(text)
    assert decision.allowed is True, f"{text!r} 是建议语气，应当放行：{decision.reason}"
    assert decision.tone == "SUGGESTION"


@pytest.mark.parametrize(
    "text",
    [
        "已经给你 8 折了",
        "已经给你申请 8 折",
        "保证给你最低报价",
        "我承诺给你打七折",
        "就按 8 折给你",
        "申请到了，直接给你 9 折",
    ],
)
def test_commitment_tone_is_human_required(text: str) -> None:
    """对外承诺 / 执行口吻 → 必须人工（建议语气也救不回来）。"""
    decision = evaluate_policy(text)
    assert decision.allowed is False, f"{text!r} 是对外承诺，必须转人工"
    assert decision.tone in {"COMMITMENT", "EXECUTION"}


def test_commitment_wins_over_suggestion_marker() -> None:
    """"建议"+"已经给你"同时出现 → 承诺优先，照转人工。"""
    decision = evaluate_policy("建议给你 8 折，已经给你申请好了")
    assert decision.allowed is False


def test_hard_category_is_not_saved_by_suggestion_tone() -> None:
    """银行账户这类硬类别：带"建议"也不放行（只有报价/折扣有例外）。"""
    decision = evaluate_policy("建议你把钱打到这个银行账户")
    assert decision.allowed is False
    assert decision.category is PolicyCategory.BANK_ACCOUNT


# ─────────────── 不误伤正常问题 ───────────────


@pytest.mark.parametrize(
    "text",
    [
        "你们公司的售后支持时间是怎样的？",
        "产品说明书在哪里下载？",
        "下周的培训能安排吗？",
        "你好，请问有人在线吗？",
    ],
)
def test_normal_questions_pass_the_gate(text: str) -> None:
    """普通问题不许被误拦（否则 AI 回复就没得用了）。"""
    decision = evaluate_policy(text)
    assert decision.allowed is True, f"{text!r} 被误拦：{decision.reason}"
    assert decision.category is None
    assert decision.tone == "NONE"


def test_empty_text_is_allowed_but_flagged() -> None:
    """空文本不该走到闸门（接口层已校验），这里只保证不会抛异常。"""
    decision = evaluate_policy("")
    assert decision.allowed is True
    assert decision.reason
