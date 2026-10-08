"""AI 助手能力清单（TASK-013）：**单一来源**。

═══════════════════════════════════════════════════════════════════════
【为什么单独抽一个模块】
═══════════════════════════════════════════════════════════════════════
同一份"我能做什么"原先写在两个地方：
    · api/routes/dashboard.py 的 _ai_assistant()  → 右栏静态面板
    · api/routes/dashboard_ai.py 的 CAPABILITY_TEXT → 问答时的回答
两处手写必然漂移。实测就漂了：风险中心与客户访问上线后，
问答那边更新了，右栏面板还是旧文案（CDP 断言直接抓到）。

现在两个位置都从这里取，改一次全都对。

═══════════════════════════════════════════════════════════════════════
【写作纪律：只写**已经能用**的能力】
═══════════════════════════════════════════════════════════════════════
每一条都对应一个真实存在的接口或页面。**没有实现的不许写进来** ——
面板上写"能分析市场趋势"而实际答不了，就是虚假承诺。
新增能力时：改本文件 + 加对应断言，面板与问答同时生效。
"""

from __future__ import annotations

from app.schemas.dashboard import AiCapability

#: 能力清单（key 稳定，label 是可读说明）。
#: ★ 顺序即展示顺序，把"今天要做什么"放最前（首页第一要务）。
CAPABILITIES: tuple[AiCapability, ...] = (
    AiCapability(
        key="pending",
        label="列出今天需要人工处理的客户（需人工/AI失败/待裁决/新客户 四类分开）",
    ),
    AiCapability(
        key="customer",
        label="查某个客户的档案与状态（说姓名或电话即可，支持「他什么情况」追问）",
    ),
    AiCapability(
        key="risk",
        label="看待处理的风险（只来自命中敏感策略、AI 回复失败两条依据）",
    ),
    AiCapability(
        key="visit",
        label="看客户访问情况（今天访问几次、最常看哪些页面）",
    ),
    AiCapability(
        key="activity",
        label="看最近客户动态（状态变更 / 沟通 / 接管 / 访问，全部是系统内真实事件）",
    ),
    AiCapability(
        key="reply",
        label="在客户详情页起草回复（命中敏感策略会转人工，不给承诺）",
    ),
    AiCapability(
        key="trace",
        label="每个数字都能点开看证据来源（EvidenceValue 可核对）",
    ),
)

#: 明确答不了的范围（面板与问答共用同一份表述）
UNAVAILABLE: tuple[str, ...] = (
    "竞争对手与市场份额",
    "行业规模、增长率等外部趋势",
    "库外的任何事实（新闻、股价、客户私下情况）",
    "替你直接写发给客户的话术（要在客户详情页人工确认）",
)

#: 硬拦（策略闸门在模型之前拦下，不给任何承诺）
BLOCKED: str = "报价 / 折扣 / 合同条款 / 收款账户 / 退款 / 赔偿 —— 按规则一律转人工"


def capability_text() -> str:
    """问答场景用的口语化能力说明（由上面同一份清单生成，不手写第二遍）。"""
    items = "、".join(c.label.split("（")[0] for c in CAPABILITIES)
    return (
        f"我能查这些：{items}。\n"
        f"答不了的：{'、'.join(UNAVAILABLE)}。\n"
        f"另外 {BLOCKED}。"
    )


__all__ = ["BLOCKED", "CAPABILITIES", "UNAVAILABLE", "capability_text"]
