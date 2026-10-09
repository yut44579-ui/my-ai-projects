"""调度层：去重 + 异步处理 + 回复路由。

★★ 这个文件解决的是**平台接入里最容易出事故的三件事**：

    ① **去重**：企微要求 5 秒内响应，超时会重发同一条消息。
       不去重的话用户问一句被答两遍，而且两遍可能不一样。
       → 靠 `message_id` 去重，落库（不能只放内存，重启就丢）。

    ② **异步**：模型生成要 3~15 秒，远超平台的等待窗口。
       所以必须"先回 200，后台慢慢算，算完主动推回去"。
       → 收进来立刻返回，处理放到后台队列。

    ③ **回复路由**：算完了要按判定决定这条怎么出去。
       auto → 直接发；draft → 进人工队列；escalate → 进人工队列；
       shadow → 什么都不发（但要留档）。
       ★ 这段逻辑**所有平台共享**，不能在各家适配器里各写一遍。

★ 为什么队列用 SQLite 表而不是内存队列：
    重启不丢。客服场景里"用户的消息"绝不能因为重启就消失 ——
    那是用户已经说出口的话，丢了就是丢了。
"""

from __future__ import annotations

import threading
from dataclasses import replace
import time
from dataclasses import dataclass
from typing import Any

from .. import config
from .. import governance, pipeline, store
from .base import Channel, InboundMessage, OutboundMessage

# 去重窗口。企微超时重试一般几十秒内，留 1 天足够宽。
DEDUP_WINDOW_HOURS = 24


@dataclass
class DispatchResult:
    accepted: bool
    duplicate: bool = False
    reason: str = ""
    qa_log_id: int | None = None

    def line(self) -> str:
        if self.duplicate:
            return "重复消息，已忽略（平台重试）"
        if not self.accepted:
            return f"未受理：{self.reason}"
        return "已受理，后台处理中"


# ══════════════════════════════════════════════════════════════════════
# ① 去重
# ══════════════════════════════════════════════════════════════════════

def seen_recently(key: str, tenant_id: str) -> bool:
    row = store.one(
        """SELECT id FROM inbound_log
            WHERE dedup_key=? AND tenant_id=?
              AND created_at >= datetime('now', ?)""",
        (key, tenant_id, f"-{DEDUP_WINDOW_HOURS} hours"),
    )
    return row is not None


def mark_seen(msg: InboundMessage, qa_log_id: int | None = None) -> int:
    return store.run(
        """INSERT OR IGNORE INTO inbound_log
             (dedup_key, tenant_id, platform, user_id, message_id,
              content, qa_log_id, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            msg.dedup_key(), msg.tenant_id, msg.platform, msg.user_id,
            msg.message_id, msg.content[:2000], qa_log_id, "accepted", store.now(),
        ),
    )


def set_status(key: str, status: str, qa_log_id: int | None = None) -> None:
    store.run(
        "UPDATE inbound_log SET status=?, qa_log_id=COALESCE(?, qa_log_id) WHERE dedup_key=?",
        (status, qa_log_id, key),
    )


# ══════════════════════════════════════════════════════════════════════
# ② 异步处理：先回 200，后台再算
# ══════════════════════════════════════════════════════════════════════

_QUEUE: list[tuple[InboundMessage, Channel]] = []
_QLOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_STOP = threading.Event()
# ★ 正在处理中的那个。drain() 必须等它，不能只看队列空没空。
_BUSY = threading.Event()

# 处理过的统计（给界面看"积压多少"）
_STATS = {"accepted": 0, "duplicate": 0, "processed": 0, "failed": 0, "merged": 0, "sent": 0}


def stats() -> dict[str, int]:
    with _QLOCK:
        pending = len(_QUEUE)
    return {**_STATS, "pending": pending}


def accept(msg: InboundMessage, channel: Channel) -> DispatchResult:
    """接收入站消息。★ 这个函数必须**立刻返回** —— 平台在等 200。

    真正的处理放到后台队列。平台那边 5 秒的窗口，我们不能用来跑模型。
    """
    if seen_recently(msg.dedup_key(), msg.tenant_id):
        _STATS["duplicate"] += 1
        return DispatchResult(False, duplicate=True)

    mark_seen(msg)
    _STATS["accepted"] += 1
    with _QLOCK:
        _QUEUE.append((msg, channel))
    ensure_worker()
    return DispatchResult(True)


def ensure_worker() -> None:
    """起后台处理线程（单线程就够了，客服量级不需要并发池）。

    ★ 单线程还有个好处：**天然串行**，不会出现同一个用户的两条消息
      被并发处理导致回答顺序颠倒。
    """
    global _WORKER
    if _WORKER and _WORKER.is_alive():
        return
    _STOP.clear()
    _WORKER = threading.Thread(target=_loop, name="kefu-dispatch", daemon=True)
    _WORKER.start()


def _same_conversation(a: InboundMessage, b: InboundMessage) -> bool:
    """两条消息是不是同一个人的同一个会话。"""
    return (
        a.tenant_id == b.tenant_id
        and a.platform == b.platform
        and (a.user_id or "") == (b.user_id or "")
        and (a.conversation_id or "") == (b.conversation_id or "")
    )


def _debounce_merge(msg: InboundMessage, channel: Channel) -> InboundMessage:
    """★ 防抖合并：等一小会儿，把同一个会话在这期间发来的消息并成一条。

    ★★ 为什么（实测）：
       客户连发「我想问一下」「关于报价的事」「你们能便宜点吗」——
       不合并的话 AI 对着第一句半句话回「没太确定你想问什么」，
       然后第二句转人工（队列里记的是它），第三句被静默。
       → 客户收到莫名其妙的澄清，**客服拿到的需求是半截的**。

    ★ 代价：每条消息多等 DEBOUNCE_SECONDS 秒。
      2.5 秒比大多数人打字间隔长，又短到不觉得卡。
    """
    import time as _t

    if config.DEBOUNCE_SECONDS <= 0:
        return msg

    _t.sleep(config.DEBOUNCE_SECONDS)

    extra: list[tuple[InboundMessage, Channel]] = []
    with _QLOCK:
        keep = []
        for item in _QUEUE:
            if len(extra) < config.DEBOUNCE_MAX_MESSAGES and _same_conversation(msg, item[0]):
                extra.append(item)
            else:
                keep.append(item)
        _QUEUE[:] = keep

    if not extra:
        return msg

    # ★ 拼成一条。用换行分隔 —— 检索和判定都能看到完整意思。
    merged_text = "\n".join([msg.content] + [m.content for m, _c in extra])
    merged = replace(msg, content=merged_text)
    # 被并进来的那些也标成已处理，否则它们会一直挂在"accepted"状态
    for m, _c in extra:
        set_status(m.dedup_key(), "processed")
        _STATS["merged"] = _STATS.get("merged", 0) + 1
    _STATS["processed"] += len(extra)
    return merged


def _loop() -> None:
    while not _STOP.is_set():
        item = None
        with _QLOCK:
            if _QUEUE:
                item = _QUEUE.pop(0)
        if item is None:
            time.sleep(0.3)
            continue
        msg, channel = item
        _BUSY.set()
        try:
            # ★ 先防抖合并，再处理 —— 客户连发几条时只答一次、答的是合并理解
            msg = _debounce_merge(msg, channel)
            _process(msg, channel)
            _STATS["processed"] += 1
        except Exception as exc:  # noqa: BLE001
            # ★ 单条失败不能把 worker 搞死 —— 搞死了后面所有消息都不处理，
            #   而且不会有任何提示（线程静默退出，外面完全看不出来）。
            _STATS["failed"] += 1
            _record_failure(msg, exc)
        finally:
            _BUSY.clear()


def _record_failure(msg: InboundMessage, exc: BaseException) -> None:
    """记失败。

    ★★ 这个函数**自己绝不能抛异常**。
      第一版把"记错误"和"处理消息"写在同一个 try 里，结果：
        处理炸了 → 进 except → 记错误时又炸（比如表结构不对）
        → 异常冒到 _loop 外面 → **worker 线程静默退出**
        → 后面所有消息都不处理，而且没有任何提示。

      教训：**错误处理路径必须比主路径更健壮**，不能假定它不会失败。
      所以这里每一段都单独兜住。
    """
    err = f"{type(exc).__name__}: {exc}"[:500]
    try:
        set_status(msg.dedup_key(), "failed")
    except Exception:  # noqa: BLE001
        pass
    try:
        store.run(
            """INSERT INTO dispatch_error (tenant_id, dedup_key, error, created_at)
               VALUES (?,?,?,?)""",
            (msg.tenant_id, msg.dedup_key(), err, store.now()),
        )
    except Exception:  # noqa: BLE001
        # 连记都记不下来，至少别把线程带走
        pass
    try:
        _ERRORS.append(err)
    except Exception:  # noqa: BLE001
        pass


# 出错信息的内存副本。★ 为什么除了库还要留一份：
#   如果连写库都失败了（表坏了/磁盘满），至少还能在界面上看到。
_ERRORS: list[str] = []


def errors(limit: int = 20) -> list[str]:
    return _ERRORS[-limit:]


def shutdown() -> None:
    _STOP.set()


def drain(timeout: float = 30.0) -> int:
    """等**全部处理完**（不只是队列空）。

    ★★ 第一版只等"队列为空"，结果测试全在读"处理到一半"的状态：
      消息已经从队列里取出来了，但答案还没生成、还没写库。
      表现是一堆莫名其妙的失败（人工队列是空的、状态还是 accepted）。

      教训：**"队列空"和"处理完"是两件事。**
      取出来到处理完之间有个窗口，等队列空等于在窗口里读数据。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        with _QLOCK:
            pending = len(_QUEUE)
        if pending == 0 and not _BUSY.is_set():
            # ★ 再等一拍，避开"刚 clear 完还没写下一条"的边界
            time.sleep(0.15)
            with _QLOCK:
                if not _QUEUE and not _BUSY.is_set():
                    return 0
        time.sleep(0.1)
    with _QLOCK:
        return len(_QUEUE)


def _notify_handover(channel: Channel, msg: InboundMessage, ans) -> None:
    """告诉用户"已经在转人工了"。

    ★★ 网页渠道发的是**会一直转圈的等待指示**，不是一句话。
      用户的原话：
        「你转人工的时候，只有在人工点了接管以后，才会出现已经转人工；
          否则会一直打转的状态，顺时针转的那种符号吧，
          然后人工接管之后才会停止转。」

      ★ 为什么这样对：
        发一句"已转人工，稍等"就结束了 —— 用户看到之后会想
        "转了吗？有人吗？要等多久？" 他没法判断到底有没有人在。
        转圈不一样：**转着 = 还在等，停了 = 有人了**。这是一个
        用户不用思考就能懂的状态指示。
    """
    try:
        if msg.platform == "web":
            from .web import WebChannel

            WebChannel.sys_note(msg.user_id, "正在为你转接人工客服…",
                                msg.tenant_id, kind="waiting")
        else:
            channel.send(
                OutboundMessage(
                    platform=msg.platform, tenant_id=msg.tenant_id, user_id=msg.user_id,
                    content="—— 已转人工，稍等 ——",
                    conversation_id=msg.conversation_id,
                    reply_token="system", reason="handover_notice",
                )
            )
    except Exception:  # noqa: BLE001
        # ★ 通知失败不能影响"消息已进人工队列"这个事实 ——
        #   和 _record_failure 同样的道理：辅助路径不能把主路径带崩。
        pass


def _retrieved_reference(ans) -> str:
    """转人工时给客服看的"AI 查到了什么"。

    ★ 为什么不是把 AI 那句话给他：
      转人工时 AI 的输出是「这个我答不了，我让人来回你」——
      客服看了等于什么都没看到，还得自己从头查一遍。
      给他**检索到的原文**，他扫一眼就知道该怎么说。

    ★ 为什么明确标注"AI 没有作答"：
      不标的话客服会以为这是 AI 的答案，直接复制发给客户，
      而它其实是资料原文（可能含内部措辞）。
    """
    hits = getattr(ans, "hits", None) or []
    if not hits:
        return "（AI 什么都没查到，需要你自己判断）"
    parts = ["（AI 没有作答，以下是它查到的原文，供你参考）"]
    for h in hits[:3]:
        title = getattr(h, "title", "") or getattr(h, "source_ref", "") or "资料"
        text = (getattr(h, "text", "") or "")[:300]
        parts.append(f"\n《{title}》\n{text}")
    return "\n".join(parts)


def _process(msg: InboundMessage, channel: Channel) -> None:
    """后台处理一条消息。"""
    ans = pipeline.answer_question(
        msg.content,
        tenant_id=msg.tenant_id,
        channel=msg.platform,
        is_internal=msg.is_internal,
        conversation_id=msg.conversation_id,
        user_ref=msg.user_id,
    )
    set_status(msg.dedup_key(), "processed", ans.qa_log_id)

    # ── ③ 回复路由 ────────────────────────────────────────────────
    #
    # ★★ 先处理 silent（人工接管中）：
    #   用户的原话：「你客服接入了之后，就一直是客服谈话了，不要又弹出其他的」。
    #   AI **什么都不发**。但消息已经记进 qa_log 了，
    #   人工在工作台能看到客户又说了什么（他正在处理，需要知道）。
    if ans.decision == "silent":
        _STATS["silent"] = _STATS.get("silent", 0) + 1
        return

    if ans.decision == "auto":
        out = OutboundMessage(
            platform=msg.platform, tenant_id=msg.tenant_id, user_id=msg.user_id,
            content=ans.text, conversation_id=msg.conversation_id,
            reply_token=msg.reply_token, reason="auto",
        )
        resp = channel.send(out)
        _STATS["sent"] += 1
        store.run(
            """INSERT INTO outbound_log
                 (tenant_id, platform, user_id, content, decision, resp, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (msg.tenant_id, msg.platform, msg.user_id, ans.text[:2000],
             "auto", store.jdump(resp)[:1000], store.now()),
        )
    elif ans.decision in ("draft", "escalate"):
        # 进人工队列。
        #
        # ★★ 但**必须给用户一个交代**，不能让他干等。
        #
        #   这里和 D6/D7（"转人工就直接转，不要说'我叫个同事'"）看着矛盾，
        #   其实不矛盾 —— 区别在于**谁在说话**：
        #     ✗ AI 说："我叫个同事帮你看下"     ← AI 在解释自己（禁止）
        #     ✗ AI 说："已转人工 · 财务 李姐"    ← 暴露岗位和姓名（禁止）
        #     ✓ 系统提示："—— 已转人工，稍等 ——"  ← 系统在说事实，不是 AI 在解释
        #
        #   不发这条的话，用户发完消息就面对一片寂静 ——
        #   他会以为系统坏了，然后重复发、或者直接走掉。
        _notify_handover(channel, msg, ans)
        # ★ 给客服参考的东西，要看是哪一种转人工：
        #   draft    → AI 真的写了一句（只是没把握），把那句给他，他能直接改
        #   escalate → AI 写的是"我答不了"这句套话，**对客服毫无用处**
        #              真正有用的是"AI 查到了什么"，让他不用从头摸索
        if ans.decision == "draft":
            reference = ans.text or ""
        else:
            reference = _retrieved_reference(ans)
        store.run(
            """INSERT INTO handover_queue
                 (tenant_id, platform, user_id, conversation_id, question,
                  ai_answer, decision, reason, qa_log_id, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?, 'open', ?)""",
            (
                msg.tenant_id, msg.platform, msg.user_id, msg.conversation_id,
                msg.content[:500], reference[:2000], ans.decision,
                ans.reason[:500], ans.qa_log_id, store.now(),
            ),
        )
    elif ans.decision == "shadow":
        # 影子模式：什么都不发，但留档（对照数据靠它）
        _STATS["sent"] += 0
    if governance.is_stopped(msg.tenant_id):
        # 急停期间进来的消息也进人工队列（不能丢）
        pass
