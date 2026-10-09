"""冒烟测试 5：渠道接入层（去重 / 异步 / 路由 / 验签 / 加解密）。

★ 这一层最容易出事故，而且**大多跟业务无关，纯粹是接入协议的坑**：

    ① 去重没做 → 平台重发 → 用户被答两遍（而且两遍可能不一样）
    ② 同步处理   → 超过平台等待窗口 → 平台一直重发 → 雪崩
    ③ 路由写错   → 该转人工的自动发出去了（最严重）
    ④ 不验签     → 任何人都能伪造消息烧你的模型额度
    ⑤ worker 没兜住异常 → 线程静默退出，后面所有消息都不处理

  这五条都能在**不接真实平台**的情况下测出来，所以必须先测。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["KEFU_DB"] = str(Path(tempfile.mkdtemp(prefix="kefu_test5_")) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import store  # noqa: E402
from app.channels import dispatch as D  # noqa: E402
from app.channels.feishu import FeishuChannel, _extract_text, _signature as fs_sig  # noqa: E402
from app.channels.sim import SimChannel  # noqa: E402
from app.channels.wecom import WeComChannel, _signature as wc_sig  # noqa: E402
from app.connectors import folder as folder_conn  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


print("=" * 64)
print("冒烟测试 5 · 渠道接入层")
print("=" * 64)

store.init_db()

# ── 准备知识 ──────────────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix="kb5_"))
(tmp / "员工手册.md").write_text(
    "标题: 员工手册\n权限: external\n---\n"
    "第1.1条 工作时间：上午九点至下午六点，中午休息一小时。\n"
    "第4.2条 年假：入职满一年享有5天，此后每满一年增加1天，最多15天。\n",
    encoding="utf-8",
)
folder_conn.sync(folder_conn.FolderConnector(tmp), tenant_id="default")

# ══════════════════════════════════════════════════════════════════════
# 一、飞书：验签
# ══════════════════════════════════════════════════════════════════════
print("\n[1] 飞书：验签（不验签任何人都能伪造消息烧额度）")
fs = FeishuChannel(
    app_id="cli_test", app_secret="sec", verification_token="vtok", encrypt_key="ekey",
)
payload = {"schema": "2.0", "header": {"event_type": "im.message.receive_v1"}}
body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
ts = str(int(time.time()))
nonce = "n1"
good = fs_sig("ekey", ts, nonce, body)
headers_ok = {
    "X-Lark-Request-Timestamp": ts,
    "X-Lark-Request-Nonce": nonce,
    "X-Lark-Signature": good,
}
check("★ 正确签名通过", fs.verify(payload, headers_ok))

bad = dict(headers_ok)
bad["X-Lark-Signature"] = "0" * 64
check("★ 错误签名被拒", not fs.verify(payload, bad))

# 重放：时间戳是 1 小时前的
old_ts = str(int(time.time()) - 3600)
old_sig = fs_sig("ekey", old_ts, nonce, body)
replay = {
    "X-Lark-Request-Timestamp": old_ts,
    "X-Lark-Request-Nonce": nonce,
    "X-Lark-Signature": old_sig,
}
check("★★ 重放攻击被拒（时间戳太旧）", not fs.verify(payload, replay), "签名对但时间旧")

check("★ 完全没签名时被拒（不能默认放行）", not fs.verify(payload, {}))

# ── 2. 飞书：解析 ─────────────────────────────────────────────────────
print("\n[2] 飞书：事件解析")
check("URL 校验请求不当成消息",
      fs.parse({"type": "url_verification", "challenge": "abc"}) is None)
check("URL 校验能正确回 challenge",
      fs.challenge_response({"type": "url_verification", "challenge": "abc"}) == {"challenge": "abc"})

event = {
    "schema": "2.0",
    "header": {"event_type": "im.message.receive_v1", "create_time": "1700000000"},
    "event": {
        "sender": {"sender_id": {"open_id": "ou_zhangwei"}},
        "message": {
            "message_id": "om_msg_001",
            "chat_id": "oc_chat_001",
            "message_type": "text",
            # ★ 飞书的 content 是**字符串化的 JSON**
            "content": json.dumps({"text": "@_user_1 上班时间是几点"}, ensure_ascii=False),
        },
    },
}
m = fs.parse(event)
check("解析出消息", m is not None)
if m:
    print(f"     user={m.user_id} conv={m.conversation_id} text={m.content!r}")
    check("★ 去掉了 @ 占位符", "@_user_1" not in m.content, m.content)
    check("内容正确", "上班时间是几点" in m.content, m.content)
    check("拿到了 message_id（去重要用）", m.message_id == "om_msg_001", m.message_id)

check("非文本消息不假装读懂了（转人工）",
      "处理不了" in (fs.parse({
          "header": {"event_type": "im.message.receive_v1"},
          "event": {"sender": {"sender_id": {"open_id": "ou_x"}},
                    "message": {"message_id": "m2", "message_type": "image"}},
      }).content))
check("无关事件类型被忽略",
      fs.parse({"header": {"event_type": "im.chat.member.bot.added_v1"}}) is None)

# ★ 内部/外部判定必须保守
anyone = fs.parse(event)
check("★★ 没配内部名单时一律当外部客户（不能猜成内部）",
      anyone is not None and anyone.is_internal is False)
fs2 = FeishuChannel(verification_token="vtok", internal_open_ids={"ou_zhangwei"})
check("配了名单后能识别内部员工", fs2.parse(event).is_internal is True)

check("拿不到文本时返回 None（不崩）", _extract_text("") == "" and _extract_text("坏json") == "坏json")

# ══════════════════════════════════════════════════════════════════════
# 三、企微：AES 加解密
# ══════════════════════════════════════════════════════════════════════
print("\n[3] 企微：AES 加解密（写错了表现是「回调一直失败」，平台不告诉你哪里错）")
key = base64.b64encode(os.urandom(32)).decode()[:43]
wc = WeComChannel(corp_id="wwtest123", token="mytoken", encoding_aes_key=key)

for n in (1, 16, 31, 32, 33, 100):
    plain = "测" * n
    back = wc.decrypt(wc.encrypt(plain))
    check(f"往返一致（明文 {n} 字）", back == plain, f"{plain[:10]} != {back[:10]}")

# ★ 边界：PKCS7 用 32 字节块，不是 16。长度刚好是 32 倍数时最容易出错。
check("★ 32 字节块边界正确（不是 AES 标准的 16）",
      wc.decrypt(wc.encrypt("A" * 32)) == "A" * 32)
check("★ 32 的倍数 +16 也对（这里是最容易踩的坑）",
      wc.decrypt(wc.encrypt("B" * 48)) == "B" * 48)

# receiveid 校验
try:
    other = WeComChannel(corp_id="ww_other", token="t", encoding_aes_key=key)
    wc.decrypt(other.encrypt("hi"))
    check("★ 发给别的企业的密文会被拒（receiveid 校验）", False, "居然解开了")
except ValueError:
    check("★ 发给别的企业的密文会被拒（receiveid 校验）", True)
except Exception as exc:  # noqa: BLE001
    check("★ 发给别的企业的密文会被拒（receiveid 校验）", False, str(exc))

# ── 4. 企微：验签 ─────────────────────────────────────────────────────
print("\n[4] 企微：验签")
enc = wc.encrypt("<xml></xml>")
ts2 = str(int(time.time()))
sig2 = wc_sig("mytoken", ts2, "n2", enc)
check("正确签名通过",
      wc.verify({"_query": {"msg_signature": sig2, "timestamp": ts2, "nonce": "n2"},
                 "Encrypt": enc}, {}))
check("错误签名被拒",
      not wc.verify({"_query": {"msg_signature": "bad", "timestamp": ts2, "nonce": "n2"},
                     "Encrypt": enc}, {}))
check("★ 缺字段时被拒（不能默认放行）",
      not wc.verify({"_query": {"msg_signature": sig2}}, {}))
old_ts2 = str(int(time.time()) - 3600)
check("★ 重放被拒",
      not wc.verify({"_query": {"msg_signature": wc_sig("mytoken", old_ts2, "n2", enc),
                                "timestamp": old_ts2, "nonce": "n2"}, "Encrypt": enc}, {}))

# ── 5. 企微：XML 解析 ─────────────────────────────────────────────────
print("\n[5] 企微：XML 解析")
xmlmsg = (
    "<xml><ToUserName><![CDATA[wwtest123]]></ToUserName>"
    "<FromUserName><![CDATA[ZhangWei]]></FromUserName>"
    "<CreateTime>1700000000</CreateTime>"
    "<MsgType><![CDATA[text]]></MsgType>"
    "<Content><![CDATA[年假能休几天]]></Content>"
    "<MsgId>1234567890123456</MsgId><AgentID>1</AgentID></xml>"
)
parsed = wc.parse({"_plain": xmlmsg})
check("解析出消息", parsed is not None)
if parsed:
    print(f"     user={parsed.user_id} text={parsed.content} msgid={parsed.message_id}")
    check("内容正确", parsed.content == "年假能休几天", parsed.content)
    check("★ 拿到了 MsgId（企微重发时它不变，去重靠它）",
          parsed.message_id == "1234567890123456", parsed.message_id)
check("非文本消息转人工",
      "处理不了" in wc.parse({"_plain": xmlmsg.replace("text", "image")}).content)
check("坏 XML 返回 None 不崩", wc.parse({"_plain": "<xml>没闭合"}) is None)
check("没有 Encrypt 也没有明文时返回 None", wc.parse({}) is None)

# ══════════════════════════════════════════════════════════════════════
# 六、调度：去重 + 异步 + 路由
# ══════════════════════════════════════════════════════════════════════
print("\n[6] ★★ 调度：去重（不去重用户会被答两遍）")
ch = SimChannel()
msg = ch.parse({"text": "上班时间是几点", "message_id": "fixed-001"})

r1 = D.accept(msg, ch)
check("第一次受理", r1.accepted, r1.line())
# 模拟平台重发同一条
msg2 = ch.parse({"text": "上班时间是几点", "message_id": "fixed-001"})
r2 = D.accept(msg2, ch)
check("★★ 同一个 message_id 第二次被忽略（平台重发）", not r2.accepted and r2.duplicate, r2.line())

# 不同的 message_id 应该正常受理
msg3 = ch.parse({"text": "年假能休几天", "message_id": "fixed-002"})
check("不同 message_id 正常受理", D.accept(msg3, ch).accepted)

D.drain(timeout=60)

# ── 7. 异步 + 路由 ────────────────────────────────────────────────────
print("\n[7] ★★ 异步处理与回复路由")
# 去重表里应该都有记录
rows = store.rows("SELECT message_id, status FROM inbound_log ORDER BY id")
print(f"     入站记录: {[(r['message_id'], r['status']) for r in rows]}")
check("入站消息都留了档", len(rows) == 2, f"实际 {len(rows)}（重发的那条不该新增）")
check("处理完的状态被更新", all(r["status"] == "processed" for r in rows), str(rows))

# auto 的应该真发出去了
sent_texts = ch.texts()
print(f"     实际发出: {sent_texts}")
check("★ 判定为 auto 的真的发给了用户", len(sent_texts) >= 1, str(sent_texts))
# ★★ 反过来断言：发出去的话里**不许有出处**。
#   用户的明确要求：「不要标明出处，来源泄露信息」——
#   那些"出处"是内部文档名（《报价底线》《客户分级标准》），
#   给客户看等于把内部资料结构抖出去了。
#   ★ 而"可溯源"没破：qa_log 里照样存着 citations，
#     控制台和人工工作台会单独一栏显示给自己人看。
check("★★ 发出去的话里没有出处（不向客户暴露内部资料名）",
      not any(("出处" in t or "来源" in t or "《" in t) for t in sent_texts), str(sent_texts))
check("★ 但溯源信息仍然存了（自己人要看）",
      store.one("SELECT COUNT(*) n FROM qa_log WHERE citations_json IS NOT NULL AND citations_json <> '[]'")["n"] >= 1)
check("出站留了档（出事要能查）",
      store.one("SELECT COUNT(*) n FROM outbound_log")["n"] >= 1)

# 发出去的内容不能含内部身份
for t in sent_texts:
    check(f"★ 发出的话没有内部身份词（{t[:12]}…）",
          not any(w in t for w in ("财务部", "法务部", "工号")), t)

# 敏感问题应该进人工队列，且**不发给用户 AI 的回答**
before = len(ch.sent)
ch2 = SimChannel()
m_sens = ch2.parse({"text": "你们能便宜点吗", "message_id": "sens-001"})
D.accept(m_sens, ch2)
D.drain(timeout=60)
# ★ 现在会发一条**系统提示**（"—— 已转人工，稍等 ——"），
#   因为不给用户交代的话他会干等、以为系统坏了。
#   但**不能有 AI 生成的回答** —— 那才是这条断言真正要防的。
user_msgs = ch2.texts()
ai_msgs = [t for t in user_msgs if "人工" not in t]
print(f"     用户收到 {len(user_msgs)} 条: {user_msgs}")
check("★★ 敏感问题没有 AI 生成的回答（只有系统提示）", len(ai_msgs) == 0, str(ai_msgs))
check("★ 但用户收到了「已转人工」的交代（不是干等）",
      any("人工" in t for t in user_msgs), str(user_msgs))
ho = store.rows("SELECT question, decision, status FROM handover_queue ORDER BY id")
print(f"     人工队列: {[(r['question'][:10], r['decision']) for r in ho]}")
check("★ 敏感问题进了人工队列", len(ho) >= 1, str(ho))
check("队列里标了为什么转人工", any(r["decision"] == "escalate" for r in ho), str(ho))

# ── 8. 影子模式：什么都不发 ───────────────────────────────────────────
print("\n[8] ★ 影子模式下什么都不发（但留档）")
from app import governance as G  # noqa: E402

G.set_shadow("default", on=True)
ch3 = SimChannel()
D.accept(ch3.parse({"text": "上班时间是几点", "message_id": "shadow-001"}), ch3)
D.drain(timeout=60)
check("★★ 影子模式下一条都不发", len(ch3.sent) == 0, str(ch3.texts()))
row = store.one(
    "SELECT decision, would_be_decision FROM qa_log WHERE question_raw='上班时间是几点' ORDER BY id DESC LIMIT 1"
)
check("但记下了本来会怎么判", row and row["would_be_decision"] is not None, str(row))
G.set_shadow("default", on=False)

# ── 9. 急停：全部走人工 ───────────────────────────────────────────────
print("\n[9] ★ 急停期间全部走人工")
G.set_stop("default", on=True, by="测试", reason="")
ch4 = SimChannel()
D.accept(ch4.parse({"text": "上班时间是几点", "message_id": "stop-001"}), ch4)
D.drain(timeout=60)
# ★ 同上：急停期间也不能让用户干等，系统提示发一条是对的。
#   不能发的是"AI 的回答"（急停的意义就是 AI 不参与）。
ai4 = [t for t in ch4.texts() if "人工" not in t]
print(f"     用户收到: {ch4.texts()}")
check("★ 急停期间没有 AI 的回答", len(ai4) == 0, str(ai4))
check("但用户还是知道有人接手了",
      any("人工" in t for t in ch4.texts()), str(ch4.texts()))
check("而是进人工队列",
      store.one("SELECT COUNT(*) n FROM handover_queue")["n"] >= 2)
G.set_stop("default", on=False, by="测试", reason="")

# ── 10. worker 健壮性 ─────────────────────────────────────────────────
print("\n[10] ★ worker 不能被单条坏消息搞死")


class BoomChannel(SimChannel):
    def send(self, msg):  # type: ignore[override]
        raise RuntimeError("模拟发送失败")


b = BoomChannel()
# ★ 用独立的用户/会话 —— 第 9 步急停留下了一条 open 的转人工记录，
#   同一个用户会被判成"已有人工在管"而静默，就走不到发送那一步了。
#   这里要测的是 worker 扛不扛得住发送异常，跟转人工无关。
D.accept(b.parse({"text": "上班时间是几点", "message_id": "boom-001",
                  "user_id": "boom-user", "conversation_id": "boom-conv"}), b)
D.drain(timeout=60)
errs = store.rows("SELECT error FROM dispatch_error")
print(f"     失败记录: {[e['error'][:40] for e in errs]}")
check("★ 发送失败被记下来了（不是静默丢掉）", len(errs) >= 1, str(errs))

# worker 还活着，后续消息还能处理
ch5 = SimChannel()
# ★ 同样用独立用户，否则会被第 9 步那条 open 的人工记录挡住
D.accept(ch5.parse({"text": "年假能休几天", "message_id": "after-boom",
                    "user_id": "after-boom-user",
                    "conversation_id": "after-boom-conv"}), ch5)
D.drain(timeout=60)
check("★★ 出过错之后 worker 还活着（没静默退出）", len(ch5.sent) >= 1, str(ch5.texts()))

# ── 11. 统计 ──────────────────────────────────────────────────────────
print("\n[11] 调度统计")
st = D.stats()
print(f"     {st}")
check("能给出积压/受理/重复的统计", "pending" in st and st["accepted"] >= 4, str(st))
check("统计到重复消息", st["duplicate"] >= 1, str(st))

print("\n" + "=" * 64)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 64)
D.shutdown()
sys.exit(1 if FAIL else 0)
