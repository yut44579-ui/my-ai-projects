"""存储层（SQLite）。

★ 这个文件是整个系统的地基，重点在**问答日志**和**反馈**两张表。

为什么它们最重要：
    "越用越聪明"听起来像要靠训练，实际上 80% 的效果来自
    —— 记录每次问答的结果，然后按**失败原因分类**去修补。
    没有记录，学习就是瞎猜；失败原因不分清，修补就是乱改。

所以日志不是"顺便记一下"，而是产品的核心资产：
    · 没答上来的  → 沉淀成待补充问题（补知识）
    · 检索没命中  → 补同义词/黑话（改检索）
    · 答了但被改  → 变成风格示例（改话术）
    · 本来不该答  → 改判定规则（改转人工边界）
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH

_LOCK = threading.Lock()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ══════════════════════════════════════════════════════════════
-- 知识（连接器拉进来的东西）
-- ══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS doc (
    id            TEXT PRIMARY KEY,       -- 源内稳定 ID（用于增量同步）
    tenant_id     TEXT NOT NULL,
    source        TEXT NOT NULL,          -- folder / feishu / yuque / url ...
    source_ref    TEXT,                   -- 源里的路径 / URL / 文档 ID
    title         TEXT NOT NULL DEFAULT '',
    updated_at    TEXT,                   -- ★ 源里的最后修改时间（时效性判定用）
    content_hash  TEXT,                   -- 内容哈希，用来判断有没有变
    permission    TEXT NOT NULL DEFAULT 'external',  -- external / internal
    synced_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_tenant ON doc(tenant_id, source);

CREATE TABLE IF NOT EXISTS chunk (
    id          INTEGER PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES doc(id) ON DELETE CASCADE,
    tenant_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunk(doc_id);

-- ══════════════════════════════════════════════════════════════
-- ★★ 问答日志 —— 学习闭环的地基
-- ══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS qa_log (
    id                INTEGER PRIMARY KEY,
    tenant_id         TEXT NOT NULL,
    channel           TEXT,                     -- feishu / wecom / web
    conversation_id   TEXT,
    user_ref          TEXT,                     -- 用户标识（脱敏后）
    is_internal       INTEGER NOT NULL DEFAULT 0,  -- 1=内部员工 0=外部客户

    question_raw      TEXT NOT NULL,            -- ★ 用户原话（口语，宝贵）
    question_rewrite  TEXT,                     -- 改写后的检索词
    context_json      TEXT,                     -- 上下文（指代补全用）

    retrieved_json    TEXT,                     -- 命中的块 + 分数
    quality           REAL,                     -- 检索质量 0~1（各信号综合）
    signals_json      TEXT,                     -- 各信号原值，便于事后复盘
    citations_json    TEXT,                     -- 出处

    answer_text       TEXT,
    confidence        REAL,
    decision          TEXT,                     -- auto / draft / escalate / shadow
    -- ★ 命中了哪条转人工的意图规则（报价/合同/退款/投诉…）。
    --   必须落库：后果预览要靠它复现"意图优先于分数"的判定顺序。
    --   没有它，预览会按纯分数重算，把"报价"这类算成自动发 —— 数字骗人。
    intent            TEXT,
    -- ★ 影子模式用：没有治理开关干预时，本来会怎么判。
    --   必须落库，不能只在内存里 ——
    --   影子报告如果靠"重新推算"（用 quality 反推），会漏掉
    --   意图命中和出站检查拦下的那些，**把覆盖率算高**。
    --   客户是拿这个数字决定"要不要开自动"的，算高了就是误导。
    would_be_decision TEXT,

    -- ★ 结果 —— 学习全靠这几列
    outcome           TEXT NOT NULL DEFAULT 'pending',
                      -- pending / sent / accepted / edited / rejected
                      -- / escalated / unanswered
    failure_reason    TEXT,
                      -- no_hit       检索没命中（缺知识）
                      -- stale        命中了但过期（缺时效）
                      -- bad_style    答了但不像人话（缺风格示例）
                      -- wrong_fact   答错了（缺事实约束）
                      -- should_escalate 本来就不该答（判定错了）
                      -- banned       命中了禁用内容（安全）
    edited_to         TEXT,                     -- ★ 人工最终发出去的话

    latency_ms        INTEGER,
    tokens_in         INTEGER,
    tokens_out        INTEGER,
    cost              REAL,
    created_at        TEXT NOT NULL,
    decided_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_qa_tenant_time ON qa_log(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_outcome ON qa_log(outcome);
CREATE INDEX IF NOT EXISTS idx_qa_reason ON qa_log(failure_reason);

-- 反馈（人工改草稿 = 最主要的、零成本的反馈来源）
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY,
    qa_log_id   INTEGER REFERENCES qa_log(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,   -- operator_edit / customer_retry / thumb / admin_review
    kind        TEXT NOT NULL,   -- positive / negative / correction
    detail_json TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fb_qa ON feedback(qa_log_id);

-- ══════════════════════════════════════════════════════════════
-- 学习产物（四类，对应四种失败原因）
-- ══════════════════════════════════════════════════════════════
-- ① 没答上来 → 待补充问题（按频次排，推给管理员）
CREATE TABLE IF NOT EXISTS gap_item (
    id             INTEGER PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    question_norm  TEXT NOT NULL,      -- 归一化（去标点/统一说法），用于合并同类
    sample_question TEXT NOT NULL,     -- 保留一条原话样本
    occurrences    INTEGER NOT NULL DEFAULT 1,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open',  -- open / added / ignored
    resolved_doc_id TEXT,
    UNIQUE(tenant_id, question_norm)
);

-- ② 答了但被改 → 风格示例（拿这家公司真人说法当范本）
--
-- ★ status 是"评测关卡"用的（见 learning/eval.py）：
--     pending  刚学到，还没验证 —— 不参与回答
--     active   验证通过，参与回答
--     rejected 验证后准确率下降，弃用
--   为什么要 pending：学到的东西**不能立刻生效**，
--   否则一条噪音示例会污染所有后续回答，而且你事后根本不知道是哪条带偏的。
CREATE TABLE IF NOT EXISTS style_example (
    id            INTEGER PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    question      TEXT NOT NULL,
    answer_before TEXT,                -- AI 原来写的
    answer_after  TEXT NOT NULL,       -- 人工改成并真发出去的
    diff_summary  TEXT,                -- 改了什么（人话）
    enabled       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'pending',
    weight        REAL NOT NULL DEFAULT 1.0,
    created_at    TEXT NOT NULL,
    UNIQUE(tenant_id, question, answer_after)
);

-- ③ 检索没命中 → 同义词 / 内部黑话
--    status 含义同上：同义词加错了会让检索拉进不相关文档，
--    比不命中更糟，所以也要过闸门。
CREATE TABLE IF NOT EXISTS synonym (
    id         INTEGER PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    colloquial TEXT NOT NULL,          -- 口语 / 黑话（报消、打款、过一下）
    standard   TEXT NOT NULL,          -- 标准说法（报销、付款、审批）
    source     TEXT NOT NULL DEFAULT 'manual',  -- manual / mined
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, colloquial, standard)
);

-- ④ 评测集 —— ★ 每次学习必须过这一关，否则"越学越偏"
CREATE TABLE IF NOT EXISTS eval_case (
    id               INTEGER PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    question         TEXT NOT NULL,
    must_contain     TEXT,   -- JSON 数组：回答里必须出现的要点
    must_not_contain TEXT,   -- JSON 数组：★ 禁用词（身份泄漏/承诺/敏感）
    expect_decision  TEXT,   -- 期望判定 auto/draft/escalate（可空）
    note             TEXT,
    enabled          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_run (
    id          INTEGER PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    label       TEXT NOT NULL,     -- 这次跑是为了验证哪次学习
    total       INTEGER NOT NULL,
    passed      INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    detail_json TEXT,
    created_at  TEXT NOT NULL
);

-- ══════════════════════════════════════════════════════════════
-- 配置（档位等；安全底线不在这里，是代码常量）
-- ══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS tenant_config (
    tenant_id  TEXT PRIMARY KEY,
    tier       TEXT NOT NULL DEFAULT 'standard',   -- conservative/standard/aggressive
    config_json TEXT,
    updated_at TEXT NOT NULL
);

-- 平台强制的安全底线（客户不可改，只记录生效值以便审计）
CREATE TABLE IF NOT EXISTS platform_policy (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ★ 运行期设置（模型配置等）。
--   为什么要落库：原来只能改 .env 然后重启 ——
--   对要交给客户用的产品来说，"模型必须能在界面上配"。
CREATE TABLE IF NOT EXISTS setting (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ★★ 问题上报（客服/销售 → 技术）
--
-- ★ 为什么要有这张表（用户的原始需求）：
--   「使用这套系统的通常都是不怎么会用 AI 的小白销售或者客服，
--     只会去回复话术。点了这个就会有关于技术人员专门处理这些问题的界面」
--
-- ★ 核心设计是**报的人不需要懂技术**：
--   他只要选个"哪儿不对"+写一句"发生了什么"，
--   剩下的（哪个会话、哪句问答、AI 当时怎么判的、检索质量多少、
--   有没有报错）**全部由系统自动附上**。
--   让他去复制日志、贴报错信息，等于这件事不会有人做。
CREATE TABLE IF NOT EXISTS issue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    -- 报的人填的（尽量少）
    title         TEXT NOT NULL,          -- 一句话：哪儿不对
    kind          TEXT NOT NULL,          -- 类型（下拉选，见 ISSUE_KINDS）
    detail        TEXT,                   -- 详细描述（可留空）
    severity      TEXT NOT NULL DEFAULT 'normal',  -- 影响面
    reporter      TEXT,                   -- 谁报的
    reporter_role TEXT,                   -- 客服 / 销售 / 其他
    -- ★ 系统自动附的现场（报的人不用管）
    context_json  TEXT,
    page          TEXT,                   -- 在哪个界面点的
    -- 技术那边
    status        TEXT NOT NULL DEFAULT 'new',
                  -- new 待处理 / working 处理中 / resolved 已解决
                  -- / closed 已确认关闭 / rejected 不是问题
    assignee      TEXT,
    taken_at      TEXT,
    resolution    TEXT,                   -- ★ 技术填的：怎么解决的
    resolution_kind TEXT,                 -- 知识库没资料 / 代码bug / 配置问题 / 使用问题
    resolved_at   TEXT,
    -- ★ 报的人确认（闭环的关键）
    reporter_ack  TEXT,                   -- ok 好了 / no 还没好
    reporter_note TEXT,
    ack_at        TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issue_status ON issue(tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_issue_ack ON issue(status, reporter_ack);

-- ★ 急停留痕。为什么必须留：
--   急停是"出事时"才会按的按钮。事后复盘时，
--   "谁在什么时候按的、当时以为什么"是**唯一能还原现场的东西**。
--   没有它，一次事故会变成互相猜。
CREATE TABLE IF NOT EXISTS stop_log (
    id         INTEGER PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    action     TEXT NOT NULL,     -- stop / resume
    by_who     TEXT,
    reason     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stop_tenant ON stop_log(tenant_id, created_at DESC);

-- ══════════════════════════════════════════════════════════════
-- 渠道调度（去重 / 出站留档 / 处理失败 / 人工队列）
-- ══════════════════════════════════════════════════════════════

-- ★ 去重表。**为什么必须落库而不是放内存**：
--   企微超时会重发同一条消息。如果去重记录在内存里，重启后
--   历史重试就会被当成新消息 —— 用户被答两遍。
--   而且这张表本身就是排查"为什么重复回复"的唯一依据。
CREATE TABLE IF NOT EXISTS inbound_log (
    id         INTEGER PRIMARY KEY,
    dedup_key  TEXT NOT NULL,
    tenant_id  TEXT NOT NULL,
    platform   TEXT NOT NULL,
    user_id    TEXT,
    message_id TEXT,
    content    TEXT,
    qa_log_id  INTEGER,
    status     TEXT NOT NULL DEFAULT 'accepted',  -- accepted/processed/failed
    created_at TEXT NOT NULL,
    UNIQUE(dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_inb_time ON inbound_log(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inb_user ON inbound_log(tenant_id, user_id, created_at DESC);

-- 出站留档。★ 发给用户的话必须有记录：
--   出事时要能查"这句话是什么时候、按什么判定发出去的"。
CREATE TABLE IF NOT EXISTS outbound_log (
    id         INTEGER PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    platform   TEXT NOT NULL,
    user_id    TEXT,
    content    TEXT NOT NULL,
    decision   TEXT,          -- auto / human / draft_confirmed
    resp       TEXT,          -- 平台响应（排查发送失败用）
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_out_time ON outbound_log(tenant_id, created_at DESC);

-- 处理失败留档。★ 不留的话，"有消息没被处理"是静默的 ——
--   worker 抛异常线程就退出了，而外面完全看不出来。
CREATE TABLE IF NOT EXISTS dispatch_error (
    id         INTEGER PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    dedup_key  TEXT,
    error      TEXT,
    created_at TEXT NOT NULL
);

-- ★ 人工队列。转人工的消息进这里，等客服接手。
--   带上 AI 已经查到的答案和判定理由 ——
--   **不能让客户重说一遍**，也不能让客服从头摸索。
CREATE TABLE IF NOT EXISTS handover_queue (
    id              INTEGER PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    platform        TEXT NOT NULL,
    user_id         TEXT,
    conversation_id TEXT,
    question        TEXT NOT NULL,
    ai_answer       TEXT,          -- AI 本来想说什么（客服可参考/改写）
    decision        TEXT,          -- draft / escalate
    reason          TEXT,          -- 为什么转人工（给客服看的）
    qa_log_id       INTEGER,
    assigned_to     TEXT,          -- 接手的人
    reply_text      TEXT,          -- 客服最终发出去的
    status          TEXT NOT NULL DEFAULT 'open',  -- open/taken/done
    created_at      TEXT NOT NULL,
    taken_at        TEXT,
    done_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ho_status ON handover_queue(tenant_id, status, created_at DESC);

-- ★ 网页渠道的"发件箱"。
--   为什么网页渠道需要它，而企微/飞书不需要：
--   企微和飞书有"主动发消息 API"，AI 算完直接推给平台就行。
--   网页没有 —— 客户浏览器那边只能**自己来取**（轮询）。
--   所以每条要发给浏览器的消息先落这里，浏览器按 seq 拉增量。
--
--   ★ 用自增 seq 做游标，而不是时间戳：
--     同一秒内产生的两条消息，用时间戳会漏掉一条。
CREATE TABLE IF NOT EXISTS web_outbox (
    seq        INTEGER PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    content    TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'ai',   -- ai / system / human
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_out ON web_outbox(session_id, seq);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _LOCK, connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """给已存在的库补上后加的列。

    ★ 为什么不直接重建表：真实客户的库里有积累的问答日志和反馈，
      重建会把这些（最有价值的学习原料）全丢掉。
      `CREATE TABLE IF NOT EXISTS` 对已存在的表不会加新列，所以必须单独补。
    """
    adds = [
        ("style_example", "status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("synonym", "status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("qa_log", "would_be_decision", "TEXT"),
        ("qa_log", "intent", "TEXT"),
    ]
    for table, col, decl in adds:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    r = rows(sql, params)
    return r[0] if r else None


def run(sql: str, params: tuple = ()) -> int:
    """写操作。返回 lastrowid 或受影响行数。"""
    with _LOCK, connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if cur.lastrowid else cur.rowcount


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def jload(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


# ── 常用写入 ────────────────────────────────────────────────────────────

def log_qa(
    *,
    tenant_id: str = "default",
    channel: str = "web",
    conversation_id: str | None = None,
    user_ref: str | None = None,
    is_internal: bool = False,
    question_raw: str,
    question_rewrite: str | None = None,
    context: Any = None,
    retrieved: Any = None,
    quality: float | None = None,
    signals: Any = None,
    citations: Any = None,
    answer_text: str | None = None,
    confidence: float | None = None,
    decision: str | None = None,
    would_be_decision: str | None = None,
    intent: str | None = None,
    failure_reason: str | None = None,
    latency_ms: int | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost: float | None = None,
) -> int:
    """记一条问答。★ 每次回答都必须调这个 —— 它是学习闭环的唯一原料。"""
    return run(
        """INSERT INTO qa_log (
              tenant_id, channel, conversation_id, user_ref, is_internal,
              question_raw, question_rewrite, context_json,
              retrieved_json, quality, signals_json, citations_json,
              answer_text, confidence, decision, would_be_decision, intent,
              failure_reason,
              latency_ms, tokens_in, tokens_out, cost, created_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            tenant_id, channel, conversation_id, user_ref, 1 if is_internal else 0,
            question_raw, question_rewrite, jdump(context) if context is not None else None,
            jdump(retrieved) if retrieved is not None else None, quality,
            jdump(signals) if signals is not None else None,
            jdump(citations) if citations is not None else None,
            answer_text, confidence, decision, would_be_decision, intent,
            # ★ failure_reason 必须在**写入时**就带上，不能只靠事后 decide() 补。
            #   踩到的坑：「澄清过几次」这个计数查的是 failure_reason='clarified'，
            #   而 log_qa 不写这个字段、decide() 又只在最后才调 ——
            #   于是计数永远是 0 → 无限澄清，用户会被一直问「你想问什么」。
            failure_reason,
            latency_ms, tokens_in, tokens_out, cost, now(),
        ),
    )


def decide(
    qa_log_id: int,
    *,
    outcome: str,
    failure_reason: str | None = None,
    edited_to: str | None = None,
) -> None:
    """记录这条问答最终怎么了。★ 这是"变聪明"真正依赖的那一步。

    ★★ `failure_reason` 用 COALESCE 保底，**不覆盖已有的值**。
      踩到的坑：`log_qa` 写入时已经带了 `failure_reason='clarified'`，
      然后 `decide(qa_id, outcome='sent')`（不传 reason）把它**覆盖成 NULL** ——
      于是"澄清过几次"永远是 0，AI 会**无限反问**，用户被一直问"你想问什么"。

    ★ 语义上的道理：`decide` 记的是**结果**（sent/edited/unanswered），
      它不该顺手抹掉"为什么失败"这个已经确定的事实。
      没传新原因，就保留原来的。
    """
    run(
        """UPDATE qa_log
              SET outcome=?, failure_reason=COALESCE(?, failure_reason),
                  edited_to=COALESCE(?, edited_to), decided_at=?
            WHERE id=?""",
        (outcome, failure_reason, edited_to, now(), qa_log_id),
    )


def add_feedback(
    qa_log_id: int, *, source: str, kind: str, detail: Any = None
) -> int:
    return run(
        """INSERT INTO feedback (qa_log_id, source, kind, detail_json, created_at)
           VALUES (?,?,?,?,?)""",
        (qa_log_id, source, kind, jdump(detail) if detail is not None else None, now()),
    )
