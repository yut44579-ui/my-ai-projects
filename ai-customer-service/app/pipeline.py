"""编排层：一句话进来，走完所有环节，出去一条结果。

★ 顺序是刻意的，每一步的位置都有原因：

    ⓪ 治理开关      ← ★ 放在最前。急停生效时后面一步都不该走
    ① 意图判定      ← 不该答的直接不调模型，省钱又安全
    ② 查同义词      ← 在检索之前。不然后面白检
    ③ 口语→检索词   ← 调一次便宜模型。不先改写，后面什么都查不到
    ④ 检索 + 评分   ← 程序算，不让模型自评
    ⑤ 质量不够就转 ← ★ 在生成之前拦。省一次调用，也避免模型硬编
    ⑥ 生成          ← 只让它转述资料
    ⑦ 出站检查      ← 身份泄漏/自解释/客服腔/markdown
    ⑧ 分级判定      ← 意图优先于分数
    ⑨ ★ 收尾与记录 ← **所有出口都走这里**，影子模式在这一层生效

★ 第 ⑨ 步不是"顺便记一下"。
  没有它，前八步做得再好，系统也永远不会变聪明。

★★ 这一版最重要的结构改变：**所有返回都经过 `_finish()`**。
   原因是影子模式踩过一个坑：
   转人工的路径在函数中间就 return 了，而影子开关当时写在最后 ——
   于是影子期间"AI 答不上来"的那些**还是会真的转人工**，影响了真实流程。
   影子模式的定义是"完全观察、不影响任何东西"，
   所以它必须在**每一个出口**都生效，而不只是最后那一个。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import config, governance, llm, policy, retrieval, store
from . import tiering as T
from .learning import loop as learning


@dataclass
class Answer:
    text: str
    decision: str                 # auto / draft / escalate / shadow
    quality: float
    citations: list[str] = field(default_factory=list)
    hits: list[retrieval.Hit] = field(default_factory=list)
    intent: str | None = None
    reason: str = ""              # 人话说明这条为什么这么判（给运营看）
    problems: list[str] = field(default_factory=list)
    degraded: bool = False
    qa_log_id: int | None = None
    latency_ms: int = 0
    rewrite: str = ""
    # ★ 失败原因**在每个转人工的地方当场标注**，不靠事后从 quality 反推。
    #   第一版就是靠反推的（quality<0.3 就算 no_hit），结果意图转人工的
    #   质量也是 0.0，被误判成"缺知识"，于是"我要投诉"堆进了待补充列表。
    #   每个出口自己最清楚是为什么，就该由它来说。
    failure_reason: str | None = None
    # ★ 没有治理开关干预时，本来会怎么判。
    #   影子模式靠它来算"如果开自动，覆盖率是多少"。
    would_be_decision: str | None = None

    @property
    def needs_human(self) -> bool:
        return self.decision == "escalate"

    @property
    def is_knowledge_gap(self) -> bool:
        """★ 只有"不会答"才该进待补充列表；"故意不答"不能进。

        两者的处理完全不同：
          不会答 → 补知识库（管理员能做的事）
          故意不答 → 判定规则在正常工作（不需要做任何事）
        混在一起会把管理员带偏，让他以为该补资料。
        """
        return self.failure_reason == "no_hit"


def _handover_active(tenant_id: str, channel: str, user_ref: str | None,
                     conversation_id: str | None = None) -> dict | None:
    """这个会话是不是**已经有人工在管**（或者正在等人工）。

    ★★ 为什么必须有这个检查（用户的反馈）：
      「你客服接入了之后，就一直是客服谈话了，不要又弹出其他的」

      ★ 踩到的现象：人工已经接管了，用户又发一句"666"，
        AI 还回一句"没太确定你想问什么" ——
        用户那边就懵了：不是有人接管了吗？怎么又来个机器人？

      ★ 更糟的是状态自相矛盾：
        界面上那个转圈写着"正在为你转接人工客服…"，
        然后 AI 自己又答了一句 —— **系统在打自己的脸**。

    ★ 规则：只要这个会话有 open 或 taken 的人工记录，
      **AI 完全沉默**，把消息留给人工。
      （连"open（还没人接）"也要沉默 —— 因为我们已经跟用户承诺
        "正在转接人工"了，这时候 AI 再开口就是自相矛盾。）
    """
    try:
        where = ["tenant_id=?", "platform=?", "status IN ('open','taken')"]
        args: list = [tenant_id, channel]
        # ★ 优先按会话 id 找（最准），没有再退回按用户找
        if conversation_id:
            where.append("(conversation_id=? OR user_id=?)")
            args += [conversation_id, user_ref or ""]
        elif user_ref:
            where.append("user_id=?")
            args.append(user_ref)
        else:
            return None
        row = store.one(
            f"SELECT id, status, assigned_to FROM handover_queue WHERE {' AND '.join(where)} "
            f"ORDER BY id DESC LIMIT 1",
            tuple(args),
        )
        return dict(row) if row else None
    except Exception:  # noqa: BLE001 —— 查不到就当没有，不能因为查询失败就不回答
        return None


def _escalate_text(intent: str | None) -> str:
    """转人工时给用户看的那句话。

    ★ 评审意见（D6/D7）：转人工就直接转，不要说"我叫个同事"，
      也不要说转给了谁。所以这里只有最朴素的一句。
    """
    return "这个我答不了，我让人来回你。"


def _self_intro_text() -> str:
    """模型不可用时的自我介绍**固定话术**。

    ★ 必须是固定文本，不能靠模型生成 ——
      没有模型的时候让程序编一句，那就是"事实由模型编"，违反第一条纪律。
    """
    return ("我是这里的智能客服，可以帮你查公司资料、产品、流程、政策这些。"
            "你直接问就行，答不上来的我会转人工。")


def _recent_questions(tenant_id: str, user_ref: str | None, channel: str,
                      limit: int = 12) -> list[str]:
    """取这个用户最近问过的问题 —— 用来判断"是不是在重复问同一件事"。

    ★ 这是"中级问题先答一次、重复提及才转人工"的判断依据。
      取不到就当没有历史（新用户自然不会有重复），绝不能因此报错。
    """
    if not user_ref:
        return []
    try:
        rows = store.rows(
            """SELECT question_raw FROM qa_log
                WHERE tenant_id=? AND user_ref=? AND channel=?
                ORDER BY id DESC LIMIT ?""",
            (tenant_id, user_ref, channel, limit),
        )
        return [r["question_raw"] for r in rows if r["question_raw"]]
    except Exception:  # noqa: BLE001 —— 历史查不到不该影响本次回答
        return []


def _clarify_count(tenant_id: str, user_ref: str | None, channel: str, conversation_id: str | None = None) -> int:
    """这个会话**已经被澄清过几次**。

    ★ 为什么要记：无意义输入如果无限澄清下去，就成了"一直问他要干什么"，
      很烦人。所以澄清到 CLARIFY_MAX 次还说不清，就转人工。

    ★★ 一个踩到的坑：第一版只认 `user_ref`，而检测脚本 / 直接调 API
      的时候往往没有 user_ref，于是**计数永远是 0 → 无限澄清**。
      修法：没有 user_ref 就退回用 conversation_id 数。
      ★ 教训：计数类的逻辑，**用来分组的那个 key 必须保证拿得到**，
        拿不到时要能退化到另一个 key，而不是直接返回 0。
    """
    key_field, key_val = ("user_ref", user_ref)
    if not key_val:
        key_field, key_val = ("conversation_id", conversation_id)
    if not key_val:
        return 0
    try:
        row = store.one(
            f"""SELECT COUNT(*) n FROM qa_log
                 WHERE tenant_id=? AND {key_field}=? AND channel=?
                   AND failure_reason='clarified'
                   AND created_at >= datetime('now','-30 minutes')""",
            (tenant_id, key_val, channel),
        )
        return (row or {}).get("n", 0)
    except Exception:  # noqa: BLE001
        return 0


def answer_question(
    question: str,
    *,
    tenant_id: str = "default",
    channel: str = "web",
    is_internal: bool = False,
    conversation_id: str | None = None,
    user_ref: str | None = None,
    context: list[str] | None = None,
    tier: str | None = None,
    permission: str | None = None,
    use_llm: bool = True,
) -> Answer:
    """主入口。所有渠道（网页/企微/飞书）最终都调这里。"""
    t0 = time.time()
    q = (question or "").strip()
    tier = tier or _tenant_tier(tenant_id)
    perm = permission if permission is not None else ("internal" if is_internal else "external")

    # 治理开关在**最开始**读一次。★ 中途不能再变 ——
    # 不然一次请求里前面按了急停、后面又读了新值，行为就不一致了。
    stopped = governance.is_stopped(tenant_id)
    shadow = governance.is_shadow(tenant_id)
    usage = {"tokens_in": 0, "tokens_out": 0}

    def finish(ans: Answer, r: retrieval.Retrieval | None = None, rewrite: str = "") -> Answer:
        """★ 唯一的收尾出口。影子模式在这一层生效。"""
        ans.would_be_decision = ans.decision
        if shadow:
            ans.decision = "shadow"
            ans.reason = f"影子模式（本来会判：{ans.would_be_decision}）｜{ans.reason}"
            if "影子模式，只记录不发送" not in ans.problems:
                ans.problems.append("影子模式，只记录不发送")
        ans.qa_log_id = _log(
            tenant_id, channel, conversation_id, user_ref, is_internal, q, ans, t0, r, rewrite,
            tokens_in=usage["tokens_in"], tokens_out=usage["tokens_out"],
        )
        return ans

    # ── ⓪ 急停：最高优先级，后面一步都不走 ────────────────────────────
    #   客户按了"停"就**真的停** —— 连算都不用算。
    #   这里连草稿也不给：他按这个按钮时的心态是"我要接管了"，
    #   还给草稿的话他还是得逐条看，按钮就没起到"停"的作用。
    if stopped:
        return finish(
            Answer(
                text=_escalate_text(None),
                decision="escalate",
                quality=0.0,
                reason="急停生效中，全部转人工（AI 未参与）",
                failure_reason="should_escalate",  # 不是缺知识，是被人为停掉了
                problems=["急停生效中"],
            )
        )

    # ── ⓪-b ★★ 有人工在管这个会话 → AI 完全沉默 ───────────────────────
    #
    # 用户的反馈：「你客服接入了之后，就一直是客服谈话了，不要又弹出其他的」
    #
    # ★ 踩到的现象：人工已经接管，用户又发一句"666"，
    #   AI 还回一句"没太确定你想问什么" —— 用户就懵了。
    # ★ 更糟的是**状态自相矛盾**：界面上转圈写着"正在为你转接人工客服…"，
    #   然后 AI 自己又答一句，等于系统在打自己的脸。
    #
    # ★ 所以这里在**任何处理之前**就拦下来（急停之后、意图判定之前）：
    #   不检索、不生成、不发任何东西。消息照样记进日志，
    #   人工在工作台能看到（他正在处理，需要知道客户又说了什么）。
    ho = _handover_active(tenant_id, channel, user_ref, conversation_id)
    if ho and not shadow:
        return finish(
            Answer(
                text="",                      # ★ 空字符串 = 什么都不发
                decision="silent",
                quality=0.0,
                reason=f"这个会话已经有人工在管（#{ho['id']} · {ho['status']}"
                       f"{' · ' + ho['assigned_to'] if ho.get('assigned_to') else ''}）"
                       f"—— AI 不再插话，避免和客服说的话打架",
                problems=["人工接管中，AI 静默"],
            )
        )

    # ── ① 分级判定 + 意图判定（都不调模型）──────────────────────────
    #
    # ★★ 三档的处理方式完全不同（见 app/tiering.py 的说明）：
    #     high   → 直接转人工，连检索都不做（报价/合同/退款/投诉/法律）
    #     simple → 不检索，模型直接答（寒暄/"你是做什么的"/道谢）
    #     medium → 正常检索；检索中等时**先答一次**，
    #              只有用户**重复提及**才转人工
    #
    # ★ 顺序很重要：先判高风险，再判简单。
    #   「你们公司多少钱」既有"公司"又有"多少钱"，必须先判成高风险。
    history = _recent_questions(tenant_id, user_ref, channel, limit=12)
    clarify_count = _clarify_count(tenant_id, user_ref, channel, conversation_id)
    tiering = T.classify(
        q, quality=None, history=history, clarify_count=clarify_count,   # ★ None = 还没检索
        auto_threshold=policy.tier_thresholds(tier)[0],
        draft_threshold=policy.tier_thresholds(tier)[1],
    )

    # ① -a ★ 看不懂用户在说什么 → **反问澄清，不转人工**
    #
    # 用户的批评原话：「不是我随便发一句话就直接接入人工，
    # 这样不是会加重客服或者销售的压力？」
    # ★ 人工也没法回答"工作台"这三个字，转过去纯粹是浪费人的注意力。
    if tiering["action"] == "clarify":
        # ★★ 但**缺知识的账还是要记**（只有"检索没命中"的那种才记）。
        #   反问只是"先不打扰人"，不代表"这个问题不值得学"。
        #   不记的话，"越用越聪明"的缺口列表就会漏掉最该补的那些。
        #   ★ 无意义输入（"工作台""测试"）不记 —— 那不是知识缺口。
        if tiering.get("tier") == "medium":
            try:
                learning.record_gap(tenant_id, q)
            except Exception:  # noqa: BLE001 —— 记缺口失败不该影响回答
                pass
        return finish(
            Answer(
                text=T.CLARIFY_TEXT,
                decision="auto", quality=0.0,
                reason=tiering["reason"],
                failure_reason="clarified",   # ★ 用来数"澄清过几次"
                problems=["已澄清"],
            )
        )

    # ① -b 硬风险 / 说不清到极限：直接转，不检索、不生成
    #
    # ★ 注意 failure_reason 分两种：
    #   高风险  → "should_escalate"（故意不答，不是缺知识）
    #   说不清  → "should_escalate"（同上 —— ★ 绝不能记成缺知识，
    #              "工作台"这三个字不是知识缺口，记进去会污染待补充列表）
    if tiering["action"] == "escalate_now":
        return finish(
            Answer(
                text=_escalate_text(None),
                decision="escalate", quality=0.0,
                intent=tiering.get("risk"),
                reason=tiering["reason"] + "（未调模型）",
                failure_reason="should_escalate",   # ★ 故意不答，不是缺知识
                problems=([f"高风险：{tiering.get('risk')}"] if tiering.get("risk")
                          else ["已澄清到上限，用户仍说不清"]),
            )
        )

    # ① -c 软风险：AI 先答流程 + 把信息问全，**不转人工**（见下方 ⑥-2）
    process_kind = tiering.get("risk") if tiering["action"] == "answer_process" else None

    # ① -d 简单：不检索，模型直接答（严格限制不许编业务事实）
    if tiering["tier"] == "simple":
        if use_llm and llm.available():
            gen = llm.self_intro(q)
            usage["tokens_in"], usage["tokens_out"] = gen.tokens_in, gen.tokens_out
            if gen.text:
                out = policy.check_outbound(gen.text, is_internal=is_internal)
                if not out.must_escalate:
                    return finish(
                        Answer(
                            text=out.cleaned or gen.text,
                            decision="auto", quality=1.0,
                            reason=tiering["reason"],
                            problems=out.problems,
                        )
                    )
        # 没有模型（或出站检查拦下）→ 用固定话术，绝不编
        return finish(
            Answer(
                text=_self_intro_text(),
                decision="auto", quality=1.0,
                reason=f"{tiering['reason']}；模型不可用，改用固定话术（不编造）",
            )
        )

    # ① -e 其余（medium）走原来的意图判定
    verdict = policy.judge_intent(q)
    if verdict.escalate and not process_kind:
        return finish(
            Answer(
                text=_escalate_text(verdict.intent),
                decision="escalate",
                quality=0.0,
                intent=verdict.intent,
                reason=f"命中{verdict.intent}类，按规则转人工（未调模型）",
                failure_reason="should_escalate",  # ★ 故意不答，不是缺知识
            )
        )

    # ── ③ 口语 → 检索词 ────────────────────────────────────────────
    rewrite = q
    if use_llm and llm.available():
        rr = llm.rewrite_query(q, context)
        if rr.text:
            rewrite = rr.text
    syn = learning.synonyms(tenant_id)
    search_query = retrieval.expand_query(rewrite, syn)
    if rewrite != q:
        search_query = retrieval.expand_query(f"{q} {rewrite}", syn)

    # ── ④ 检索 + 评分 ──────────────────────────────────────────────
    r = retrieval.search(search_query, tenant_id=tenant_id, permission=perm)
    quality = r.quality

    # ── ⑤ ★★ 软风险（流程类）：答流程 + 问全信息，**不转人工** ──────
    #
    # 用户的批评：「不是我随便发一句话就直接接入人工，
    # 这样不是会加重客服或者销售的压力？」
    #
    # ★ 一条"能报个价吗"直接甩给销售，销售拿到也没用 ——
    #   他还是得回头问"什么零件、什么材料、多少件"。
    #   让 AI 先把流程说清楚、把该问的问全，
    #   人工接手时拿到的才是**一条完整的需求**。
    if process_kind:
        text = ""
        if use_llm and llm.available():
            gen = llm.answer_process(q, r.context() if r.hits else "")
            usage["tokens_in"], usage["tokens_out"] = gen.tokens_in, gen.tokens_out
            text = gen.text or ""
        if not text:
            text = llm.process_fallback(process_kind)
        # ★ 出站检查照旧 —— 流程回答里也**不许出现数字和承诺**
        chk = policy.check_outbound(text, is_internal=is_internal)
        if chk.must_escalate:
            return finish(
                Answer(
                    text=_escalate_text(None), decision="escalate", quality=quality,
                    hits=r.hits, reason="流程回答被出站检查拦下：" + "；".join(chk.problems),
                    failure_reason="should_escalate", problems=chk.problems,
                ),
                r, rewrite,
            )
        return finish(
            Answer(
                text=chk.cleaned or text, decision="auto", quality=quality, hits=r.hits,
                citations=[h.citation() for h in r.hits[:2]],
                reason=tiering["reason"], problems=chk.problems,
            ),
            r, rewrite,
        )

    # ── ⑥ 用**真实检索质量**重跑一次分级 ────────────────────────────
    # ★ 第 ① 步判的时候还没检索，quality 只能填 0；
    #   现在有真实质量了，重判一次，才能正确区分
    #   "检索充分（正常答）" 和 "检索中等（先答+盯着）"。
    tiering = T.classify(
        q, quality=quality, history=history, clarify_count=clarify_count,
        auto_threshold=policy.tier_thresholds(tier)[0],
        draft_threshold=policy.tier_thresholds(tier)[1],
    )
    watch = tiering["action"] == "answer_then_watch"

    # 检索不够 → 转人工（在生成之前拦）
    decision = policy.decide(quality, tier=tier)
    if tiering["action"] == "clarify":
        # ★ 第二次分级也可能给出 clarify（检索没命中时先澄清一次）
        # ★★ 这里**必须记缺口**：检索确实没命中，说明资料里缺这一块。
        #   不记的话"待补充的问题"列表就是空的 —— 学习闭环瞎了。
        try:
            learning.record_gap(tenant_id, q)
        except Exception:  # noqa: BLE001 —— 记缺口失败不该影响回答
            pass
        return finish(
            Answer(
                text=T.CLARIFY_TEXT, decision="auto", quality=quality, hits=r.hits,
                reason=tiering["reason"],
                failure_reason="clarified", problems=["已澄清"],
            ),
            r, rewrite,
        )
    if tiering["action"] == "escalate_now" or decision == "escalate":
        return finish(
            Answer(
                text=_escalate_text(None),
                decision="escalate",
                quality=quality,
                hits=r.hits,
                reason=tiering["reason"] if tiering["action"] == "escalate_now"
                       else f"检索质量 {quality} 低于门槛（命中 {len(r.hits)} 条）",
                failure_reason="no_hit",  # ★ 找不到 → 缺知识，该进待补充列表
            ),
            r,
            rewrite,
        )

    # ── ⑥ 生成 ─────────────────────────────────────────────────────
    citations = [h.citation() for h in r.hits[:4]]
    answer_text = ""
    degraded = False
    problems: list[str] = []

    if use_llm and llm.available():
        style = learning.style_examples(tenant_id)
        gen = llm.generate_answer(q, r.context(), citations, style_examples=style)
        degraded = gen.degraded
        usage["tokens_in"], usage["tokens_out"] = gen.tokens_in, gen.tokens_out
        if gen.text and llm.is_unanswerable(gen.text):
            # ★ 模型说"资料不够" —— 它比我们更清楚资料够不够，听它的，转人工
            return finish(
                Answer(
                    text=_escalate_text(None),
                    decision="escalate",
                    quality=quality,
                    hits=r.hits,
                    citations=citations,
                    reason="模型判定资料不足以回答",
                    failure_reason="no_hit",  # ★ 资料里确实没有 → 缺知识
                ),
                r,
                rewrite,
            )
        answer_text = llm.strip_marker(gen.text) if gen.text else ""

    if not answer_text:
        # ── ★ 第三档降级：不调模型，直接给原文 ──────────────────────
        answer_text = llm.raw_fallback(r.hits)
        degraded = True
        problems.append("模型不可用，已降级为原文直出")
        # 原文直出虽然保住了信息，但**不该自动发** —— 它不像人话，
        # 客户会觉得公司在拿文档糊弄他。所以强制走起草或转人工。
        decision = "draft" if decision == "auto" else decision
        watch = False  # 降级了就不能"先答一次"，因为原文不该直接发

    # ── ★★ 中级问题：先答一次，但把不确定说出来，请用户确认 ────────
    #
    # 用户的要求：「中级的问题会先正常 AI 分析，并反复确认之后再转人工」。
    #
    # ★ 为什么"反复确认"是有用的，而不是客套：
    #     用户确认了       → 说明答对了，这一次不用转人工
    #     用户说"不是这个" → 我们**立刻知道第一次理解错了**，
    #                        比让他换个说法再问一遍（然后我们才发现）早一整轮
    #
    # ★ 而且这种情况下**要发出去**（不能只起草）——
    #   不然"先答一次"就变成了"先让客服看一次"，用户那边什么都没收到，
    #   根本谈不上"确认"。
    if watch and answer_text and not degraded:
        answer_text = answer_text.rstrip() + "\n" + llm.confirm_suffix()
        decision = "auto"
        problems.append(f"中级问题（检索质量 {quality}）：先答一次并请用户确认；"
                        f"如果他重复提及，下次转人工")

    # ── ⑦ 出站检查 ─────────────────────────────────────────────────
    chk = policy.check_outbound(answer_text, is_internal=is_internal)
    problems.extend(chk.problems)
    if chk.must_escalate:
        return finish(
            Answer(
                text=_escalate_text(None),
                decision="escalate",
                quality=quality,
                hits=r.hits,
                citations=citations,
                reason="出站检查拦下：" + "；".join(chk.problems),
                failure_reason="banned",  # ★ 安全问题，不是缺知识（不该进待补充列表）
                problems=problems,
                degraded=degraded,
            ),
            r,
            rewrite,
        )
    answer_text = chk.cleaned or _escalate_text(None)
    if not chk.cleaned:
        decision = "escalate"

    # ── ⑧ 分级（出站检查可能改了判定）─────────────────────────────
    decision = policy.decide(quality, tier=tier) if decision == "auto" else decision
    if policy.judge_intent(answer_text).escalate:
        decision = "escalate"

    return finish(
        Answer(
            text=answer_text,
            decision=decision,
            quality=quality,
            citations=citations,
            hits=r.hits,
            reason=_why(quality, decision, tier),
            problems=problems,
            degraded=degraded,
            rewrite=rewrite,
        ),
        r,
        rewrite,
    )


def _why(quality: float, decision: str, tier: str) -> str:
    auto, draft = policy.tier_thresholds(tier)
    return f"检索质量 {quality}；{tier} 档门槛 自动≥{auto} 起草≥{draft} → {decision}"


def _tenant_tier(tenant_id: str) -> str:
    row = store.one("SELECT tier FROM tenant_config WHERE tenant_id=?", (tenant_id,))
    return (row or {}).get("tier") or "standard"


def _log(
    tenant_id: str,
    channel: str,
    conversation_id: str | None,
    user_ref: str | None,
    is_internal: bool,
    question: str,
    ans: Answer,
    t0: float,
    r: retrieval.Retrieval | None = None,
    rewrite: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> int:
    """★ 记一条问答日志。这是学习闭环的唯一原料，任何时候都不能跳过。"""
    hits = ans.hits or []
    qa_id = store.log_qa(
        tenant_id=tenant_id,
        channel=channel,
        conversation_id=conversation_id,
        user_ref=user_ref,
        is_internal=is_internal,
        question_raw=question,
        question_rewrite=rewrite or None,
        retrieved=[{"doc": h.title, "score": h.score, "stale": h.stale} for h in hits],
        quality=ans.quality,
        signals=(r.signals if r else None),
        citations=ans.citations,
        answer_text=ans.text,
        confidence=ans.quality,
        decision=ans.decision,
        # ★ 影子模式靠这一列算覆盖率。必须落库 ——
        #   靠 quality 反推会漏掉意图命中和出站检查，把数字算高。
        would_be_decision=ans.would_be_decision,
        # ★ 后果预览靠这一列复现"意图优先于分数"的判定顺序。
        intent=ans.intent,
        # ★ 失败原因要在**写入时**就带上（澄清计数靠它）
        failure_reason=ans.failure_reason,
        latency_ms=int((time.time() - t0) * 1000),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    # 转人工的当场就把结果定下来，不用等人工反馈 ——
    # ★ 否则"没答上来"这件事要等到有人去看日志才会被发现，
    #   而它恰恰是最该被立刻沉淀的一类。
    if ans.decision == "escalate":
        store.decide(
            qa_id,
            outcome="unanswered",
            failure_reason=ans.failure_reason or "should_escalate",
        )
        # ★★ 只有"不会答"才进待补充列表，"故意不答"不能进。
        #   第一版把所有转人工都记进去了，结果"我要投诉"被问了 4 次
        #   就排到待补充列表前排 —— 可投诉本来就该转人工，
        #   补知识库也补不出一个"投诉答案"。这会把管理员带偏：
        #   他以为该补资料，其实是判定规则在正常工作。
        if ans.is_knowledge_gap:
            learning.record_gap(tenant_id, question)
    elif ans.decision == "auto":
        store.decide(qa_id, outcome="sent")
    # ★ 影子模式：outcome 保持 pending，等人工回答来对照。
    #   但如果"本来就会因为查不到而转人工"，那缺口是真的，
    #   影子期间也要记进待补充列表 —— 否则白跑两周什么都没学到。
    elif ans.decision == "shadow" and ans.is_knowledge_gap:
        learning.record_gap(tenant_id, question)
    return qa_id


# ══════════════════════════════════════════════════════════════════════
# 反馈入口 —— "越用越聪明"的数据从这里进来
# ══════════════════════════════════════════════════════════════════════

def operator_edit(qa_log_id: int, final_text: str) -> None:
    """人工把草稿改完发出去了。

    ★ 这是整个系统里**最重要、最便宜**的一路反馈：
      人工本来就要改，我们顺手把它变成训练素材，零额外成本。
      量最大，而且学到的是"这家公司自己的说法"。
    """
    store.decide(qa_log_id, outcome="edited", failure_reason="bad_style", edited_to=final_text)
    store.add_feedback(qa_log_id, source="operator_edit", kind="correction",
                       detail={"final": final_text})
    # 立刻挖一次，让它尽快进待验证队列（真正生效还要过评测关卡）
    row = store.one("SELECT tenant_id FROM qa_log WHERE id=?", (qa_log_id,))
    if row:
        learning.mine_style_examples(row["tenant_id"], days=1)


def operator_accept(qa_log_id: int) -> None:
    """人工直接点了发送（说明草稿可用）。这也是正反馈。"""
    store.decide(qa_log_id, outcome="accepted")
    store.add_feedback(qa_log_id, source="operator_edit", kind="positive")


def operator_reject(qa_log_id: int, why: str = "wrong_fact") -> None:
    """人工没用这条草稿，直接自己写了。属于负反馈。"""
    store.decide(qa_log_id, outcome="rejected", failure_reason=why)
    store.add_feedback(qa_log_id, source="operator_edit", kind="negative", detail={"why": why})


def customer_retry(qa_log_id: int) -> None:
    """客户又追问了同一件事 → 说明上一条没解决。

    ★ 这也是自动收集的反馈，零成本。但要谨慎：
      追问不一定代表答错（可能只是没看懂），所以只记 negative 不改判定。
    """
    store.add_feedback(qa_log_id, source="customer_retry", kind="negative")
