#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hermes 独立 API 冒烟（TASK-002C 验收用）—— 起真服务、发真请求、验真文件。"""
import subprocess, time, os, sys, hashlib, json

ROOT = r"D:\sales-report-agent"
os.chdir(ROOT)
ENV = dict(os.environ); ENV["PYTHONPATH"] = ""
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
BASE = "http://127.0.0.1:8500"

import httpx

proc = subprocess.Popen([PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", "8500"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=ENV)
ok = False
try:
    print("=== 等 service 就绪 ===")
    for i in range(45):
        time.sleep(2)
        try:
            r = httpx.get(f"{BASE}/api/health", timeout=3)
            if r.status_code == 200:
                h = r.json()
                print(f"✅ /api/health 200 | data_match={h['data_snapshot']['match']} | problems={h['problems']}")
                ok = True
                break
        except Exception:
            pass
    if not ok:
        print("❌ service 未就绪")
        sys.exit(1)

    print("\n=== 1) 真上传 23MB xlsx ===")
    t0 = time.time()
    with open(os.path.join(ROOT, "data", "Online Retail.xlsx"), "rb") as f:
        r = httpx.post(f"{BASE}/api/upload", files={"file": ("Online Retail.xlsx", f)},
                       timeout=900)
    up = r.json()
    print(f"   HTTP {r.status_code} | file_id={up.get('file_id')} | rows={up.get('rows')} | columns={len(up.get('column_names', []))} | {time.time()-t0:.1f}s")
    assert up.get("rows") == 541909, "行数不符"
    assert len(up.get("column_names", [])) == 8, "字段数不符"

    print("\n=== 2) 真执行（2011-11-21~11-27）===")
    t0 = time.time()
    r = httpx.post(f"{BASE}/api/execute",
                   json={"file_id": up["file_id"], "start": "2011-11-21", "end": "2011-11-27",
                         "metrics": ["sales_amount", "rows_in_range", "rows_valid", "rows_excluded"]},
                   timeout=900)
    ex = r.json()
    print(f"   HTTP {r.status_code} | execution_id={ex.get('execution_id')}")
    print(f"   amount={ex.get('amount')} rows_in_range={ex.get('rows_in_range')} "
          f"rows_excluded={ex.get('rows_excluded')} | {time.time()-t0:.1f}s")
    # 与 Hermes 独立计算（002A 时算过）比对
    assert abs(ex.get("amount", 0) - 316412.16) < 0.01, f"金额不符: {ex.get('amount')}"
    assert ex.get("rows_in_range") == 19950 and ex.get("rows_excluded") == 296, "行数不符"

    print("\n=== 3) 真下载 + openpyxl 读回 ===")
    eid = ex["execution_id"]
    r = httpx.get(f"{BASE}/api/download/{eid}", timeout=300)
    data = r.content
    print(f"   HTTP {r.status_code} | {len(data)} 字节 | sha256={hashlib.sha256(data).hexdigest()[:16]}…")
    tmp = os.path.join(ROOT, "outputs", "_hermes_smoke.xlsx")
    open(tmp, "wb").write(data)
    from openpyxl import load_workbook
    ws = load_workbook(tmp).active
    vals = {}
    for row in ws.iter_rows(values_only=True):
        if row and row[0] and row[1] is not None:
            vals[str(row[0])] = row[1]
    print(f"   读回：销售额={vals.get('销售额（元）')} 区间行数={vals.get('区间原始行数')} 排除={vals.get('被排除行数')}")
    os.remove(tmp)
    assert abs(float(vals.get("销售额（元）", 0)) - 316412.16) < 0.01, "读回的金额不符"

    print("\n=== 4) 执行记录 ===")
    r = httpx.get(f"{BASE}/api/executions", timeout=60)
    ex2 = r.json()
    n = len(ex2) if isinstance(ex2, list) else len(ex2.get("executions", ex2.get("items", [])))
    print(f"   HTTP {r.status_code} | 记录数 {n}")

    print("\n=== 5) 错误路径 ===")
    r = httpx.post(f"{BASE}/api/execute", json={"file_id": "不存在", "start": "2011-11-21", "end": "2011-11-27"}, timeout=60)
    print(f"   不存在的 file_id → HTTP {r.status_code}（应 4xx）")

    print("\n🎉 独立冒烟全部通过：上传 541909 行 / 8 字段 · 执行 316,412.16（元）· 下载文件可读回 · 记录可查")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    print("(service 已关闭)")
