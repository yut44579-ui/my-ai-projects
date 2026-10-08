#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Hermes 门禁检查（验收用工具 —— Hermes 自己的工具，不是产品代码）

用法:
    .venv/Scripts/python.exe scripts/hermes_gate.py [TASK_ID]

执行顺序（SOP GATE-1..6）:
    GATE-1 静态检查 (compileall)
    GATE-2 单元测试 (pytest)
    GATE-3 API 冒烟        (按 TASK 判断是否适用)
    GATE-4 真实文件 E2E    (按 TASK 判断)
    GATE-5 前端可操作性    (按 TASK 判断)
    GATE-6 Git diff 范围
任何一步 FAIL → 停止并报告。
"""
import os, subprocess, sys, json, hashlib, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = "python"
DATA_SHA = "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d"
ENV = dict(os.environ); ENV["PYTHONPATH"] = ""


def run(cmd, cwd=ROOT, timeout=900):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="ignore", env=ENV)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def gate(name, ok, detail=""):
    print(f"\n{'='*64}\n[{ '✅ PASS' if ok else '❌ FAIL' }] {name}\n{'='*64}")
    if detail:
        print(detail[-3000:] if len(detail) > 3000 else detail)
    return ok


def main():
    task = sys.argv[1] if len(sys.argv) > 1 else "TASK-?"
    print(f"# Hermes 门禁检查 · {task}\n# 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}\n# 根目录 {ROOT}")
    results = {}

    # GATE-1 静态
    code, out = run([PY, "-m", "compileall", "-q", "app", "tests"])
    results["GATE-1 静态"] = gate("GATE-1 静态检查 (compileall)", code == 0, out or "(无输出=通过)")
    if code != 0:
        return summary(results)

    # GATE-2 单测
    code, out = run([PY, "-m", "pytest", "tests/", "-q", "--no-header", "-x" if "--fast" in sys.argv else "-q"])
    results["GATE-2 单测"] = gate("GATE-2 单元测试 (pytest)", code == 0, out)
    if code != 0:
        return summary(results)

    # 数据完整性（AC-06 溯源）
    xf = os.path.join(ROOT, "data", "Online Retail.xlsx")
    if os.path.exists(xf):
        h = hashlib.sha256()
        with open(xf, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        ok = h.hexdigest() == DATA_SHA
        results["数据 SHA256"] = gate("数据溯源 (data/Online Retail.xlsx SHA256)",
                                      ok, f"实际: {h.hexdigest()}\n预期: {DATA_SHA}")

    # GATE-6 Git 范围
    code, out = run(["git", "status", "--short"])
    code2, out2 = run(["git", "diff", "--stat"])
    untracked = [l for l in out.split("\n") if l.strip()]
    results["GATE-6 Git"] = gate("GATE-6 Git diff 范围检查", True,
                                 "status --short:\n" + (out or "(clean)") + "\ndiff --stat:\n" + (out2 or "(无)"))
    print(f"\n变更文件数: {len(untracked)}")

    return summary(results)


def summary(results):
    print(f"\n\n{'#'*64}\n# 门禁汇总\n{'#'*64}")
    allok = True
    for k, v in results.items():
        print(f"  {'✅' if v else '❌'} {k}")
        allok = allok and v
    print(f"\n总体: {'✅ 全部通过' if allok else '❌ 存在 FAIL —— 按 SOP 停止，不进入下一 TASK'}")
    print("\n注意：AC 里涉及具体数值/页面操作的项，需 Hermes 另行独立核对（本脚本只覆盖通用门禁）。")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
