"""查 smoke5 那两条为什么失败。"""
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp(prefix="kefu_dbg_"))
os.environ["KEFU_DB"] = str(TMP / "kefu.db")
os.environ.setdefault("DEEPSEEK_API_KEY", "")
sys.path.insert(0, r"D:\ai-kefu")

from app import store  # noqa: E402
from app.channels import dispatch as D  # noqa: E402
from app.channels.sim import SimChannel  # noqa: E402
from app import governance as G  # noqa: E402

store.init_db()


class BoomChannel(SimChannel):
    def send(self, msg):
        raise RuntimeError("模拟发送失败")


# 复现：先急停一次（会建 handover），再发 boom 消息
G.set_stop("default", on=True, by="测试", reason="")
ch = SimChannel()
m = ch.parse({"text": "上班时间是几点", "message_id": "stop-001"})
print("  急停消息:", {k: getattr(m, k, None) for k in
                   ("platform", "user_id", "conversation_id", "tenant_id")})
D.accept(m, ch)
D.drain(timeout=60)
G.set_stop("default", on=False, by="测试", reason="")

print("\n  急停后 handover_queue:")
for r in store.rows("SELECT id, platform, user_id, conversation_id, status FROM handover_queue"):
    print("   ", dict(r))

print("\n  human_session:")
for r in store.rows("SELECT * FROM human_session"):
    print("   ", dict(r))

b = BoomChannel()
m2 = b.parse({"text": "上班时间是几点", "message_id": "boom-001"})
print("\n  boom 消息:", {k: getattr(m2, k, None) for k in
                     ("platform", "user_id", "conversation_id", "tenant_id")})

from app import pipeline  # noqa: E402
ho = pipeline._handover_active(m2.tenant_id, m2.platform, m2.user_id, m2.conversation_id)
print("  _handover_active 返回:", ho)

D.accept(m2, b)
st = D.drain(timeout=60)
print("\n  drain 统计:", st)
print("  dispatch_error:", [dict(x) for x in store.rows("SELECT * FROM dispatch_error")])
print("  BoomChannel 收到:", b.texts())
