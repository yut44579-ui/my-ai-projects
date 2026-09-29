"""system_info.py · 「关于系统自己」的问题：导入记录 / 身份 / 我没有的能力（FR-010-A3 + A4）。

════════════════════════════════════════════════════════════════════════
【为什么这些要单独有一层】
════════════════════════════════════════════════════════════════════════
FR-010-A 之前，这三类问题都被当成"数据里没有这个维度"：
「我刚刚导入的文件在哪里」「你是什么」「你能查天气吗」—— 用户收到一段很长的
"数据不支持这个问题"，而系统为了拒绝还**先去读了一遍销售数据**。

这三件事其实都不需要销售数据：
    导入记录   → 查本机的导入流水（FR-003）与统一文档层（FR-009-A）就有
    身份       → 一句话，本来就知道
    能力边界   → 没有就是没有，说清楚比绕弯子强

所以本模块回答的全是**关于系统自身**的问题：它读的是**记录**，不是销售数据。
词表与话术都收在这一处，`routing.py`（判路）与 `general.py`（回答）都从这里取 ——
两处各抄一份，早晚会漂移成"判得出来却答不上来"。

════════════════════════════════════════════════════════════════════════
【★ 它不 import 任何销售链路模块】
════════════════════════════════════════════════════════════════════════
本文件不 import `app.ai.tools` / `app.engine.*` —— 想在这里读销售数据，得先加一行 import，
而那行会被 `tests/test_fr007_isolation.py` 的"炸弹"测试当场炸出来（那条测试把读数据的
四个入口全换成 AssertionError）。这正是本模块存在的意义之一：能力边界要**结构性**成立，
不是靠提示词里写一句"请不要查销售数据"。

════════════════════════════════════════════════════════════════════════
【★ 能力边界的判据分**严**与**宽**两档（别合并）】
════════════════════════════════════════════════════════════════════════
    looks_like_capability_question()   路由用，**严**：只认"你有没有这项能力"的问法
                                       （「你能查天气吗」✓；「今天天气怎么样」✗）
    decline_for()                      回答用，**宽**：只要问的是天气/预测，就给那句实话

为什么严的那一档不能放宽：裸问一句「今天天气怎么样」在 FR-007 里走的是**澄清/解析失败**
那条既有通道（status=error、不生成回答段），tests/test_chat.py 与
tests/test_fr007_isolation.py 两条既有验收钉着它。本 TASK 不动那条通道的形状 ——
它只是把用户看到的那句"没听懂"换成**更诚实的那句"我没有天气数据"**（见 `service.ask`）。
"""

from __future__ import annotations

from typing import Any

from app.importer import db, models, store
from app.repositories import unified_documents

# ════════════════════════════════════════════════════════════════════════
# ① 词表（全部是"必要条件"，不是单一判据）
# ════════════════════════════════════════════════════════════════════════
# ── A4：不具备的能力 ──────────────────────────────────────────────────
#: 天气/气象类（数据源里**没有**这些字段，也没有接任何外部服务）
WEATHER_WORDS: tuple[str, ...] = (
    "天气", "气温", "温度", "下雨", "下雪", "降雨", "台风", "空气质量", "湿度", "紫外线", "风力",
)
#: 预测/外推类（本版**只做已经发生的事的确定性聚合**，不做预测模型）
PREDICTION_WORDS: tuple[str, ...] = (
    "预测", "预估", "预判", "外推", "推算", "forecast", "Forecast", "FORECAST",
)
#: 「你有没有这项能力」的问法（路由靠它把"问能力"与"直接要数据"分开）
_CAPABILITY_FRAMING: tuple[str, ...] = (
    "查天气", "查气温", "看天气", "天气预报", "天气数据", "天气情况", "气温数据",
    "能查", "能看", "会查", "支持",
)

# ── A3：系统元信息（导入记录）─────────────────────────────────────────
# 判据两件事同时成立：点了"导入/上传"这件事 + 在问它的**记录**（什么/几份/记录/刚刚…）。
# 为什么要求同时成立：光有"导入"两字的问法是「怎么导入数据」—— 那是**使用说明**（静态文案），
# 不该被这条抢走；反过来"我刚刚导入的文件在哪里"要的是**真实记录**，静态文案答不了它。
_META_TOPIC_WORDS: tuple[str, ...] = ("导入", "上传")
#: 「在问那份记录」的词 —— 它们**单独出现**就足以说明问的是我的导入流水
_META_ASK_WORDS: tuple[str, ...] = (
    "几份", "几个", "几次", "多少份", "多少条", "记录", "历史",
    "最新", "最近", "刚刚", "刚才", "上次", "上一次", "在哪", "哪里", "去哪",
)
#: 「什么/哪些」这类**指代不明**的词：只有配上第一人称（我/我的/自己）才算在问"我的记录"。
#: 为什么必须加这道限制：「上传支持哪些格式」问的是**能力**（使用说明），
#: 光凭"哪些"就判成元信息，会把它抢答成"你还没有导入过任何资料" —— 答非所问。
_META_ASK_WORDS_SELF: tuple[str, ...] = ("什么", "哪些", "哪些文件", "啥", "什么时候")
_SELF_WORDS: tuple[str, ...] = ("我", "我的", "自己")

# ── A3：身份 ──────────────────────────────────────────────────────────
#: 「你是什么」这类问法 —— 一句话能答完，**不展开十几项能力**
_IDENTITY_WORDS: tuple[str, ...] = (
    "你是什么", "你是谁", "你叫什么", "你是干什么", "你是啥", "自我介绍", "介绍一下你",
)
#: 用户明说要**展开**时（"详细说说你能做什么"）才给能力清单 —— 不问不倒长清单
_IDENTITY_DETAIL_WORDS: tuple[str, ...] = (
    "详细", "展开", "具体说", "多说", "能做什么", "能干什么", "支持哪些", "支持什么", "有哪些能力",
)


# ════════════════════════════════════════════════════════════════════════
# ② A4：两句如实的话（**不编、不绕弯、不假装**）
# ════════════════════════════════════════════════════════════════════════
WEATHER_DECLINE = (
    "我这里目前没有天气数据，暂时没法查天气。"
    "我目前主要能帮你处理销售数据、生成报表，以及理解你已经导入的业务资料。"
)

PREDICTION_DECLINE = (
    "这个我给不了。我只做已经发生的事的确定性聚合（已经算出来的销售额、趋势、对比），"
    "不做预测模型，也不拿「按最近趋势外推」冒充预测。"
)

DECLINES: dict[str, str] = {"weather": WEATHER_DECLINE, "prediction": PREDICTION_DECLINE}


def _has_weather(text: str) -> bool:
    return any(word in text for word in WEATHER_WORDS)


def _has_prediction(text: str) -> bool:
    return any(word in text for word in PREDICTION_WORDS)


def looks_like_capability_question(text: str) -> bool:
    """**路由用（严）**：这句是不是在问「你有没有某项能力」。

    预测类不问自明（「帮我预测下个月能卖多少」= 要一项我给不了的能力）；
    天气类必须带上"查/看/有没有"这类**索取能力**的说法才算 —— 裸问天气不算（见模块开头）。
    """
    question = (text or "").strip()
    if not question:
        return False
    if _has_prediction(question):
        return True
    if not _has_weather(question):
        return False
    return any(word in question for word in _CAPABILITY_FRAMING)


def decline_for(question: str) -> tuple[str, str] | None:
    """**回答用（宽）**：这句话问的是我没有的能力吗？是 → `(kind, 那句实话)`，不是 → None。

    预测排在天气前面：两句都命中时（"预测明天下雨吗"），说"我给不了预测"更贴近用户问的事。
    """
    text = (question or "").strip()
    if not text:
        return None
    if _has_prediction(text):
        return "prediction", PREDICTION_DECLINE
    if _has_weather(text):
        return "weather", WEATHER_DECLINE
    return None


# ════════════════════════════════════════════════════════════════════════
# ③ A3：身份（一句话）与系统元信息（查真实记录）
# ════════════════════════════════════════════════════════════════════════
IDENTITY_TITLE = "我是谁"
IDENTITY_LINE = (
    "我是你的销售分析助手，可以帮你查询和分析销售数据、生成销售报表，"
    "也可以查看和理解你已经导入的业务资料。"
)

RECORDS_TITLE = "你导入的资料"

#: 一份导入记录都没查到（**真的空库**，不是"没读到"）
NO_RECORDS = (
    "还没有导入过任何资料。在「数据管理」页点上传，把 {formats} 拖进去就行 —— "
    "导入之后每一次的结果都会留在「数据管理」→「导入记录」里。"
)
#: ★ 读不出来时**必须**与"没有导入过"分开说 —— 把读失败说成"你没有导入过"就是一句假话
RECORDS_UNAVAILABLE = (
    "你的导入记录这次读不出来（本机存储暂时不可用）—— 这不是「没有导入过」，"
    "是这次没读到。稍后再问一次试试。"
)

#: 导入状态 → 人话（`store.list_imports` 的 status 取值）
_STATUS_LABELS: dict[str, str] = {
    "success": "已成功保存",
    "failed": "这次没有成功",
    "skipped": "之前已经导入过（内容一样，没有重复入库）",
}


def is_meta_question(text: str) -> bool:
    """「我刚刚导入的文件在哪里 / 我导入了几份文件」——**系统元信息**（A3）。

    两件事同时成立才算：点了"导入/上传"这件事 + 在问它的**记录**。
    第二件事分两档（见上面两个词表的注释）：
      · 记录类词（刚刚/最新/几份/在哪…）单独出现就够；
      · 指代不明的「什么/哪些」必须配第一人称 —— 否则「上传支持哪些格式」这种
        **问能力**的问题会被抢答成"你还没有导入过任何资料"。
    """
    question = (text or "").strip()
    if not any(word in question for word in _META_TOPIC_WORDS):
        return False
    if any(word in question for word in _META_ASK_WORDS):
        return True
    has_self = any(word in question for word in _SELF_WORDS)
    return has_self and any(word in question for word in _META_ASK_WORDS_SELF)


def is_identity_question(text: str) -> bool:
    """「你是什么 / 你是谁」——**没明说要展开**时算这一类（要展开就走使用说明）。"""
    question = (text or "").strip()
    if not any(word in question for word in _IDENTITY_WORDS):
        return False
    return not any(word in question for word in _IDENTITY_DETAIL_WORDS)


def identity_answer(question: str) -> tuple[str, str] | None:
    """身份问题 → **一句话**（A5：一句话能答完的就一句话答完）。"""
    if not is_identity_question(question):
        return None
    return IDENTITY_TITLE, IDENTITY_LINE


def _format_labels() -> str:
    """可导入的格式（人话）——**从 models.SOURCE_TYPES 的声明里取**，不另抄一份清单。

    括号里的补充（如「Excel 工作簿（含宏）」）在"能传什么格式"这句话里是噪音，去掉。
    """
    labels: list[str] = []
    for suffix in models.SOURCE_TYPES:
        label = str(models.SUFFIX_LABELS[suffix]).split("（")[0]
        if label not in labels:
            labels.append(label)
    return " / ".join(labels)


def _source_type_label(source_type: Any) -> str:
    """格式名（pdf/excel/docx…）→ 中文（PDF 文档 / Excel 工作簿…）。

    与 `app/api_imports.py::_SOURCE_TYPE_LABELS` 是**同一个来源**（`models.SOURCE_TYPES`）的
    两份推导。不共用那个常量是因为它住在 FastAPI 端点模块里 —— 把端点层拉进这条
    非销售回答链路，只为了拿几个中文标签，代价比这两行推导大。
    """
    text = str(source_type or "")
    for spec in models.SOURCE_TYPES.values():
        if str(spec["source_type"]) == text:
            return str(spec["label"])
    return ""


def _human_time(value: Any) -> str:
    """`2026-09-29T18:28:05+08:00` → `2026-09-29 18:28`（人看的时间）。"""
    text = str(value or "")
    if len(text) >= 16 and text[4] == "-" and text[10] in ("T", " "):
        return f"{text[:10]} {text[11:16]}"
    return text or "（时间未知）"


def _read_imports(limit: int = 50) -> dict[str, Any] | None:
    """读真实的导入记录（**唯一的取数口**）。

    走的是两条既有仓储 —— **不另写一套取数逻辑**：
        `app.importer.store.list_imports`        导入流水（成功/失败都留，新的在前）
        `app.repositories.unified_documents`     FR-009-A 的统一文档层（含历史文档来源）

    ★ 读不出来返回 None —— 调用方**必须**把它与"没有记录"分开说（见 RECORDS_UNAVAILABLE）。
    """
    try:
        with db.readonly() as connection:
            records, total = store.list_imports(connection, limit=limit, offset=0)
            char_counts = store.document_char_counts(
                connection, [str(item["document_id"]) for item in records if item.get("document_id")]
            )
            row_counts = store.dataset_row_counts(
                connection, [str(item["dataset_id"]) for item in records if item.get("dataset_id")]
            )
    except Exception:                                     # noqa: BLE001 —— 读不出来就是读不出来
        return None
    document_total: int | None
    try:
        _page, document_total, _notes = unified_documents.list_documents(limit=1, offset=0)
    except Exception:                                     # noqa: BLE001
        document_total = None
    return {
        "records": records,
        "total": int(total),
        "char_counts": char_counts,
        "row_counts": row_counts,
        "document_total": document_total,
    }


def _status_text(record: dict[str, Any]) -> str:
    return _STATUS_LABELS.get(str(record.get("status")), f"状态是 {record.get('status')!r}")


def _fall_into(record: dict[str, Any], overview: dict[str, Any]) -> str:
    """这条记录"落成了什么"（文档看字数、数据表看行数）—— 都没有就不提。"""
    parts: list[str] = []
    document_id = record.get("document_id")
    if document_id:
        chars = overview["char_counts"].get(str(document_id))
        parts.append(f"{chars:,} 字" if isinstance(chars, int) else "文档正文")
    dataset_id = record.get("dataset_id")
    if dataset_id:
        rows = overview["row_counts"].get(str(dataset_id))
        parts.append(f"{rows:,} 行数据" if isinstance(rows, int) else "一份数据表")
    return "，".join(parts)


def _where_text(record: dict[str, Any]) -> str:
    """「去哪儿看」—— 页面名与真实页面对得上（数据管理 · 导入记录 / 文档资料 / 数据源）。"""
    parts = ["可以在左侧「数据管理」→「导入记录」里看到它"]
    if record.get("document_id"):
        parts.append("文档正文在「文档资料」里")
    if record.get("dataset_id"):
        parts.append("这份数据在「数据源」里")
    return "，".join(parts) + "。"


def _describe(record: dict[str, Any], overview: dict[str, Any]) -> str:
    """一条导入记录 → 一句人话（文件名 / 类型 / 落成了什么 / 入库时间 / 去哪儿看）。"""
    filename = str(record.get("source_filename") or "（没有文件名）")
    kind = _source_type_label(record.get("source_type"))
    when = _human_time(record.get("imported_at"))
    landed = _fall_into(record, overview)
    status = str(record.get("status"))

    if status == "success":
        detail = f"（{kind}，{landed}，{when} 入库）" if landed else f"（{kind}，{when} 入库）"
        return f"你刚刚导入的《{filename}》{detail}已成功保存。" + _where_text(record)
    if status == "skipped":
        return (
            f"《{filename}》这次是「{_status_text(record)}」—— 同一份文件之前就导过了，"
            f"库里留的还是最早那一份（最近一条记录在 {when}）。" + _where_text(record)
        )
    if status == "failed":
        return (
            f"最近的这条导入记录是《{filename}》，{_status_text(record)}（{when}）—— "
            "所以现在还用不上它。失败的原因留在「数据管理」→「导入记录」里那一条上。"
        )
    return f"最近一条导入记录是《{filename}》，{_status_text(record)}（{when}）。"


def import_records_answer(question: str) -> tuple[str, str] | None:
    """「我刚刚导入的文件在哪里 / 我导入了几份文件 / 我最新导入了什么」→ **查真实记录**再答。

    ★ 三件事必须分开说清楚，一句都不能混（这是 A3 的诚实底线）：
        · 真的没有导入过   → 明说"还没有导入过任何资料"+ 怎么导入（**不许编文件名**）
        · 记录读不出来     → 明说是"这次没读到"，**不说成"你没有导入过"**
        · 有记录           → 文件名/类型/字数或行数/入库时间/去哪儿看，逐项照抄
    """
    if not is_meta_question(question):
        return None

    overview = _read_imports()
    if overview is None:
        return RECORDS_TITLE, RECORDS_UNAVAILABLE

    records = overview["records"]
    if not records:
        return RECORDS_TITLE, NO_RECORDS.format(formats=_format_labels())

    lines = [_describe(records[0], overview)]

    # 问"几份/几次/多少条"时才报总数（A5：问题有多大，回答就做到多大）
    asked_count = any(word in question for word in ("几份", "几个", "几次", "多少份", "多少条"))
    if asked_count:
        total = overview["total"]
        ok = sum(1 for item in records if str(item.get("status")) == "success")
        tail = f"你的导入记录一共 {total} 条（这份列表里成功 {ok} 条）"
        if len(records) < total:
            tail += f"，这里只看了最近 {len(records)} 条"
        documents = overview.get("document_total")
        if isinstance(documents, int):
            tail += f"；库里的文档资料共 {documents} 份"
        lines.append(tail + "。")
    return RECORDS_TITLE, "\n".join(lines)


def system_self_answer(question: str) -> tuple[str, str, str] | None:
    """**本模块的唯一入口**：这句话是关于系统自己的问题吗？是 → `(标题, 正文, 类别)`。

    类别（`kind`）供调用方决定状态与提示语：
        "meta"      导入记录（查了真实记录）
        "identity"  身份（一句话）
        "capability"我没有的能力（明说没有）
    None = 不是这三类，由 `general.system_answer` 继续往下判（使用说明 / 概念问答）。
    """
    for kind, builder in (
        ("meta", import_records_answer),
        ("identity", identity_answer),
    ):
        got = builder(question)
        if got is not None:
            title, text = got
            return title, text, kind
    declined = decline_for(question)
    if declined is not None:
        kind, text = declined
        return "这件事我做不了", text, "capability"
    return None


__all__ = [
    "DECLINES",
    "IDENTITY_LINE",
    "IDENTITY_TITLE",
    "NO_RECORDS",
    "PREDICTION_DECLINE",
    "PREDICTION_WORDS",
    "RECORDS_TITLE",
    "RECORDS_UNAVAILABLE",
    "WEATHER_DECLINE",
    "WEATHER_WORDS",
    "decline_for",
    "identity_answer",
    "import_records_answer",
    "is_identity_question",
    "is_meta_question",
    "looks_like_capability_question",
    "system_self_answer",
]
