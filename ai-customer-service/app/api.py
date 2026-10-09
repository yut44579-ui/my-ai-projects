"""HTTP 接口。

★ 设计原则：接口只做"翻译"，不写业务判断。
  所有判定（该不该转人工、质量够不够、能不能发）都在 pipeline/policy 里，
  接口层不重复实现 —— 否则同一件事写两遍必然对不上（另两个项目都踩过）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import classify, config, governance, llm, pipeline, policy, retrieval, store
from .channels.base import OutboundMessage
from .connectors import folder as folder_conn
from .learning import eval as learning_eval
from .learning import loop as learning

app = FastAPI(title="AI客服", version="0.1.0")

_WEB = Path(__file__).resolve().parents[1] / "web"


# ══════════════════════════════════════════════════════════════════════
# 请求模型
# ══════════════════════════════════════════════════════════════════════

class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    tenant_id: str = "default"
    channel: str = "web"
    is_internal: bool = False
    conversation_id: str | None = None
    context: list[str] | None = None
    tier: str | None = None
    use_llm: bool = True


class EditIn(BaseModel):
    final_text: str = Field(min_length=1, max_length=4000)


class SyncIn(BaseModel):
    path: str
    tenant_id: str = "default"
    default_permission: str = "external"


class SynonymIn(BaseModel):
    colloquial: str = Field(min_length=1, max_length=60)
    standard: str = Field(min_length=1, max_length=60)
    tenant_id: str = "default"


class TierIn(BaseModel):
    tier: str
    tenant_id: str = "default"


# ══════════════════════════════════════════════════════════════════════
# 健康 / 元信息
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_ready": llm.available(),
        "docs": (store.one("SELECT COUNT(*) n FROM doc") or {}).get("n", 0),
        "chunks": (store.one("SELECT COUNT(*) n FROM chunk") or {}).get("n", 0),
        "qa_logs": (store.one("SELECT COUNT(*) n FROM qa_log") or {}).get("n", 0),
    }


@app.get("/api/tiers")
def tiers() -> dict[str, Any]:
    """档位清单。★ 客户只选一个词，不调数字。"""
    return {
        "tiers": [
            {"key": "conservative", "name": "保守", "desc": "只有最稳的才自动发", "cover": "≈40%"},
            {"key": "standard", "name": "标准", "desc": "流程类自动，涉钱类起草", "cover": "≈65%"},
            {"key": "aggressive", "name": "激进", "desc": "能答就答，答不上转人工", "cover": "≈85%"},
        ],
        "thresholds": policy.TIERS,
    }


# ══════════════════════════════════════════════════════════════════════
# 问答
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/ask")
def ask(body: AskIn) -> dict[str, Any]:
    ans = pipeline.answer_question(
        body.question,
        tenant_id=body.tenant_id,
        channel=body.channel,
        is_internal=body.is_internal,
        conversation_id=body.conversation_id,
        context=body.context,
        tier=body.tier,
        use_llm=body.use_llm,
    )
    return {
        "qa_log_id": ans.qa_log_id,
        "text": ans.text,
        "decision": ans.decision,
        "quality": ans.quality,
        "citations": ans.citations,
        "intent": ans.intent,
        "reason": ans.reason,
        "problems": ans.problems,
        "degraded": ans.degraded,
        "rewrite": ans.rewrite,
        # ★ 把命中的原文也返回，界面上可以展开给运营核对
        "hits": [
            {"title": h.title, "score": h.score, "stale": h.stale, "text": h.text[:400]}
            for h in ans.hits
        ],
    }


# ══════════════════════════════════════════════════════════════════════
# 反馈 —— 学习闭环的数据入口
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/feedback/{qa_log_id}/edit")
def fb_edit(qa_log_id: int, body: EditIn) -> dict[str, str]:
    if not store.one("SELECT id FROM qa_log WHERE id=?", (qa_log_id,)):
        raise HTTPException(404, "没有这条问答记录")
    pipeline.operator_edit(qa_log_id, body.final_text)
    return {"ok": "已记录人工版本，并立刻变成风格示例"}


@app.post("/api/feedback/{qa_log_id}/accept")
def fb_accept(qa_log_id: int) -> dict[str, str]:
    pipeline.operator_accept(qa_log_id)
    return {"ok": "已记录：草稿可用"}


@app.post("/api/feedback/{qa_log_id}/reject")
def fb_reject(qa_log_id: int, why: str = "wrong_fact") -> dict[str, str]:
    pipeline.operator_reject(qa_log_id, why)
    return {"ok": "已记录：这条草稿没用"}


# ══════════════════════════════════════════════════════════════════════
# 学习看板
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/learning/report")
def learning_report(tenant_id: str = "default", days: int = 30) -> dict[str, Any]:
    b = learning.analyze(tenant_id, days)
    return {
        "total": b.total,
        "ok": b.answered_ok,
        "rate": round(b.hit_rate, 4),
        "by_outcome": b.by_outcome,
        "by_decision": b.by_decision,
        # ★ 失败原因 + 对应的修法，这是"该修哪里"的答案
        "by_reason": [
            {
                "key": k,
                "label": learning.FAILURE_LABELS.get(k, k),
                "count": v,
                "fix": learning.FIX_BY_REASON.get(k, ""),
            }
            for k, v in sorted(b.by_reason.items(), key=lambda x: -x[1])
        ],
    }


@app.get("/api/learning/gaps")
def learning_gaps(tenant_id: str = "default", limit: int = 30) -> dict[str, Any]:
    """★ 待补充问题清单 —— 要直接推给管理员的那份。

    这是产品"越用越好"的飞轮入口：没有它，覆盖率永远停在原地。
    """
    return {"items": learning.open_gaps(tenant_id, limit)}


@app.post("/api/learning/gaps/{gap_id}/resolve")
def gap_resolve(gap_id: int, status: str = "added") -> dict[str, str]:
    learning.resolve_gap(gap_id, status=status)
    return {"ok": "已标记"}


@app.get("/api/learning/styles")
def learning_styles(tenant_id: str = "default", limit: int = 50) -> dict[str, Any]:
    return {"items": learning.style_examples(tenant_id, limit)}


@app.get("/api/learning/synonyms")
def learning_synonyms(tenant_id: str = "default") -> dict[str, Any]:
    return {"items": learning.synonyms(tenant_id)}


@app.post("/api/learning/synonyms")
def add_synonym(body: SynonymIn) -> dict[str, str]:
    learning.add_synonym(body.tenant_id, body.colloquial, body.standard)
    return {"ok": f"已记下：{body.colloquial} → {body.standard}"}


@app.post("/api/learning/tier")
def set_tier(body: TierIn) -> dict[str, Any]:
    if body.tier not in policy.TIERS:
        raise HTTPException(400, f"档位只能是 {list(policy.TIERS)}")
    store.run(
        """INSERT INTO tenant_config (tenant_id, tier, updated_at) VALUES (?,?,?)
           ON CONFLICT(tenant_id) DO UPDATE SET tier=excluded.tier, updated_at=excluded.updated_at""",
        (body.tenant_id, body.tier, store.now()),
    )
    return {"ok": f"已切到「{body.tier}」档", "thresholds": policy.tier_thresholds(body.tier)}


@app.get("/api/learning/tier/current")
def current_tier(tenant_id: str = "default") -> dict[str, Any]:
    row = store.one("SELECT tier FROM tenant_config WHERE tenant_id=?", (tenant_id,))
    tier = (row or {}).get("tier") or "standard"
    return {"tier": tier, "thresholds": policy.tier_thresholds(tier)}


@app.get("/api/learning/qs")
def recent_qa(tenant_id: str = "default", limit: int = 40) -> dict[str, Any]:
    """最近的问答记录（含失败原因），供人工复盘。"""
    return {
        "items": store.rows(
            """SELECT id, question_raw, question_rewrite, answer_text, quality,
                      decision, outcome, failure_reason, edited_to, created_at
                 FROM qa_log WHERE tenant_id=?
                ORDER BY id DESC LIMIT ?""",
            (tenant_id, limit),
        )
    }


# ══════════════════════════════════════════════════════════════════════
# ★★ 评测关卡 —— "越用越聪明"不变成"越用越偏"的保障
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/learning/pending")
def learning_pending(tenant_id: str = "default") -> dict[str, Any]:
    """有多少学到的还没验证生效。★ 界面要显示它，
    否则管理员不知道"系统学了东西但还没用上"。"""
    return learning.pending_count(tenant_id)


@app.post("/api/learning/eval/seed")
def eval_seed(tenant_id: str = "default", limit: int = 50) -> dict[str, Any]:
    """从问答日志里自动生成评测用例。

    ★ 没有这步，关卡就是空的 —— 没人会主动手写评测用例，
      而"每次学习都验证"这句话就落不了地。
    """
    r = learning_eval.seed_from_log(tenant_id, limit)
    total = store.one("SELECT COUNT(*) n FROM eval_case WHERE tenant_id=?", (tenant_id,))
    return {**r, "total_cases": (total or {}).get("n", 0)}


@app.get("/api/learning/eval/cases")
def eval_cases(tenant_id: str = "default") -> dict[str, Any]:
    return {
        "items": [
            {
                "id": c["id"],
                "question": c["question"],
                "must_contain": store.jload(c["must_contain"], []),
                "must_not_contain": store.jload(c["must_not_contain"], []),
                "expect_decision": c["expect_decision"],
                "note": c["note"],
            }
            for c in learning_eval.cases(tenant_id)
        ]
    }


class EvalCaseIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    must_contain: list[str] = []
    must_not_contain: list[str] = []
    expect_decision: str | None = None
    note: str = ""
    tenant_id: str = "default"


@app.post("/api/learning/eval/cases")
def eval_add_case(body: EvalCaseIn) -> dict[str, str]:
    learning_eval.add_case(
        body.tenant_id,
        body.question,
        must_contain=body.must_contain,
        must_not_contain=body.must_not_contain,
        expect_decision=body.expect_decision,
        note=body.note,
    )
    return {"ok": "已加入评测集"}


@app.post("/api/learning/eval/run")
def eval_run(tenant_id: str = "default", use_llm: bool = True) -> dict[str, Any]:
    out = learning_eval.run(tenant_id, label="手动跑一遍", use_llm=use_llm)
    return {
        "total": out.total,
        "passed": out.passed,
        "failed": out.failed,
        "score": round(out.score, 4),
        "note": out.note,
        "summary": out.summary(),
        "failures": out.failures(10),
    }


@app.post("/api/learning/eval/promote")
def eval_promote(tenant_id: str = "default", use_llm: bool = True) -> dict[str, Any]:
    """★ 关卡入口：把攒着的学习产物送进闸门，通过了才生效。"""
    return learning_eval.promote_pending(tenant_id, use_llm=use_llm)


@app.get("/api/learning/eval/history")
def eval_history(tenant_id: str = "default", limit: int = 20) -> dict[str, Any]:
    """评测历史 —— 回答"哪次学习有效、哪次无效"。"""
    return {"items": learning_eval.history(tenant_id, limit)}


@app.get("/api/learning/rejected")
def learning_rejected(tenant_id: str = "default") -> dict[str, Any]:
    """被闸门拦下的学习产物。★ 看不到它，管理员会以为系统什么都没学到。"""
    return learning_eval.rejected_items(tenant_id)


# ══════════════════════════════════════════════════════════════════════
# ★★ 设置：模型配置（★ 用户反馈"控制台模型也弄不了"）
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/settings/model")
def model_config() -> dict[str, Any]:
    """读模型配置。★ 密钥只回显遮罩后的，完整 key 永不回传。"""
    from . import settings as S

    return {
        "config": S.get_model_config(mask=True),
        "fields": [
            {"key": k, "env": e, "desc": d, "secret": k == "api_key"}
            for k, e, d in S.MODEL_FIELDS
        ],
    }


class ModelIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    model_cheap: str | None = None


@app.post("/api/settings/model")
def model_save(body: ModelIn) -> dict[str, Any]:
    """保存模型配置。★ 立即生效，不用重启。

    ★ 密钥的规则：界面上显示的是遮罩串（sk-eee***01），
      用户没动它就原样提交 —— 我们**不能把遮罩串当新 key 存进去**，
      那服务立刻就不能用了。所以带 `*` 的一律视为"不改"。
    """
    from . import llm, settings as S

    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    r = S.set_model_config(patch)
    llm.reset_client()
    return {**r, "config": S.get_model_config(mask=True)}


@app.post("/api/settings/model/test")
def model_test() -> dict[str, Any]:
    """真调一次模型验证配置。★ 必须真调 ——
    只检查"字段填了没有"发现不了 key 错、地址错、模型名错。"""
    from . import settings as S

    return S.test_model()


# ══════════════════════════════════════════════════════════════════════
# ★★ 知识库导入（★ 用户反馈"知识库也不知道怎么导入"）
# ══════════════════════════════════════════════════════════════════════

class PasteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200000)
    permission: str = "external"
    tenant_id: str = "default"


@app.post("/api/kb/paste")
def kb_paste(body: PasteIn) -> dict[str, Any]:
    """直接粘贴一段文字作为一篇文档。

    ★ 这是最"土"但最常用的导入方式：
      客户手里有一份制度，直接复制粘贴进来就能用，
      不用先存成文件再上传。
    """
    from . import retrieval

    doc_id = f"paste:{body.title[:60]}"
    r = retrieval.upsert_document(
        doc_id=doc_id, tenant_id=body.tenant_id, source="paste",
        source_ref=body.title, title=body.title, content=body.content,
        permission=body.permission,
    )
    return {"ok": True, "doc_id": doc_id, "chunks": r["chunks"]}


@app.post("/api/kb/upload")
async def kb_upload(
    request: Request,
    tenant_id: str = "default",
    permission: str = "external",
) -> dict[str, Any]:
    """上传文件（支持一次多个）。

    ★ 走 multipart 表单。支持 .txt/.md/.docx/.pdf/.xlsx/.csv。
      ★ 解析不了的文件**如实跳过并说明原因**，不假装读过。
    """
    from .connectors.folder import FolderConnector
    from . import retrieval

    form = await request.form()
    saved_dir = config.DATA_DIR / "uploads"
    saved_dir.mkdir(parents=True, exist_ok=True)

    added, skipped, docs = 0, [], []
    for field in form.getlist("files"):
        name = getattr(field, "filename", None)
        if not name:
            continue
        ext = Path(name).suffix.lower()
        if ext not in (".txt", ".md", ".markdown", ".csv", ".docx", ".pdf", ".xlsx", ".xls"):
            skipped.append(f"{name}（不支持的类型 {ext}）")
            continue
        target = saved_dir / name
        target.write_bytes(await field.read())
        # 借用文件夹连接器的解析能力（它已经处理了所有格式和编码）
        conn = FolderConnector(saved_dir)
        nd = conn.fetch(name)
        if nd is None or not (nd.content or "").strip():
            skipped.append(f"{name}（解析不出内容）")
            continue
        r = retrieval.upsert_document(
            doc_id=f"upload:{name}", tenant_id=tenant_id, source="upload",
            source_ref=name, title=nd.title or name, content=nd.content,
            updated_at=nd.updated_at, permission=permission,
        )
        added += 1
        docs.append({"name": name, "title": nd.title or name, "chunks": r["chunks"]})
    return {"added": added, "documents": docs, "skipped": skipped}


@app.get("/api/kb/docs")
def kb_docs(tenant_id: str = "default") -> dict[str, Any]:
    """文档清单（★ 界面上要能看见"我导进来了什么"）。"""
    return {
        "items": store.rows(
            """SELECT d.id, d.title, d.source, d.source_ref, d.permission,
                      d.updated_at, d.synced_at,
                      (SELECT COUNT(*) FROM chunk c WHERE c.doc_id = d.id) chunks
                 FROM doc d WHERE d.tenant_id=? ORDER BY d.synced_at DESC LIMIT 300""",
            (tenant_id,),
        )
    }


@app.delete("/api/kb/docs")
def kb_delete(doc_id: str) -> dict[str, str]:
    """删一篇文档。★ 真的删（含所有分块）——
    只标记删除的话，旧内容会永远留在索引里继续被回答。"""
    from . import retrieval

    retrieval.delete_document(doc_id)
    return {"ok": "已删除"}


# ══════════════════════════════════════════════════════════════════════
# ★★ 治理：急停 / 影子模式 / 后果预览
#    这三样是"客户敢不敢开自动"的决定因素
# ══════════════════════════════════════════════════════════════════════

class StopIn(BaseModel):
    on: bool
    by: str = "管理员"
    reason: str = ""
    tenant_id: str = "default"


@app.get("/api/gov/status")
def gov_status(tenant_id: str = "default") -> dict[str, Any]:
    return {
        "stopped": governance.is_stopped(tenant_id),
        "shadow": governance.is_shadow(tenant_id),
        "tier": governance._tier(tenant_id),
    }


@app.post("/api/gov/stop")
def gov_stop(body: StopIn) -> dict[str, Any]:
    """★ 急停。客户敢开全自动的前提，就是有这么一个随时能按的按钮。"""
    return governance.set_stop(body.tenant_id, on=body.on, by=body.by, reason=body.reason)


@app.get("/api/gov/stop/history")
def gov_stop_history(tenant_id: str = "default") -> dict[str, Any]:
    """急停留痕。★ 事后复盘时，"谁在什么时候按的、当时以为什么"
    是唯一能还原现场的东西。"""
    return {"items": governance.stop_history(tenant_id)}


class ShadowIn(BaseModel):
    on: bool
    tenant_id: str = "default"


@app.post("/api/gov/shadow")
def gov_shadow(body: ShadowIn) -> dict[str, Any]:
    return governance.set_shadow(body.tenant_id, on=body.on)


@app.get("/api/gov/shadow/report")
def gov_shadow_report(tenant_id: str = "default", days: int = 30) -> dict[str, Any]:
    """★ 影子模式对照报告 —— 拿数据让客户自己说"你开吧"。"""
    rep = governance.shadow_report(tenant_id, days)
    return {
        "total": rep.total,
        "with_human": rep.with_human,
        "fact_match": rep.fact_match,
        "would_auto": rep.would_auto,
        "match_rate": round(rep.match_rate, 4),
        "coverage": round(rep.coverage, 4),
        "summary": rep.line(),
        "examples": rep.examples,
    }


@app.get("/api/gov/preview")
def gov_preview(
    tenant_id: str = "default",
    tier: str | None = None,
    auto_threshold: float | None = None,
    draft_threshold: float | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """★ 后果预览：换一套门槛会怎样。

    ★★ 零成本、瞬时 —— **不调模型**。
      因为每次问答的检索质量分已经存在 qa_log 里了，
      重放只是把历史分数按新门槛重新分一次类。
      所以界面上可以做到"拖滑块实时看数字"。
    """
    if tier:
        return {"compare": governance.compare_preview(tenant_id, tier=tier, days=days)}
    p = governance.preview(
        tenant_id, auto_threshold=auto_threshold, draft_threshold=draft_threshold, days=days
    )
    return {"now": p.as_dict(), "basis": f"按过去 {days} 天的 {p.total} 条真实问答推算"}


class SyncUrlIn(BaseModel):
    urls: list[str] = Field(min_length=1)
    tenant_id: str = "default"
    default_permission: str = "external"


@app.post("/api/kb/sync-urls")
def kb_sync_urls(body: SyncUrlIn) -> dict[str, Any]:
    """从一组网页接入知识。

    ★ 客户最常有的"公开资料"就是官网和公众号文章 ——
      没人会去整理成文档，但它们回答了客户最常问的问题。
    """
    from .connectors.urls import UrlConnector

    conn = UrlConnector(body.urls, default_permission=body.default_permission)
    res = folder_conn.sync(conn, tenant_id=body.tenant_id)
    rep = classify.audit(body.tenant_id)
    return {
        "added": res.added, "updated": res.updated, "unchanged": res.unchanged,
        "deleted": res.deleted,
        # ★ 抓失败的如实列出来，不假装成功
        "skipped": res.skipped,
        "audit": {
            "total": rep["total"], "severe_count": rep["severe_count"],
            "advice": classify.advice(),
            "risky": [
                {"title": r["title"], "permission": r["permission"], "severe": r["severe"],
                 "flags": [f.line() for f in r["flags"]]}
                for r in rep["risky"][:20]
            ],
        },
    }


class ProbeApiIn(BaseModel):
    base_url: str
    headers: dict[str, str] = {}


@app.post("/api/kb/probe-api")
def kb_probe_api(body: ProbeApiIn) -> dict[str, Any]:
    """接客户内部接口之前的体检。

    ★ 这个接口的价值是**把"接不上"的原因说清楚**：
      客户 IT 最常犯的错是返回 `{"data":[...]}` 而不是 `{"items":[...]}`。
      直接报"解析失败"他会一头雾水；告诉他"期望 items、实际是 data"
      他五分钟就能改好。
    """
    from .connectors.api import probe

    return probe(body.base_url, body.headers)


class SyncApiIn(BaseModel):
    base_url: str
    headers: dict[str, str] = {}
    tenant_id: str = "default"
    default_permission: str = "external"
    items_path: str = "items"
    max_items: int = 5000


@app.post("/api/kb/sync-api")
def kb_sync_api(body: SyncApiIn) -> dict[str, Any]:
    """从客户自研系统（CRM/ERP/工单）接入知识。"""
    from .connectors.api import ApiConnector

    conn = ApiConnector(
        body.base_url,
        headers=body.headers,
        items_path=body.items_path,
        default_permission=body.default_permission,
        max_items=body.max_items,
    )
    res = folder_conn.sync(conn, tenant_id=body.tenant_id)
    return {
        "added": res.added, "updated": res.updated, "unchanged": res.unchanged,
        "deleted": res.deleted,
        # ★ 接口的错误原因要带出来，否则用户不知道是"没数据"还是"接错了"
        "connector_error": conn.last_error,
        "describe": conn.describe(),
    }


# ══════════════════════════════════════════════════════════════════════
# ★★ 网页客服（网站右下角的挂件）
# ══════════════════════════════════════════════════════════════════════

class WebChatIn(BaseModel):
    session: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    tenant_id: str = "default"


@app.post("/api/web/chat")
def web_chat(body: WebChatIn) -> dict[str, Any]:
    """网页访客发一句话。

    ★ 立刻返回 —— 生成在后台，回复靠轮询取（见 /api/web/poll 的说明）。
      这里同步等模型的话，用户按下回车要等 3~15 秒才有任何反应。
    """
    from .channels import dispatch
    from .channels.web import WebChannel

    ch = WebChannel(tenant_id=body.tenant_id)
    msg = ch.parse({"text": body.text, "session": body.session, "tenant_id": body.tenant_id})
    if msg is None:
        raise HTTPException(400, "消息为空")
    res = dispatch.accept(msg, ch)
    return {"accepted": res.accepted, "duplicate": res.duplicate, "note": res.line()}


@app.get("/api/web/poll")
def web_poll(session: str, since: int = 0) -> dict[str, Any]:
    """浏览器拉增量回复。

    ★ 为什么用轮询而不是 WebSocket：
      · 很多 SME 的网站挂在虚拟主机/共享空间上，**不支持长连接**
      · 轮询可以走普通 HTTP，任何环境都能用
      · 客服场景的消息量很小（一个人几十秒一条），轮询的开销可以忽略

     代价是有最多 1~2 秒的延迟 —— 对客服场景完全可以接受。
    """
    from .channels.web import WebChannel

    return WebChannel.poll(session, since)


@app.post("/api/web/rate-limit-check")
def web_rate_limit(session: str) -> dict[str, Any]:
    """网页渠道的**限流检查**。

    ★ 必须单独说明这件事：
      网页挂件是公开的，任何人打开你的网站都能用。
      真正要防的不是"伪造请求"（那是企微/飞书要考虑的），
      而是**同一个人疯狂刷** —— 刷一次就是一次真金白银的模型调用。

      所以这个渠道的安全边界在**频率限制**上，不在签名上。
    """
    from . import governance

    stop = governance.is_stopped("default")
    # 最近 1 分钟这个会话发了几条
    n = (store.one(
        """SELECT COUNT(*) n FROM inbound_log
            WHERE platform='web' AND user_id=?
              AND created_at >= datetime('now','-1 minutes')""",
        (session,),
    ) or {}).get("n", 0)
    limit = 12
    return {
        "stopped": stop,
        "sent_last_minute": n,
        "limit": limit,
        "allowed": (not stop) and n < limit,
        "note": "超了会自动转人工而不是丢弃 —— 用户的消息永远不会丢。",
    }


# ══════════════════════════════════════════════════════════════════════
# ★★ 问题上报（客服/销售 → 技术）
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/issues/meta")
def issues_meta() -> dict[str, Any]:
    """表单要用的选项 —— ★ 全部是小白能懂的词，不是技术术语。"""
    from . import issues as I

    return {
        "kinds": [{"key": k, "label": l, "hint": h} for k, l, h in I.ISSUE_KINDS],
        "severities": [{"key": k, "label": l, "hint": h} for k, l, h in I.SEVERITIES],
        "resolution_kinds": [{"key": k, "label": l} for k, l in I.RESOLUTION_KINDS],
    }


class IssueIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str = "other"
    detail: str = ""
    severity: str = "normal"
    reporter: str = ""
    reporter_role: str = ""
    # ★ 这两个是**可选**的：如果用户是从某个具体问答点过来的，
    #   前端会把 qa_log_id 带上，服务端自动抓现场（他一个字都不用填）
    qa_log_id: int | None = None
    user_ref: str | None = None
    page: str = ""
    tenant_id: str = "default"


@app.post("/api/issues")
def issue_create(body: IssueIn) -> dict[str, Any]:
    """报一个问题。

    ★★ 这个接口的设计原则：**报的人不需要懂技术。**

      必填只有两个：哪儿不对（kind）+ 一句话（title）。
      剩下的现场（哪个会话、哪句问答、AI 怎么判的、检索质量、
      命中了什么、最近的报错）**全部由服务端自动抓**。

      ★ 如果表单让他填"复现步骤""错误堆栈"，结果只有一个：
        **他不会填，于是这个问题永远不被上报** —— 然后他私聊技术，或者不管了。
    """
    from . import issues as I

    return I.create(
        title=body.title, kind=body.kind, detail=body.detail, severity=body.severity,
        reporter=body.reporter, reporter_role=body.reporter_role,
        qa_log_id=body.qa_log_id, user_ref=body.user_ref, page=body.page,
        tenant_id=body.tenant_id,
    )


@app.get("/api/issues")
def issue_list(
    tenant_id: str = "default", status: str = "", mine: str = "", limit: int = 100
) -> dict[str, Any]:
    """列表。★ 技术看全部；报的人传 mine=自己 就只看自己报的。"""
    from . import issues as I

    return I.listing(tenant_id=tenant_id, status=status, mine=mine, limit=limit)


@app.get("/api/issues/{issue_id}")
def issue_detail(issue_id: int) -> dict[str, Any]:
    from . import issues as I

    d = I.detail(issue_id)
    if not d:
        raise HTTPException(404, "没有这条问题")
    return d


class IssueTakeIn(BaseModel):
    by: str = "技术"


@app.post("/api/issues/{issue_id}/take")
def issue_take(issue_id: int, body: IssueTakeIn) -> dict[str, Any]:
    from . import issues as I

    return I.take(issue_id, by=body.by)


class IssueResolveIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=2000)
    kind: str = ""
    by: str = "技术"


@app.post("/api/issues/{issue_id}/resolve")
def issue_resolve(issue_id: int, body: IssueResolveIn) -> dict[str, Any]:
    """技术提交解决方案。

    ★ 注意：提交后状态是 `resolved`（已解决，等确认），**不是 closed**。
      必须等报问题的人点"好了"才算关闭。
    """
    from . import issues as I

    return I.resolve(issue_id, resolution=body.resolution, kind=body.kind, by=body.by)


class IssueAckIn(BaseModel):
    ok: bool
    note: str = ""
    by: str = ""


@app.post("/api/issues/{issue_id}/ack")
def issue_ack(issue_id: int, body: IssueAckIn) -> dict[str, Any]:
    """报的人确认。

    ★ 说"还没好"就**打回 working**，并带上他的补充说明 ——
      这是闭环里最有价值的一步：很多时候技术改的和他遇到的不是一回事。
    """
    from . import issues as I

    return I.ack(issue_id, ok=body.ok, note=body.note, by=body.by)


class IssueRejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    by: str = "技术"


@app.post("/api/issues/{issue_id}/reject")
def issue_reject(issue_id: int, body: IssueRejectIn) -> dict[str, Any]:
    """技术判定"不是问题" —— ★ 也必须说理由，否则报的人下次就不报了。"""
    from . import issues as I

    return I.reject(issue_id, reason=body.reason, by=body.by)


# ══════════════════════════════════════════════════════════════════════
# ★★ 实时监控（★ 用户：要能在另一个页面观察 AI 到底做了什么）
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/live")
def live(tenant_id: str = "default", minutes: int = 30, limit: int = 40) -> dict[str, Any]:
    """当前活跃会话 + 每个会话正在发生什么。

    ★★ 为什么要单独做这个（而不是让人去人工队列里翻）：

      用户的原话：
        「要实时同步用户的 id 以及聊天记录，也就是要能随时知道用户在说什么，
          避免不知道导致损失」
        「我在官网进入控制台或者人工那些，是另起一页观察，
          这样才会测试聊天记录，以及更为准确的知道 AI 做了什么」

      ★ 人工队列只显示**已经转人工**的那部分。
        但真正需要被看见的，往往还包括**AI 正在自动回答的那些** ——
        因为"出事"通常不是转人工时出的，而是 AI 自动发出去的那一句。
    """
    since = f"-{max(1, min(minutes, 1440))} minutes"
    rows = store.rows(
        """SELECT user_ref, channel, COUNT(*) turns, MAX(created_at) last_at,
                  SUM(CASE WHEN decision='escalate' THEN 1 ELSE 0 END) escalated,
                  SUM(CASE WHEN decision='auto'     THEN 1 ELSE 0 END) autoed,
                  SUM(CASE WHEN decision='draft'    THEN 1 ELSE 0 END) drafted
             FROM qa_log
            WHERE tenant_id=? AND created_at >= datetime('now', ?)
              AND user_ref IS NOT NULL AND user_ref <> ''
            GROUP BY user_ref, channel
            ORDER BY last_at DESC LIMIT ?""",
        (tenant_id, since, limit),
    )

    sessions = []
    for r in rows:
        uid, ch = r["user_ref"], r["channel"]
        recent = store.rows(
            """SELECT id, question_raw, answer_text, decision, quality,
                      failure_reason, intent, created_at
                 FROM qa_log WHERE tenant_id=? AND user_ref=? AND channel=?
                ORDER BY id DESC LIMIT 5""",
            (tenant_id, uid, ch),
        )
        recent.reverse()
        ho = store.one(
            """SELECT COUNT(*) n FROM handover_queue
                WHERE tenant_id=? AND user_id=? AND platform=? AND status='open'""",
            (tenant_id, uid, ch),
        ) or {}
        waiting = ho.get("n", 0)
        last = recent[-1] if recent else {}
        status = ("waiting_human" if waiting
                  else "just_escalated" if last.get("decision") == "escalate"
                  else "ai_handling")
        sessions.append({
            "user_id": uid, "platform": ch, "last_at": r["last_at"],
            "turns": r["turns"], "escalated": r["escalated"],
            "auto": r["autoed"], "draft": r["drafted"],
            "waiting": waiting, "status": status,
            "last_question": last.get("question_raw", ""),
            "last_decision": last.get("decision", ""),
            "last_quality": last.get("quality"),
            "recent": [
                {"qa_id": x["id"], "q": x["question_raw"], "a": (x["answer_text"] or "")[:170],
                 "decision": x["decision"], "quality": x["quality"],
                 "why": x["failure_reason"] or "", "at": x["created_at"]}
                for x in recent
            ],
        })

    tot = store.one(
        """SELECT COUNT(*) n,
                  SUM(CASE WHEN decision='auto' THEN 1 ELSE 0 END) a,
                  SUM(CASE WHEN decision='escalate' THEN 1 ELSE 0 END) e
             FROM qa_log WHERE tenant_id=? AND created_at >= datetime('now', ?)""",
        (tenant_id, since),
    ) or {}
    ho_open = store.one(
        "SELECT COUNT(*) n FROM handover_queue WHERE tenant_id=? AND status='open'", (tenant_id,)
    ) or {}

    return {
        "window_minutes": minutes,
        "sessions": sessions,
        "stats": {
            "active_sessions": len(sessions),
            "questions": tot.get("n") or 0,
            "auto": tot.get("a") or 0,
            "escalated": tot.get("e") or 0,
            "waiting_human": ho_open.get("n") or 0,
        },
        "hint": "★ 这里包含 AI **自动回答**的那些，不只是转人工的 —— "
                "出事通常不是转人工时出的，而是自动发出去的那一句。",
    }


# ══════════════════════════════════════════════════════════════════════
# ★★ 渠道接入（★ 用户反馈"接入微信或者飞书的途径没有看到"）
# ══════════════════════════════════════════════════════════════════════

# 每个渠道要填什么、去哪儿拿
CHANNEL_SPECS = [
    {
        "key": "web", "name": "网页挂件", "status": "ready",
        "why": "不需要任何平台账号。把一行 script 贴到自己网站上就能用。",
        "fields": [], "steps": [
            "把下面的代码贴到官网的 HTML 里（放在 </body> 之前）",
            "刷新页面，右下角会出现客服按钮",
        ],
        "snippet": '<script src="https://你的域名/aikefu.js"></script>',
        "callback": None,
    },
    {
        "key": "feishu", "name": "飞书", "status": "configurable",
        "why": "★ 中小企业首选：飞书支持长连接，不需要公网地址、不需要证书。",
        "fields": [
            ("app_id", "App ID", False),
            ("app_secret", "App Secret", True),
            ("verification_token", "Verification Token", True),
            ("encrypt_key", "Encrypt Key（可选）", True),
        ],
        "steps": [
            "打开 open.feishu.cn → 开发者后台 → 创建「企业自建应用」",
            "在「凭证与基础信息」里拿到 App ID 和 App Secret",
            "在「事件与回调」里拿到 Verification Token",
            "把下面的回调地址填进「请求网址」",
            "订阅事件：接收消息 im.message.receive_v1",
            "在「权限管理」里开：im:message、im:message:send_as_bot",
        ],
        "callback": "/api/channels/feishu/webhook",
    },
    {
        "key": "wecom", "name": "企业微信", "status": "configurable",
        "why": "★ 注意：企微回调必须公网 HTTPS（不能用 IP:端口、不能自签证书）。"
               "服务器在内网的话接不了，需要配公网入口或用内网穿透。",
        "fields": [
            ("corp_id", "企业 ID（CorpID）", False),
            ("agent_id", "应用 AgentId", False),
            ("secret", "应用 Secret", True),
            ("token", "Token", True),
            ("aes_key", "EncodingAESKey（43 位）", True),
            ("internal_users", "内部员工名单（逗号分隔，可留空）", False),
        ],
        "steps": [
            "企业微信管理后台 → 应用管理 → 自建 → 创建应用",
            "在应用详情里拿到 AgentId 和 Secret",
            "在「企业信息」里拿到 CorpID",
            "在应用「接收消息」里点「随机获取」Token 和 EncodingAESKey",
            "把下面的回调地址填进「URL」并点保存（企微会立刻来验证）",
            "★ 员工名单留空的话，**所有人都按外部客户处理**（保守，不会漏内部信息）",
        ],
        "callback": "/api/channels/wecom/webhook",
    },
    {
        "key": "dingtalk", "name": "钉钉", "status": "configurable",
        "why": "机器人和企微类似，需要公网回调地址。",
        "fields": [
            ("client_id", "AppKey", False),
            ("client_secret", "AppSecret", True),
            ("robot_code", "机器人编码", False),
        ],
        "steps": [
            "open-dev.dingtalk.com → 创建企业内部应用",
            "拿到 AppKey 和 AppSecret",
            "在「机器人」里创建机器人",
            "★ 钉钉的适配器还没实现（只有配置位）—— "
            "接的话按飞书那套改，事件格式不同",
        ],
        "callback": "/api/channels/dingtalk/webhook",
    },
]


@app.get("/api/channels/config")
def channels_config(public_base: str = "") -> dict[str, Any]:
    """渠道配置状态 + 回调地址。

    ★ 为什么要给"回调地址"：客户在平台后台要填这个，
      而自己拼最容易拼错（路径、scheme、端口）。
      直接给他完整的一条，复制粘贴就行。
    """
    out = []
    for spec in CHANNEL_SPECS:
        vals = {}
        for key, _label, secret in spec["fields"]:
            v = store.one("SELECT value FROM setting WHERE key=?", (f"channel.{spec['key']}.{key}",))
            raw = (v or {}).get("value") or ""
            if secret:
                from . import settings as S

                vals[key] = S.mask_key(raw) if raw else ""
                vals[f"{key}_set"] = bool(raw)
            else:
                vals[key] = raw
        configured = all(
            True if secret and vals.get(f"{key}_set") else bool(vals.get(key) or secret)
            for key, _l, secret in spec["fields"]
        ) if spec["fields"] else True
        cb = spec["callback"]
        out.append({
            **{k: v for k, v in spec.items() if k != "fields"},
            "fields": [{"key": k, "label": l, "secret": s} for k, l, s in spec["fields"]],
            "values": vals,
            "configured": configured if spec["fields"] else True,
            "callback_url": (public_base.rstrip("/") + cb) if (public_base and cb) else cb,
        })
    return {"channels": out}


class ChannelIn(BaseModel):
    key: str
    values: dict[str, str] = {}


@app.post("/api/channels/config")
def channels_save(body: ChannelIn) -> dict[str, Any]:
    """保存某个渠道的配置。

    ★ 密钥类字段的规则和模型配置一样：
      界面上显示的是遮罩串，带 `*` 的一律视为"不改"。
      否则用户没动它一保存，就把星号当新密钥存进去了。
    """
    from . import settings as S

    spec = next((c for c in CHANNEL_SPECS if c["key"] == body.key), None)
    if not spec:
        raise HTTPException(400, f"没有这个渠道：{body.key}")
    changed = []
    for key, _label, secret in spec["fields"]:
        if key not in body.values:
            continue
        v = (body.values.get(key) or "").strip()
        if secret and (not v or "*" in v):
            continue
        S.put(f"channel.{body.key}.{key}", v)
        changed.append(key)
    return {"changed": changed, "note": "配置已保存。企微/飞书需要重启服务才生效（适配器在启动时读配置）。"}


def _channel_for(platform: str):
    """按平台取适配器配置。

    ★ 从环境变量读，不写死在代码里 ——
      凭据进代码库是最常见的密钥泄露方式（另两个项目都专门防过这条）。
    """
    import os

    if platform == "feishu":
        from .channels.feishu import FeishuChannel

        ids = (os.environ.get("FEISHU_INTERNAL_OPEN_IDS") or "").strip()
        return FeishuChannel(
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
            encrypt_key=os.environ.get("FEISHU_ENCRYPT_KEY", ""),
            internal_open_ids=set(x for x in ids.split(",") if x),
        )
    if platform == "wecom":
        from .channels.wecom import WeComChannel

        ids = (os.environ.get("WECOM_INTERNAL_USERIDS") or "").strip()
        return WeComChannel(
            corp_id=os.environ.get("WECOM_CORP_ID", ""),
            agent_id=os.environ.get("WECOM_AGENT_ID", ""),
            secret=os.environ.get("WECOM_SECRET", ""),
            token=os.environ.get("WECOM_TOKEN", ""),
            encoding_aes_key=os.environ.get("WECOM_AES_KEY", ""),
            internal_userids=set(x for x in ids.split(",") if x),
        )
    if platform == "sim":
        from .channels.sim import SimChannel

        return SimChannel()
    if platform == "web":
        # ★★ 网页渠道曾经漏在这里 —— 于是**人工在工作台回复网页访客会失败**，
        #   报一个"不认识的渠道：web"。
        #   这个 bug 很隐蔽：接管是成功的、状态也变成"已接管"，
        #   只有"发出去"这一步悄悄失败了 —— 客服以为回了，客户什么都没收到。
        #   ★ 教训：适配器的注册是**并列的一串 if**，
        #     加新渠道时最容易漏的就是这一处，而且它只在"那个渠道要主动发消息"时才暴露。
        from .channels.web import WebChannel

        return WebChannel()
    if platform == "dingtalk":
        # ★ 钉钉适配器还没实现 —— **如实报错**，不假装成功。
        #   假装成功的话，客服会以为消息发出去了。
        raise HTTPException(
            501, "钉钉适配器还没实现（只有配置界面）。接的话按飞书那套改，事件格式不同。"
        )
    raise HTTPException(404, f"不认识的渠道：{platform}")


@app.get("/api/channels/stats")
def channels_stats() -> dict[str, Any]:
    from .channels import dispatch

    return {
        **dispatch.stats(),
        "errors": dispatch.errors(10),
        "recent_inbound": store.rows(
            """SELECT platform, user_id, substr(content,1,60) content, status, created_at
                 FROM inbound_log ORDER BY id DESC LIMIT 20"""
        ),
    }


def _handle_webhook(platform: str, payload: dict[str, Any], headers: dict[str, str]):
    """把平台回调收进来。★ 必须**尽快返回**（企微 5 秒窗口）。

    所以这里只做三件事：验签 → 解析 → 丢进队列，然后立刻回。
    模型生成放在后台（见 channels/dispatch.py）。
    """
    from .channels import dispatch

    try:
        ch = _channel_for(platform)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"渠道配置有问题：{exc}") from exc

    # ★ 验签。不验的话任何人都能伪造消息让 AI 回复，还会烧模型额度。
    if not ch.verify(payload, headers):
        store.run(
            """INSERT INTO dispatch_error (tenant_id, dedup_key, error, created_at)
               VALUES (?,?,?,?)""",
            ("default", None, f"{platform} 回调验签失败", store.now()),
        )
        raise HTTPException(403, "验签失败")

    msg = ch.parse(payload)
    if msg is None:
        # 不是消息（心跳、URL 校验、事件通知）—— 正常情况，不是错误
        return {"ok": True, "ignored": True}

    res = dispatch.accept(msg, ch)
    return {"ok": True, "accepted": res.accepted, "duplicate": res.duplicate,
            "note": res.line()}


@app.post("/api/channels/{platform}/webhook")
async def channel_webhook(platform: str, request: Request) -> Any:
    """平台事件回调入口（飞书/企微都走这里）。"""
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 —— 企微是 XML，飞书是 JSON
        payload = {"_raw": raw.decode("utf-8", "replace")}
    headers = {k: v for k, v in request.headers.items()}

    # 飞书的 URL 校验要回 challenge（配回调地址时会先打这一枪）
    if platform == "feishu" and payload.get("type") == "url_verification":
        ch = _channel_for("feishu")
        return ch.challenge_response(payload)

    return _handle_webhook(platform, payload, headers)


@app.get("/api/channels/wecom/webhook")
def wecom_verify(request: Request) -> Any:
    """企微的 URL 校验是 GET，且要回**解密后的 echostr**。

    ★ 这一步做不对，企微后台根本保存不了回调地址，
      而且它只提示"校验失败"，不会告诉你哪里错。
    """
    from fastapi.responses import PlainTextResponse

    q = dict(request.query_params)
    ch = _channel_for("wecom")
    payload = {"_query": q}
    if not ch.verify(payload, {}):
        raise HTTPException(403, "验签失败")
    try:
        return PlainTextResponse(ch.decrypt(q.get("echostr", "")))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"解密 echostr 失败：{exc}") from exc


@app.post("/api/channels/wecom/webhook")
async def wecom_webhook(request: Request) -> Any:
    """企微消息回调。

    ★ 企微要求 5 秒内响应，**超时就重发**。
      所以这里收到就丢队列、立刻返回空串（企微约定：回空串表示"已收到，不被动回复"）。
      真正的回复走主动发消息 API。
    """
    from fastapi.responses import PlainTextResponse

    raw = (await request.body()).decode("utf-8", "replace")
    q = dict(request.query_params)
    enc = ""
    try:
        import re

        m = re.search(r"<Encrypt><!\[CDATA\[(.*?)\]\]></Encrypt>", raw, re.S)
        enc = m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        enc = ""
    payload = {"_query": q, "Encrypt": enc}
    _handle_webhook("wecom", payload, {k: v for k, v in request.headers.items()})
    return PlainTextResponse("")


# ══════════════════════════════════════════════════════════════════════
# ★★ 人工工作台
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/handover")
def handover_list(tenant_id: str = "default", status: str = "open", limit: int = 50):
    """待人工处理的队列。

    ★ 每条都带上 AI 已经查到的答案和判定理由 ——
      **不能让客户重说一遍，也不能让客服从头摸索**（见决策记录 D38）。
    """
    return {
        "items": store.rows(
            """SELECT h.*, q.citations_json, q.retrieved_json, q.signals_json, q.quality
                 FROM handover_queue h
                 LEFT JOIN qa_log q ON q.id = h.qa_log_id
                WHERE h.tenant_id=? AND h.status=?
                ORDER BY h.created_at DESC LIMIT ?""",
            (tenant_id, status, limit),
        ),
        "counts": {
            s: (store.one(
                "SELECT COUNT(*) n FROM handover_queue WHERE tenant_id=? AND status=?", (tenant_id, s)
            ) or {}).get("n", 0)
            for s in ("open", "taken", "done")
        },
    }


class TakeIn(BaseModel):
    by: str = "客服"


@app.post("/api/handover/{item_id}/take")
def handover_take(item_id: int, body: TakeIn) -> dict[str, Any]:
    """接管。★ 接管之后 AI 必须真的停手，不能再自动回 ——
    否则会出现"人工说了 A，AI 紧接着说了 B"，客户直接懵。

    ★★ 并且**要停掉用户那边的转圈**。
      用户的原话：「只有在人工点了接管以后才会出现已经转人工，
                    否则会一直打转……人工接管之后才会停止转。」
      ★ 所以"接管"这个动作必须**通知到用户端** ——
        不通知的话，用户看到的是一个永远转下去的圈，
        他不知道到底有没有人来，只会觉得系统卡死了。
    """
    row = store.one(
        "SELECT platform, user_id, tenant_id FROM handover_queue WHERE id=?", (item_id,)
    )
    store.run(
        "UPDATE handover_queue SET status='taken', assigned_to=?, taken_at=? WHERE id=?",
        (body.by, store.now(), item_id),
    )
    # ★ 停转。失败不影响接管本身（辅助路径不能把主路径带崩）。
    if row and row["platform"] == "web":
        try:
            from .channels.web import WebChannel

            WebChannel.stop_waiting(row["user_id"], "客服已接入", row["tenant_id"])
        except Exception:  # noqa: BLE001
            pass
    return {"ok": f"{body.by} 已接管"}


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    by: str = "客服"
    send: bool = True


@app.get("/api/handover/{item_id}/context")
def handover_context(item_id: int) -> dict[str, Any]:
    """★ 取这个用户的**完整会话记录**。

    ★★ 为什么必须有这个接口：

      第一版人工工作台只显示"当前这一句 + AI 查到的资料"。
      用户看到后说：
        「人工这里可以看到具体用户上下文的聊天记录，这样才好回复与跟进进度」。

      他说得对 —— 客服手上只有一句话是没法回复的：
        · 他前面已经问过几遍了？        （说明很急）
        · AI 刚才已经答了什么？          （不能再答一遍一样的）
        · 他之前说过自己是谁、哪家公司？  （★ 不用再问一遍 ——
          让人重复自己说过的话，是最伤的体验）
        · 这个会话之前有没有被别人接手过？

      **把时间线拼出来**，客服扫一眼就知道该怎么接。
    """
    ho = store.one("SELECT * FROM handover_queue WHERE id=?", (item_id,))
    if not ho:
        raise HTTPException(404, "没有这条人工请求")
    uid = ho["user_id"] or ""
    plat = ho["platform"] or ""

    # ★ 注意字段名：qa_log 用的是 channel / user_ref，
    #   outbound_log 用的是 platform / user_id，handover_queue 又是 platform / user_id。
    #   三张表不统一 —— 第一版我全写成 user_id/platform，直接查空。
    #   （统一字段名是更大的重构，这里先用准确的列名，并把这个不一致记下来。）
    qa = store.rows(
        """SELECT id, question_raw, answer_text, decision, quality, intent,
                  failure_reason, created_at
             FROM qa_log WHERE user_ref=? AND channel=?
            ORDER BY id ASC LIMIT 100""",
        (uid, plat),
    )
    out = store.rows(
        """SELECT content, decision, created_at FROM outbound_log
            WHERE user_id=? AND platform=? ORDER BY id ASC LIMIT 100""",
        (uid, plat),
    )
    hand = store.rows(
        """SELECT id, question, decision, status, assigned_to, taken_at,
                  reply_text, done_at, created_at
             FROM handover_queue WHERE user_id=? AND platform=?
            ORDER BY id ASC LIMIT 50""",
        (uid, plat),
    )

    # ── 拼时间线 ──────────────────────────────────────────────────
    TL: list[dict[str, Any]] = []
    for r in qa:
        TL.append({"at": r["created_at"], "kind": "user", "text": r["question_raw"],
                   "qa_id": r["id"]})
        dec = r["decision"] or ""
        if dec == "auto" and (r["answer_text"] or "").strip():
            TL.append({"at": r["created_at"], "kind": "ai", "text": r["answer_text"],
                       "quality": r["quality"], "decision": dec})
        elif dec == "shadow":
            TL.append({"at": r["created_at"], "kind": "note",
                       "text": "（影子模式：AI 算了但按设置没发出去）"})
        elif dec:
            TL.append({"at": r["created_at"], "kind": "note",
                       "text": "（这条 AI 没直接答，转人工了）",
                       "why": r["failure_reason"] or r["intent"] or ""})
    for r in out:
        dec = r["decision"] or ""
        if dec == "handover_notice":
            continue  # 系统提示不单列，上面已经体现了
        TL.append({"at": r["created_at"],
                   "kind": "human" if dec in ("human", "handover_reply") else "ai",
                   "text": r["content"], "via": dec})

    order = {"user": 0, "ai": 1, "note": 2, "human": 3}

    # ★ 去重：AI 自动回答会同时出现在 qa_log.answer_text 和 outbound_log.content 里，
    #   不去重的话同一条回答在时间线上出现两遍（实测踩到）。
    #   规则：outbound 里如果内容和已经加过的 AI 回答一样，就跳过。
    seen_ai = {t["text"].strip() for t in TL if t["kind"] == "ai" and t.get("text")}
    deduped = []
    for t in TL:
        if t["kind"] == "ai" and (t.get("text") or "").strip() in seen_ai and (t.get("via") or "") != "human":
            # 已经在 qa_log 里加过了，跳过 outbound 的重复项
            if any(x["kind"] == "ai" and (x.get("text") or "").strip() == (t.get("text") or "").strip()
                   and not x.get("via") for x in deduped):
                continue
        deduped.append(t)
    TL = deduped
    TL.sort(key=lambda x: (x.get("at") or "", order.get(x["kind"], 9)))

    return {
        "handover": {
            "id": ho["id"], "status": ho["status"], "decision": ho["decision"],
            "question": ho["question"], "created_at": ho["created_at"],
            "assigned_to": ho["assigned_to"], "taken_at": ho["taken_at"],
            "reply_text": ho["reply_text"], "done_at": ho["done_at"],
        },
        "user": {"id": uid, "platform": plat, "conversation_id": ho["conversation_id"]},
        "timeline": TL,
        "stats": {
            "asked_before": max(0, len(qa) - 1),
            "handover_times": len(hand),
            "first_seen": qa[0]["created_at"] if qa else ho["created_at"],
        },
    }


@app.post("/api/handover/{item_id}/reply")
def handover_reply(item_id: int, body: ReplyIn) -> dict[str, Any]:
    """客服发出回复。

    ★ 这里同时做两件对"越用越聪明"很重要的事：
      ① 记进 outbound_log（出事能查）
      ② 如果客服改过 AI 的草稿，**把改动喂给学习闭环**（pending 等验证）
    """
    row = store.one("SELECT * FROM handover_queue WHERE id=?", (item_id,))
    if not row:
        raise HTTPException(404, "没有这条待处理记录")

    resp: dict[str, Any] = {"ok": True}
    if body.send:
        try:
            ch = _channel_for(row["platform"])
            resp = ch.send(
                OutboundMessage(
                    platform=row["platform"], tenant_id=row["tenant_id"],
                    user_id=row["user_id"] or "", content=body.text,
                    conversation_id=row["conversation_id"], reason="human",
                    # ★ reply_token 决定 outbox 里的 kind ——
                    #   标成 human，网页那边才知道"这是真人说的"（停了转圈）
                    reply_token="human",
                )
            )
        except Exception as exc:  # noqa: BLE001
            resp = {"ok": False, "error": str(exc)[:200]}

    store.run(
        """UPDATE handover_queue
              SET status='done', reply_text=?, done_at=? WHERE id=?""",
        (body.text, store.now(), item_id),
    )
    store.run(
        """INSERT INTO outbound_log
             (tenant_id, platform, user_id, content, decision, resp, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            row["tenant_id"], row["platform"], row["user_id"], body.text,
            "human", store.jdump(resp)[:1000], store.now(),
        ),
    )
    # ★ AI 的草稿和人工最终发的**不一样** → 说明 AI 那句不够好。
    #   喂给学习闭环（先进 pending，过了评测关卡才生效）。
    if row["qa_log_id"] and row["ai_answer"] and row["ai_answer"].strip() != body.text.strip():
        pipeline.operator_edit(row["qa_log_id"], body.text)
        resp["learned"] = "已记下人工版本，进待验证队列"
    return resp


@app.post("/api/handover/{item_id}/reject")
def handover_reject(item_id: int, why: str = "wrong_fact") -> dict[str, str]:
    """客服判断 AI 那条草稿不能要（直接自己写）。负反馈。"""
    row = store.one("SELECT qa_log_id FROM handover_queue WHERE id=?", (item_id,))
    if row and row["qa_log_id"]:
        pipeline.operator_reject(row["qa_log_id"], why)
    store.run("UPDATE handover_queue SET status='done', done_at=? WHERE id=?", (store.now(), item_id))
    return {"ok": "已记为负反馈"}


# ══════════════════════════════════════════════════════════════════════
# 知识接入
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/kb/sync")
def kb_sync(body: SyncIn) -> dict[str, Any]:
    p = Path(body.path)
    if not p.is_dir():
        raise HTTPException(400, f"目录不存在：{body.path}")
    conn = folder_conn.FolderConnector(p, default_permission=body.default_permission)
    res = folder_conn.sync(conn, tenant_id=body.tenant_id)
    # ★ 同步完顺手扫一遍敏感内容 —— 不让客户从零去圈"哪些能对客户说"，
    #   而是先扫出来给他确认（见 classify.py 的说明）。
    rep = classify.audit(body.tenant_id)
    return {
        "added": res.added,
        "updated": res.updated,
        "deleted": res.deleted,
        "unchanged": res.unchanged,
        "skipped": res.skipped,
        "audit": {
            "total": rep["total"],
            "clean": rep["clean"],
            "severe_count": rep["severe_count"],
            "advice": classify.advice(),
            "risky": [
                {
                    "title": r["title"],
                    "permission": r["permission"],
                    "severe": r["severe"],
                    "flags": [f.line() for f in r["flags"]],
                }
                for r in rep["risky"][:30]
            ],
        },
    }


@app.get("/api/kb/stats")
def kb_stats(tenant_id: str = "default") -> dict[str, Any]:
    return {
        "docs": store.rows(
            """SELECT id, title, source, source_ref, permission, updated_at
                 FROM doc WHERE tenant_id=? ORDER BY title LIMIT 200""",
            (tenant_id,),
        ),
        "chunks": (store.one("SELECT COUNT(*) n FROM chunk WHERE tenant_id=?", (tenant_id,)) or {}).get("n", 0),
    }


@app.get("/api/kb/audit")
def kb_audit(tenant_id: str = "default") -> dict[str, Any]:
    rep = classify.audit(tenant_id)
    return {
        "total": rep["total"],
        "clean": rep["clean"],
        "severe_count": rep["severe_count"],
        "advice": classify.advice(),
        "risky": [
            {
                "title": r["title"],
                "permission": r["permission"],
                "severe": r["severe"],
                "flags": [f.line() for f in r["flags"]],
            }
            for r in rep["risky"]
        ],
    }


# ══════════════════════════════════════════════════════════════════════
# 静态前端（★ 必须放最后：mount("/") 放前面会把 /api/* 全吃掉）
# ══════════════════════════════════════════════════════════════════════

if _WEB.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="web")
