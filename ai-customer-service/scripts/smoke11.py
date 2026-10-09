"""smoke11：连发消息的处理（防抖合并 + 新消息计数）。

★ 用户问的：「如果用户连续多次发消息，要怎么及时处理多个消息呢」
  这里把它变成可验证的断言。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp(prefix="kefu_burst_"))
os.environ["KEFU_DB"] = str(TMP / "kefu.db")
os.environ.setdefault("DEEPSEEK_API_KEY", "")
# ★ 测试里把防抖窗口调短，不然每条要等 2.5 秒
os.environ["DEBOUNCE_SECONDS"] = "0.6"
sys.path.insert(0, r"D:\ai-kefu")

from app import store  # noqa: E402
from app.channels import dispatch as D  # noqa: E402
from app.channels.sim import SimChannel  # noqa: E402

ok = 0
fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        print(f"  OK   {label}")
        ok += 1
    else:
        print(f"  FAIL {label} {extra}")
        fail += 1


store.init_db()

print("=== ① 字段建出来了 ===")
cols = [c["name"] for c in store.rows("PRAGMA table_info(handover_queue)")]
check("handover_queue 有 new_count", "new_count" in cols)
check("handover_queue 有 last_user_at", "last_user_at" in cols)

print("\n=== ② 连发 3 条 → 只处理一次（合并）===")
ch = SimChannel()
ids = []
for i, text in enumerate(["我想问一下", "关于报价的事", "你们能便宜点吗"]):
    m = ch.parse({"text": text, "message_id": f"burst-{i}",
                  "user_id": "burst-user", "conversation_id": "burst-conv"})
    D.accept(m, ch)
    ids.append(m.dedup_key())
    time.sleep(0.15)          # 模拟人打字：三条挨着发
D.drain(timeout=60)

# ★ 关键断言：只产生一条问答记录（不是三条）
qa = store.rows("SELECT id, question_raw FROM qa_log WHERE user_ref='burst-user'")
check("★ 连发 3 条只处理了一次（qa_log 一条）", len(qa) == 1, f"实际 {len(qa)} 条")
if qa:
    q = qa[0]["question_raw"]
    print(f"       合并后的问题: {q[:70]!r}")
    check("★ 三条都并进去了（不是只剩第一句）",
          all(x in q for x in ("我想问一下", "关于报价的事", "你们能便宜点吗")), q[:80])

print("\n=== ③ 全部标成已处理（不会一直挂着 accepted）===")
for key in ids:
    row = store.one("SELECT status FROM inbound_log WHERE dedup_key=?", (key,))
    check(f"  {key[:18]}… 状态", row is not None and row["status"] == "processed",
          row["status"] if row else "查不到")

print("\n=== ④ 统计里能看到合并了几条 ===")
st = D.stats()
check("统计有 merged",
      "merged" in st and st["merged"] >= 2, str(st))

print("\n=== ⑤ 单发不受影响（该等还是等一下，但只处理一条）===")
ch2 = SimChannel()
m = ch2.parse({"text": "打样要多久", "message_id": "single-1",
               "user_id": "single-user", "conversation_id": "single-conv"})
D.accept(m, ch2)
D.drain(timeout=60)
qa2 = store.rows("SELECT id FROM qa_log WHERE user_ref='single-user'")
check("单发也是一条记录", len(qa2) == 1, str(len(qa2)))

print("\n=== ⑥ 客户在人工接管期间又发消息 → new_count 累加 ===")
# 造一条队列项并接管
store.run(
    """INSERT INTO handover_queue
         (tenant_id, platform, user_id, conversation_id, question, status, created_at)
       VALUES ('default','sim','burst-user','burst-conv','你们能便宜点吗','open',?)""",
    (store.now(),),
)
iid = store.rows("SELECT id FROM handover_queue WHERE user_id='burst-user'")[0]["id"]
from app import api  # noqa: E402

api.handover_take(iid, api.TakeIn(by="张工"))
row = store.one("SELECT new_count FROM handover_queue WHERE id=?", (iid,))
check("刚接管时 new_count=0", row["new_count"] == 0, str(row["new_count"]))

# 客户又说两句（走 pipeline，会静默但计数）
from app import pipeline  # noqa: E402

for i, text in enumerate(["我的电话是13800000000", "你们几点上班"]):
    ans = pipeline.answer_question(
        tenant_id="default", question=text, channel="sim",
        user_ref="burst-user", conversation_id="burst-conv",
    )
    check(f"第 {i+1} 句被静默（AI 不说话）", ans.decision == "silent", ans.decision)

row = store.one("SELECT new_count, last_user_at FROM handover_queue WHERE id=?", (iid,))
check("★ 客户又说的两句被记下来了（new_count=2）", row["new_count"] == 2, str(row["new_count"]))
check("记了最后说话时间", row["last_user_at"] is not None, str(row["last_user_at"]))

print("\n=== ⑦ 客服一看会话就清零 ===")
api.handover_seen(iid)
row = store.one("SELECT new_count FROM handover_queue WHERE id=?", (iid,))
check("★ 看完清零", row["new_count"] == 0, str(row["new_count"]))

print("\n" + "=" * 58)
print(f"通过 {ok} · 失败 {fail}")
print("=" * 58)
D.shutdown()
sys.exit(1 if fail else 0)
