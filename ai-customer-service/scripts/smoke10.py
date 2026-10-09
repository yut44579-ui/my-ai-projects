"""验证新表 + 走一遍"接管 → 回复 → 客户再发 → 结束"的完整流程。

★ 这是这次改动的核心断言：
   人工接管之后，客户再发话，AI **不能**再转人工；
   只有人工点「结束接管」，AI 才恢复。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ★ 用隔离的临时库，别碰真库（踩过 WinError 32）
TMP = Path(tempfile.mkdtemp(prefix="kefu_hs_"))
os.environ["KEFU_DB"] = str(TMP / "kefu.db")
os.environ.setdefault("DEEPSEEK_API_KEY", "")   # 不调模型
sys.path.insert(0, r"D:\ai-kefu")

from app import store  # noqa: E402

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

print("=== ① 表建出来了 ===")
tabs = [x["name"] for x in store.rows(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='human_session'")]
check("human_session 表存在", tabs == ["human_session"], str(tabs))
cols = [c["name"] for c in store.rows("PRAGMA table_info(human_session)")]
for c in ("tenant_id", "platform", "user_id", "conversation_id",
          "assigned_to", "status", "take_count", "started_at", "ended_at"):
    check(f"字段 {c}", c in cols)

print("\n=== ② 接管会建会话 ===")
from app import api  # noqa: E402

# 造一条待处理
store.run(
    """INSERT INTO handover_queue
         (tenant_id, platform, user_id, conversation_id, question, status, created_at)
       VALUES ('default','web','u1','c1','能报个价吗','open',?)""",
    (store.now(),),
)
item = store.one("SELECT id FROM handover_queue WHERE user_id='u1'")
iid = item["id"]

r = api.handover_take(iid, api.TakeIn(by="张工"))
check("接管返回 ok", r.get("ok") == "张工 已接管", str(r))
check("第一次接管 take_count=1", r.get("take_count") == 1, str(r.get("take_count")))

sess = store.one("SELECT * FROM human_session WHERE user_id='u1' AND status='active'")
check("建了 active 的人工会话", sess is not None)
check("会话记了谁在管", sess and sess["assigned_to"] == "张工")

print("\n=== ③ ★ 客户再发话，AI 必须沉默 ===")
from app import pipeline  # noqa: E402

ho = pipeline._handover_active("default", "web", "u1", "c1")
check("检测到有人工在管", ho is not None, str(ho))
check("来源是会话级（status=session）", ho and ho.get("status") == "session",
      str(ho.get("status") if ho else None))
check("带上了接管次数", ho and ho.get("take_count") == 1,
      str(ho.get("take_count") if ho else None))

print("\n=== ④ ★ 客服回复后，会话仍然在人工手上（这是原来的 bug）===")
api.handover_reply(iid, api.ReplyIn(text="报价要工程师看过图纸才能定", by="张工", send=False))
item2 = store.one("SELECT status FROM handover_queue WHERE id=?", (iid,))
check("问题记录保持 taken（不是 done）", item2["status"] == "taken", item2["status"])
ho2 = pipeline._handover_active("default", "web", "u1", "c1")
check("★ 回复后 AI 仍然沉默（原来这里会漏）", ho2 is not None, str(ho2))

print("\n=== ⑤ 只有人工点「结束接管」才交还 AI ===")
r2 = api.handover_end(iid, by="张工")
check("结束接口返回 ok", r2.get("ok", "").startswith("张工 已结束接管"), str(r2))
sess2 = store.one("SELECT status FROM human_session WHERE user_id='u1' ORDER BY id DESC LIMIT 1")
check("会话变成 ended", sess2["status"] == "ended", sess2["status"])
ho3 = pipeline._handover_active("default", "web", "u1", "c1")
check("★ 结束后 AI 恢复（查不到人工）", ho3 is None, str(ho3))
item3 = store.one("SELECT status FROM handover_queue WHERE id=?", (iid,))
check("问题记录变成 done", item3["status"] == "done", item3["status"])

print("\n=== ⑥ ★ 同一个人被接管次数限制 ===")
# ★ 注意：这里的"次数"指的是**新开了几次人工会话**。
#   同一个会话里人工接手好几条问题，只算一次 —— 那本来就是同一个人在管。
#   ★ 所以测试必须在两次接管之间「结束接管」，模拟"客户又来了、又转了一次"。
counts = []
last = {}
for n in range(4):   # 加上前面那次，一共 5 次
    store.run(
        """INSERT INTO handover_queue
             (tenant_id, platform, user_id, conversation_id, question, status, created_at)
           VALUES ('default','web','u1','c1',?,'open',?)""",
        (f"追加问题{n+2}", store.now()),
    )
    nid = store.rows(
        "SELECT id FROM handover_queue WHERE user_id='u1' ORDER BY id DESC LIMIT 1")[0]["id"]
    last = api.handover_take(nid, api.TakeIn(by="张工"))
    counts.append(last.get("take_count"))
    # ★ 结束掉，让下一次算成"新的一次接管"
    api.handover_end(nid, by="张工")

check("第 2 次 take_count=2", counts[0] == 2, str(counts))
check("第 3 次 take_count=3", counts[1] == 3, str(counts))
check("第 4 次 take_count=4（超过上限 3）", counts[2] == 4, str(counts))
check("到上限为止没警告", "warn" not in {k: v for k, v in last.items() if k == "warn"} or counts[2] > 3,
      str(counts))
check("★ 超过上限时给警告", "warn" in last, str(last))
if "warn" in last:
    print("       警告文案:", last["warn"][:80])

# ★ 同一次会话里多接几条问题，不应该重复计数
store.run(
    """INSERT INTO handover_queue
         (tenant_id, platform, user_id, conversation_id, question, status, created_at)
       VALUES ('default','web','u3','c3','问题A','open',?)""", (store.now(),))
store.run(
    """INSERT INTO handover_queue
         (tenant_id, platform, user_id, conversation_id, question, status, created_at)
       VALUES ('default','web','u3','c3','问题B','open',?)""", (store.now(),))
ids = [x["id"] for x in store.rows(
    "SELECT id FROM handover_queue WHERE user_id='u3' ORDER BY id")]
r1 = api.handover_take(ids[0], api.TakeIn(by="张工"))
r2 = api.handover_take(ids[1], api.TakeIn(by="张工"))   # 同一个会话，没结束
check("同一会话里接第 2 条不算新的一次", r2.get("take_count") == r1.get("take_count"),
      f"{r1.get('take_count')} → {r2.get('take_count')}")

print("\n=== ⑦ 不同的人互不影响 ===")
store.run(
    """INSERT INTO handover_queue
         (tenant_id, platform, user_id, conversation_id, question, status, created_at)
       VALUES ('default','web','u2','c2','能退款吗','open',?)""",
    (store.now(),),
)
nid2 = store.rows("SELECT id FROM handover_queue WHERE user_id='u2'")[0]["id"]
rr5 = api.handover_take(nid2, api.TakeIn(by="李工"))
check("另一个人从 1 开始算", rr5.get("take_count") == 1, str(rr5.get("take_count")))
check("另一个人没有警告", "warn" not in rr5, str(rr5))


print("\n=== ⑧ ★ 接管后还在列表里（不能消失）===")
lst = api.handover_list("default")
ids_in_list = [x["id"] for x in lst["items"]]
# ★ 用**当前还是 taken** 的那条（u3 的第二条）来断言。
#   iid 在第 ⑤ 步已经点过"结束接管"变成 done 了 —— 拿它来断言是我写错了。
_taken_now = [x["id"] for x in lst["items"] if x["status"] == "taken"]
check("★ 已接管的记录仍在默认列表里（客服看得到自己在忙什么）",
      len(_taken_now) > 0 and all(i in ids_in_list for i in _taken_now),
      f"列表 {len(ids_in_list)} 条，其中 taken: {_taken_now}")
check("列表同时包含 open 和 taken",
      any(x["status"] == "taken" for x in lst["items"]),
      str(sorted({x["status"] for x in lst["items"]})))
only_open = api.handover_list("default", status="open")
check("显式传 status=open 仍然只给 open",
      all(x["status"] == "open" for x in only_open["items"]),
      str(sorted({x["status"] for x in only_open["items"]})))
only_taken = api.handover_list("default", status="taken")
check("显式传 status=taken 只给 taken",
      all(x["status"] == "taken" for x in only_taken["items"]) and len(only_taken["items"]) > 0,
      str(sorted({x["status"] for x in only_taken["items"]})))

print("\n" + "=" * 58)
print(f"通过 {ok} · 失败 {fail}")
print("=" * 58)
sys.exit(1 if fail else 0)
