"""冒烟测试 9：全项目护栏。

ALLOW_FORBIDDEN_PATTERNS
★ 这个标记表示"本文件为了检查而必须包含这些字符串"，扫描时跳过它。

★ 这个文件的存在理由是一次**重复犯错**：
    用户明确说过"名字不要叫小笋供应链，叫 AI客服"，
    我改了一处，过几轮又在新写的页面里用回了旧名字。

  ★ 教训：
    **口头约定和一次性修改都靠不住。**
    "不许出现某个名字"这种事必须变成**可执行的断言**，
    每次跑测试都检查一遍 —— 否则它一定会复发。

  这个文件还顺便兜住几条最容易复发的纪律：
    ① 不许再出现旧产品名
    ② 不许把 API Key / 密码写进代码
    ③ 每个界面都必须有子路径前缀（否则部署到子路径就 404）
    ④ 每个 <script> 块 JS 语法必须能解析（否则页面白屏）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(r"D:\ai-kefu")
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


def src_files():
    """所有会进仓库 / 上服务器的文本文件。

    ★ 排除这个文件自己 —— 检查器**必须**包含被禁的字符串才能检查它们，
      否则会自己举报自己（第一版就是这样，4 处误报）。
      这类"自指"问题在任何扫描器里都会遇到，处理方式是显式排除，
      而不是把禁令放松。
    """
    out = []
    me = Path(__file__).resolve()
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if p.resolve() == me:
            continue
        s = str(p)
        if any(x in s for x in (".venv", "__pycache__", "\\.git", "\\data\\", "\\docs\\screenshots")):
            continue
        # 任何带这个标记的文件都跳过（用于将来的检查器）
        if p.suffix.lower() in (".py", ".mjs", ".js"):
            try:
                if "ALLOW_FORBIDDEN_PATTERNS" in p.read_text(encoding="utf-8", errors="ignore"):
                    continue
            except OSError:
                pass
        if p.suffix.lower() not in (".py", ".html", ".js", ".mjs", ".css", ".md", ".txt", ".json"):
            continue
        out.append(p)
    return out


FILES = src_files()
print(f"扫描 {len(FILES)} 个文件\n")
print("=" * 64)
print("冒烟测试 9 · 全项目护栏")
print("=" * 64)

# ══════════════════════════════════════════════════════════════════════
# ① ★★ 旧产品名不许再出现
# ══════════════════════════════════════════════════════════════════════
print("\n[1] ★★ 旧产品名不许再出现")
# ★ 把"曾经的错误"写成禁止清单，而不是靠记性。
FORBIDDEN_NAMES = ["小笋", "小笋供应链"]
hits = []
for p in FILES:
    # 决策记录里会引用这件事本身（记录"曾经叫过什么"是有意义的），跳过
    if p.name.startswith("决策记录"):
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    for name in FORBIDDEN_NAMES:
        for i, line in enumerate(text.splitlines(), 1):
            if name in line:
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:70]}")
if hits:
    for h in hits:
        print(f"       {h}")
check("★★ 代码和界面里没有旧产品名", not hits, f"{len(hits)} 处")

# 产品名应该是「AI客服」
has_name = 0
for p in FILES:
    if p.suffix == ".html":
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "AI客服" in t:
            has_name += 1
check("界面上用的是「AI客服」", has_name >= 2, f"{has_name} 个界面")

# ── 2. 不许把密钥写进代码 ─────────────────────────────────────────────
print("\n[2] 不许把密钥写进代码（这个项目里踩过三次）")
# ★ 只保留"形状"规则（这些是通用的，写死没关系）
SECRETS = [
    (r"sk-[A-Za-z0-9]{16,}", "厂商 API Key"),
    (r"AUTH_SECRET_KEY\s*=\s*['\"]?\w{16,}", "认证密钥"),
]
# ★★ 具体口令**从仓库外的文件读**。
#    为什么：这个测试要检查"代码里没有这些字符串"，
#    就得知道它们是什么 —— 但如果把字面量写在这里，
#    **护栏本身就成了密钥的藏身处**，而且藏在一个"看起来在防密钥"的文件里。
#    所以：真实值放 .forbidden（被 gitignore），读不到就跳过并警告。
_FORBIDDEN_FILE = ROOT / ".forbidden"
_local = []
if _FORBIDDEN_FILE.exists():
    for _line in _FORBIDDEN_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#"):
            _local.append((re.escape(_line), "本机禁止出现的口令/密钥"))
    print(f"  已从 .forbidden 载入 {len(_local)} 条本机禁止串")
else:
    print("  ⚠ 没有 .forbidden —— 跳过「本机口令」这两条检查")
    print("    （这是**有意的**：具体口令不该写进仓库。要完整检查就本地建一个。）")
SECRETS = SECRETS + _local
found = []
for p in FILES:
    t = p.read_text(encoding="utf-8", errors="ignore")
    for pat, label in SECRETS:
        for m in re.finditer(pat, t):
            ln = t[: m.start()].count("\n") + 1
            found.append(f"{p.relative_to(ROOT)}:{ln} [{label}]")
if found:
    for f in found[:8]:
        print(f"       {f}")
check("代码里没有密钥", not found, f"{len(found)} 处")

# ── 3. 界面必须有子路径前缀 ───────────────────────────────────────────
print("\n[3] ★ 每个界面都必须处理子路径（否则部署到 /demo/kefu/ 就 404）")
for p in sorted((ROOT / "web").glob("*.html")):
    t = p.read_text(encoding="utf-8", errors="ignore")
    # ★ 纯跳转页（不调任何 API）不需要前缀函数
    if "/api" not in t:
        continue
    # ★ 两种前缀写法都认：
    #   window.__BASE__  —— 控制台（还有标签页、详情等多个模块共用）
    #   const BASE       —— 官网 / 挂件（单文件，只需要一个前缀）
    has_base = ("window.__BASE__" in t) or ("const BASE = new URL" in t)
    # 数一下有没有"没走 API() 的 fetch('/api"
    bad = re.findall(r"fetch\(\s*['\"`]/api", t)
    check(f"{p.name}: 有前缀函数", has_base, "缺 window.__BASE__")
    check(f"{p.name}: 没有漏掉前缀的 fetch", not bad, f"{len(bad)} 处")

# ── 4. 界面 JS 语法 ───────────────────────────────────────────────────
print("\n[4] ★ 界面 JS 语法（括号被替换弄坏过一次，整个界面白屏）")
try:
    import subprocess

    r = subprocess.run(
        ["node", str(ROOT / "scripts" / "check_js_syntax.mjs")],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    ok = r.returncode == 0
    check("三个界面的 <script> 都能解析", ok, (r.stdout or "")[-200:])
except FileNotFoundError:
    print("       （没装 node，跳过）")
except Exception as exc:  # noqa: BLE001
    check("三个界面的 <script> 都能解析", False, str(exc))

# ── 5. 平台强制底线还在 ───────────────────────────────────────────────
print("\n[5] ★ 平台强制的安全底线（客户不可关闭）")
policy_src = (ROOT / "app" / "policy.py").read_text(encoding="utf-8")
for key, label in [
    ("ESCALATE_INTENTS", "报价/合同/退款/投诉 代码层拦"),
    ("HANDOVER_LEAK", "交接语境的身份泄漏"),
    ("COMMITMENT", "越权承诺"),
    ("check_outbound", "出站检查"),
]:
    check(f"{label} 还在", key in policy_src)

gov = (ROOT / "app" / "governance.py").read_text(encoding="utf-8")
for key, label in [
    ("is_stopped", "急停"),
    ("is_shadow", "影子模式"),
    ("preview", "后果预览"),
]:
    check(f"{label} 还在", key in gov)

# ── 6. 学习闭环的关卡还在 ─────────────────────────────────────────────
print("\n[6] ★ 学习闭环的验证关卡（没有它「越用越聪明」会变成「越用越偏」）")
ev = (ROOT / "app" / "learning" / "eval.py").read_text(encoding="utf-8")
for key, label in [
    ("validate_change", "改动验证"),
    ("promote_pending", "批量过关卡"),
    ("seed_from_log", "评测集自动生长"),
    ("_as_list", "格式容错（假通过比失败危险）"),
]:
    check(f"{label} 还在", key in ev)

# ── 7. 学习产物默认不生效 ─────────────────────────────────────────────
print("\n[7] ★ 学到的东西默认不生效，要过评测才生效")
loop_src = (ROOT / "app" / "learning" / "loop.py").read_text(encoding="utf-8")
check("取风格示例时过滤 status='active'", "status='active'" in loop_src)
check("取同义词时过滤 status='active'", loop_src.count("status='active'") >= 2)
store_src = (ROOT / "app" / "store.py").read_text(encoding="utf-8")
check("表定义里默认是 pending", "DEFAULT 'pending'" in store_src)

print("\n" + "=" * 64)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 64)
sys.exit(1 if FAIL else 0)
