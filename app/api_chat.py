"""api_chat.py · 自然语言问答的 HTTP 端点（TASK-004）。

════════════════════════════════════════════════════════════════════════
【端点清单（全部是新增路径，与既有 12 个端点零交集）】
════════════════════════════════════════════════════════════════════════
    GET  /api/chat/capabilities     本版支持什么、数据边界在哪、LLM 接没接（**只说有没有，不说 key 是什么**）
    POST /api/chat                  提问 → 一整条链路（问题/Intent/工具/事实/回答）
    GET  /api/conversations         历史提问列表（精简摘要，分页）
    GET  /api/conversations/{id}    单次问答的完整记录（刷新页面后靠它恢复）

════════════════════════════════════════════════════════════════════════
【为什么又是独立文件 + 只往 api.py 加两行】
════════════════════════════════════════════════════════════════════════
同 TASK-003 的理由：api.py 里那 12 个端点（含 /api/health）是**冻结的 Legacy Contract**，
响应形状一个字都不许动。新能力走新文件、新路径，api.py 只多一行 import + 一行 include_router
（位置必须在 `mount("/")` 之前）。错误体形状照旧由 api.py 的统一处理器产出 ——
本模块的 `ChatApiError` 只带 `code`/`message` 两个属性，靠鸭子类型被那个处理器接住。

════════════════════════════════════════════════════════════════════════
【status 与 HTTP 状态码的分工（容易搞混，写清楚）】
════════════════════════════════════════════════════════════════════════
    4xx  = **请求本身有问题**（问题为空、body 多了字段）→ 走统一错误体，不进会话历史
    200  = 请求没问题。响应里的 `status` 再说这次问答的结果：
           ok / degraded / unsupported / error（四种都会落盘，因为都是"有效的一问一答"）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app import state
from app.ai import intent as intent_module, llm, service, tools

router = APIRouter(prefix="/api", tags=["chat"])

# 列表里每条记录的精简字段（完整记录可能有几十 KB：事实表 + 逐日序列 + 三段回答）
_SUMMARY_FIELDS = (
    "conversation_id",
    "question",
    "status",
    "created_at",
    "notice",
)


class ChatApiError(HTTPException):
    """带机器可读 `code` 的 HTTPException（与 api.py 的 ApiError 同形，但不 import 它）。

    与 api_documents.DocumentApiError 是同一个套路：只要带 `code`/`message`，
    api.py 的 `_http_error_handler` 就会产出**同一个**统一错误体。
    """

    def __init__(self, status_code: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(status_code=status_code, detail=message if detail is None else detail)
        self.code = code
        self.message = message


class ChatRequest(BaseModel):
    """提问的请求体。

    `extra="forbid"`：多传字段直接 422，**不静默忽略** —— 前端拼错参数名时要立刻暴露，
    而不是"看起来成功了但参数被丢掉"（那种 bug 最难查）。
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=500, description="用户原话")
    use_llm: bool = Field(default=True, description="false = 强制走关键词降级路径（用于验证 AC-05）")


_CHAT_RESPONSES: dict[int | str, dict] = {
    400: {"description": "问题为空（chat_empty_question）"},
    404: {"description": "conversation_id 不存在（conversation_not_found）"},
    422: {"description": "请求体不合法（多字段/超长/类型错）"},
}


def _summary(record: dict) -> dict:
    """列表用的精简视图（够列表显示 + 点进去取详情）。"""
    body = {field: record.get(field) for field in _SUMMARY_FIELDS}
    parsed = record.get("intent") or {}
    tool = record.get("tool") or {}
    facts = record.get("facts") or {}
    body["intent"] = parsed.get("intent")
    body["params"] = record.get("params")
    body["tool"] = tool.get("name")
    body["has_answer"] = bool((record.get("answer") or {}).get("text"))
    # 顺手把最关键的销售额带上（列表里一眼能看到"当时算出来多少"），有才有
    body["sales_amount"] = facts.get("sales_amount", facts.get("total_amount"))
    return body


# ── 0. 能力与数据边界 ───────────────────────────────────────────────────
@router.get("/chat/capabilities", summary="本版支持的问题类型 / 数据边界 / LLM 是否就绪")
def chat_capabilities() -> dict:
    """前端首屏用它渲染"能问什么"，也让用户一眼看到**数据边界**（Gate 的诚实要求）。"""
    profile = tools.dataset_profile()
    return {
        "intents": [
            {
                "name": name,
                "title": tools.TOOLS[name].title,
                "description": tools.TOOLS[name].description,
            }
            for name in intent_module.COMPUTE_INTENTS
        ],
        "unsupported": {
            "dimensions": [
                "区域/大区/片区", "省份/城市", "门店/渠道", "销售员/部门", "毛利/成本/折扣",
                # TASK-006：客户"属性类"维度 —— 数据里只有客户号，没有这些标签
                "VIP/客户等级/大客户", "客户行业/客户地区/客户渠道/客户生命周期",
            ],
            "reason": "数据集只有 8 列（InvoiceNo / StockCode / Description / Quantity / "
                      "InvoiceDate / UnitPrice / CustomerID / Country），没有这些字段；"
                      "**不会用 Country 代替区域**，也**不会用「销售额 TOP」顶替「VIP TOP」**。",
        },
        "llm": llm.status(),
        # 币种**显式声明**（同一个对象也随 data_profile 一起出去，两处同源）：
        # 数据集 8 列里没有货币字段，单位只能声明、不能猜 —— 事实段与前端都取这里。
        "currency": profile["currency"],
        "data_profile": profile,
        # 报告是**输出形态**，不是第 6 个 intent（`intents` 里不会多一项）：
        # 这里单独声明，前端才能把"周报/月报"写进能问什么的提示里。
        "report": {
            "periods": [
                {"key": key, "title": title, "description": description}
                for key, title, description in (
                    ("weekly", "销售周报", "按最近一个完整自然周出一份报告（周环比 + 逐日趋势 + 国家/商品结构）"),
                    ("monthly", "销售月报", "按最近一个完整自然月出一份报告（月环比 + 逐周趋势 + 国家/商品结构）"),
                )
            ],
            "export": "markdown",
            "note": "报告里的数字与上面五类问答**同一套计算**（不存在第二套口径）；"
                    "报告可预览、可下载 Markdown。",
        },
        "examples": [
            "2011年11月一共卖了多少？",
            "2011-11-01 到 2011-11-07 的销售趋势",
            "2011年11月卖得最好的5个产品",
            "2011年11月和10月的销售额对比",
            "2011年11月各国家销售额TOP5",
            "2011年11月相比10月，哪些国家推动了销售额变化？",
            # TASK-006：客户 / 商品
            "2011年11月销售额最高的10个客户",
            "2011年11月的复购率是多少？",
            "2011年11月有多少新客？",
            "沉睡客户有多少？（90天没买的）",
            "85123A 和 10002 在2011年11月的销售趋势",
            "2011年11月各商品的退货情况，前10名",
            "帮我根据本星期的销售数据做一份销售周报",
            "出一份 2011 年 11 月的月报",
            "VIP客户TOP10（数据不支持，会被明确拒绝）",
            "华南区上个月卖了多少？（数据不支持，会被明确拒绝）",
        ],
    }


# ── 1. 提问 ─────────────────────────────────────────────────────────────
@router.post("/chat", responses=_CHAT_RESPONSES, summary="自然语言提问 → 完整链路")
def chat(payload: ChatRequest) -> dict:
    """把问题交给 `service.ask()`，原样返回那条落盘记录。

    这里**不做任何加工**：记录就是链路，链路就是页面上分区显示的东西。
    端点层一旦开始"顺手整理一下响应"，页面看到的和落盘的就会分叉。
    """
    question = payload.question.strip()
    if not question:
        raise ChatApiError(400, "chat_empty_question", "问题不能为空（去掉空白后是空串）")
    return service.ask(question, use_llm=payload.use_llm)


# ── 2. 历史列表 ─────────────────────────────────────────────────────────
@router.get("/conversations", summary="历史提问（精简摘要，新的在前）")
def list_conversations(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    records, total = state.list_conversations(limit, offset)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "conversations": [_summary(record) for record in records],
    }


# ── 3. 单次问答的完整记录 ───────────────────────────────────────────────
@router.get("/conversations/{conversation_id}", responses=_CHAT_RESPONSES,
            summary="单次问答的完整链路（刷新后可恢复）")
def get_conversation(conversation_id: str) -> dict:
    record = state.get_conversation(conversation_id)
    if record is None:
        raise ChatApiError(404, "conversation_not_found", f"没有这次问答记录：{conversation_id}")
    return record
