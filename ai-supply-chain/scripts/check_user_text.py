"""扫描所有面向用户的文案，检查是否泄漏 markdown 记号。

═══════════════════════════════════════════════════════════════════════
【为什么需要这个脚本】
═══════════════════════════════════════════════════════════════════════
    D27 已记录规则："面向用户的文案不许出现 markdown 记号"——
    因为界面**不渲染 markdown**，写 `**加粗**` 会原样显示成星号。

    但这条规则被**反复违反**（报告页、项目 note、知识库 note、用户页都踩过），
    说明"靠人记住"没用，必须**自动化检查**。

    本脚本扫两处（★ 两处都要扫：只扫后端会漏掉前端 JSX 里的硬编码文案）：
      ① 后端：遍历 21 个接口的响应，递归扫描所有字符串（含 POST）
      ② 前端：扫 `frontend/src` 下的 .tsx/.ts 源码，
         检查 JSX 文本与字符串字面量里的 markdown 记号

用法（后端已启动）：
    .venv/Scripts/python.exe scripts/check_user_text.py
退出码：发现泄漏 → 1；干净 → 0
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
#: 需要登录的接口（TASK-019 起绝大多数业务接口都要）
LOGIN = {"username": "admin", "password": "Admin@2026!"}

#: 前端源码根目录（相对本脚本所在项目的根）
FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"

#: 要扫描的 GET 接口（覆盖全部业务页面的文案来源）
#: ★★ 接口清单**自动发现**（TASK-037 起）。
#:
#: 此前这里是一份**手写清单**，于是每次新增接口都要记得加进来 ——
#: 忘了加就漏检。这个坑真实发生过：`/profile/avatar-limits` 的 note 里
#: 写了 `**` 一直没被发现，直到页面断言报"有星号裸露"才暴露。
#:
#: 现在改成从 `/api/openapi.json` 拉全部路径：
#:   · GET 且无路径参数 → 直接请求
#:   · 有路径参数（如 /{id}）→ 跳过（没有通用合法 id 可填），但**打印出来**，
#:     让人知道"这些没覆盖到"，而不是默默漏掉
#:   · 非 GET → 用一份最小的探针 body（见 POST_PROBES）
OPENAPI_PATH = "/openapi.json"

#: 有路径参数的接口需要具体 id 才能请求；这里给出可用的样例 id。
#: ★ 值为 None 表示"确实没有可用的样例"，会被列为未覆盖并打印。
PATH_PARAM_SAMPLES: dict[str, dict[str, object]] = {
    "/api/customers/{customer_id}": {"customer_id": 1863},
    "/api/customers/{customer_id}/events": {"customer_id": 1863},
    "/api/customers/{customer_id}/workspace": {"customer_id": 1863},
    "/api/customers/{customer_id}/messages": {"customer_id": 1863},
    "/api/imports/{batch_id}": {"batch_id": 50},
    "/api/reports/{report_id}": {"report_id": 77},
    "/api/knowledge/documents/{document_id}": {"document_id": 1},
    "/api/researches/{research_id}": {"research_id": 1},
    "/api/proposals/{proposal_id}": {"proposal_id": 1},
}

#: 非 GET 接口的探针 body（只为触发响应、拿文案，不关心业务结果）。
POST_PROBES: dict[str, dict] = {
    "/api/knowledge/analyze": {"question": "资料里说了什么", "top_k": 3},
    "/api/dashboard/ai-ask": {"question": "今天有哪些客户需要我处理？"},
    "/api/reports/generate": {"report_type": "DAILY", "period_start": "2026-10-01", "period_end": "2026-10-01"},
    "/api/researches": {"target_type": "PROSPECT", "target_name": "探针", "source_type": "NEWS",
                        "source_text": "这是一段用于探针的最短合法资料原文，仅用于触发接口以便扫描其返回文案。"},
    "/api/proposals": {"title": "探针方案", "kind": "OTHER", "target_name": "探针对象"},
    "/api/connections": {"kind": "CRM", "name": "探针连接"},
    "/api/customers/{customer_id}/visits": {"channel": "MANUAL", "page": "/probe"},
}

#: markdown 记号。★ 只查真正会在界面上裸露的几种，避免把正常文本误判。
#: `**粗体**` / `__粗体__` / `` `代码` `` / `# 标题`（行首）
PATTERNS = [
    (re.compile(r"\*\*[^*\n]+\*\*"), "**粗体**"),
    (re.compile(r"(?<![a-zA-Z0-9_])__[^_\n]+__(?![a-zA-Z0-9_])"), "__粗体__"),
    (re.compile(r"`[^`\n]+`"), "`代码`"),
    (re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE), "# 标题"),
]

#: ★ 前端专用：**只查 `**粗体**`**。
#:   原因（实测踩过）：JavaScript/TypeScript 里反引号是**模板字符串语法**、
#:   `__xxx__` 常被用作哨兵常量（如 `'__ignore__'`），
#:   把它们当 markdown 会产出 140 处**误报** —— 而误报比不报更糟，
#:   因为使用者会直接忽略整份扫描结果。
#:   所以前端只在"会渲染成文本"的地方查粗体，并排除模板插值与注释。
FRONTEND_PATTERN = re.compile(r"\*\*(?![^`]*\$\{)[^*`\n]+\*\*")

#: ★ 注释剥除：先整体去掉块注释再逐行扫，比"看行首是不是注释"可靠。
#:   踩过的坑：JSX 块注释 `{/* ... */}` 常跨多行，中间那些行**不以注释符号开头**，
#:   用行首启发式会漏掉，导致把注释里的 `**` 报成泄漏（误报）。
BLOCK_COMMENT = re.compile(r"\{/\*.*?\*/\}|/\*.*?\*/", re.DOTALL)

#: 行注释（// 到行尾）。★ 只在非字符串场景剥离风险较大，这里保守只处理整行注释。
LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def call(method: str, path: str, body=None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def walk_strings(obj, path: str = "") -> list[tuple[str, str]]:
    """递归取出所有字符串及其 JSON 路径。"""
    out: list[tuple[str, str]] = []
    if isinstance(obj, str):
        out.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(walk_strings(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(walk_strings(v, f"{path}[{i}]"))
    return out


def main() -> int:
    status, login = call("POST", "/auth/login", LOGIN)
    if status != 200:
        print(f"登录失败（HTTP {status}）：{login}")
        return 2
    token = login["access_token"]

    all_leaks: list[tuple[str, str, str, str]] = []  # (endpoint, path, pattern, text)
    checked = 0
    skipped: list[str] = []

    def scan(label: str, data) -> None:
        for path, text in walk_strings(data):
            for pattern, pat_label in PATTERNS:
                if pattern.search(text):
                    all_leaks.append((label, path, pat_label, text[:110]))

    def fill(path: str) -> str | None:
        """把带 {param} 的路径用样例 id 填成可请求的路径；填不出返回 None。"""
        if "{" not in path:
            return path
        samples = PATH_PARAM_SAMPLES.get(path)
        if not samples:
            return None
        filled = path
        for k, v in samples.items():
            filled = filled.replace("{" + k + "}", str(v))
        return filled if "{" not in filled else None

    # ── ① 自动发现全部接口 ──
    status, spec = call("GET", OPENAPI_PATH)
    if status != 200 or not isinstance(spec, dict):
        print(f"无法读取 {OPENAPI_PATH}（HTTP {status}），回退到最小探针集")
        paths: list[str] = ["/health", "/auth/me", "/dashboard/summary", "/profile/me"]
    else:
        paths = sorted(spec.get("paths", {}).keys())

    for full in paths:
        methods = spec.get("paths", {}).get(full, {}) if isinstance(spec, dict) else {}
        # 去掉 /api 前缀（BASE 已含）
        short = full[len("/api"):] if full.startswith("/api") else full

        # ① GET（无副作用）优先
        if "get" in methods:
            req = fill(short)
            if req is None:
                skipped.append(f"GET {full}（缺样例 id）")
                continue
            status, data = call("GET", req, token=token)
            if status != 200:
                skipped.append(f"GET {full}（HTTP {status}）")
                continue
            checked += 1
            scan(f"GET {full}", data)
            continue

        # ② 非 GET：用探针 body 触发（只为拿文案）
        probe = POST_PROBES.get(full) or POST_PROBES.get(short)
        if probe is None:
            skipped.append(f"{'/'.join(m.upper() for m in methods)} {full}（无探针）")
            continue
        req = fill(short)
        if req is None:
            skipped.append(f"POST {full}（缺样例 id）")
            continue
        status, data = call("POST", req, probe, token=token)
        if status not in (200, 201):
            skipped.append(f"POST {full}（HTTP {status}）")
            continue
        checked += 1
        scan(f"POST {full}", data)

    print(f"扫描了 {checked} 个接口（自动发现自 {OPENAPI_PATH}）")
    if skipped:
        print(f"未覆盖 {len(skipped)} 个（列出来，避免默默漏掉）：")
        for s in skipped:
            print(f"    {s}")
    scanned_files = scan_frontend(all_leaks)
    print(f"扫描了 {scanned_files} 个前端源文件\n")

    if not all_leaks:
        print("OK  所有面向用户的文案都没有 markdown 记号")
        return 0

    print(f"FAIL 发现 {len(all_leaks)} 处 markdown 泄漏：")
    for ep, path, label, text in all_leaks:
        if ep == "frontend":
            print(f"  [{label}] {path}")
            print(f"      行 {text}")
        else:
            print(f"  [{label}] {ep}")
            print(f"      字段: {path}")
            print(f"      文案: {text}")
    print(
        "\n修法：把「**加粗**」去掉或改用中文引号「」——"
        "界面不渲染 markdown，星号会原样显示（见 DECISIONS D27）"
    )
    return 1


def scan_frontend(leaks: list) -> int:
    """扫前端源码里的 markdown 粗体记号。

    ★ 只查 `**粗体**`（见 FRONTEND_PATTERN 的注释：反引号与 __ 在代码里是语法，
      当 markdown 查会大量误报，反而让人不再相信扫描结果）。

    为什么必须扫前端：JSX 里的硬编码文案（说明文字、Tooltip、Alert description）
    也直接显示给用户，后端接口扫描**看不到它们** ——
    实测就是这么漏掉用户页里的两处 `**`。
    """
    if not FRONTEND_SRC.exists():
        return 0

    checked = 0
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in (".tsx", ".ts"):
            continue
        checked += 1
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # ★ 先剥掉块注释，并把剥掉的部分换成等量空行以**保持行号不错位**
        def blank_out(match: re.Match) -> str:
            return "\n" * match.group(0).count("\n")

        cleaned = BLOCK_COMMENT.sub(blank_out, raw)
        cleaned = LINE_COMMENT.sub("", cleaned)

        rel = path.relative_to(FRONTEND_SRC.parents[1])
        for lineno, line in enumerate(cleaned.splitlines(), 1):
            if FRONTEND_PATTERN.search(line):
                leaks.append(("frontend", f"{rel}:{lineno}", "**粗体**", line.strip()[:110]))
    return checked


if __name__ == "__main__":
    raise SystemExit(main())
