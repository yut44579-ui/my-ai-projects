#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
无人值守 TASK 流水线（Hermes 的自动化驱动器）

做四件事，不需要人介入：
  1. 派 claude -p 执行 TASK（读施工指令）
  2. 等它跑完
  3. 跑 Hermes 门禁（scripts/hermes_gate.py）+ 检查 Claude 是否报 FAIL/BLOCKED
  4. 门禁过 → git commit → 下一个 TASK；门禁不过 → 停止并写 FAILED 标记

用法: python scripts/auto_pipeline.py
状态文件: D:\\Hermes\\cache\\pipeline_state.json（可随时查看进度）
日志:     D:\\Hermes\\cache\\pipeline.log
"""
import subprocess, os, sys, json, time, re

ROOT = r"D:\sales-report-agent"
CACHE = r"D:\Hermes\cache"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
STATE = os.path.join(CACHE, "pipeline_state.json")
LOG = os.path.join(CACHE, "pipeline.log")

TASKS = [
    ("TASK-002C", "docs/施工指令-TASK-002C.md", "feat: TASK-002C minimal API (upload/schema/execute/download/executions)"),
    ("TASK-004A", "docs/施工指令-TASK-004A.md", "feat: TASK-004A real frontend (5 sections, real API, no mock)"),
]


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save_state(**kw):
    st = {}
    if os.path.exists(STATE):
        try:
            st = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            st = {}
    st.update(kw)
    st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def run(cmd, timeout=3600, shell=True, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "1000000"
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(cmd, shell=shell, cwd=ROOT, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="ignore", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    log("=" * 60)
    log("流水线启动")
    save_state(status="running", started=time.strftime("%Y-%m-%d %H:%M:%S"), current=None,
               completed=[], failed=None)

    completed = []
    for task_id, instr, commit_msg in TASKS:
        log(f"▶ 开始 {task_id}")
        save_state(current=task_id, status="running")

        prompt = (f"请读取 D:/sales-report-agent/{instr} 全文，严格按其中 Scope / Out of Scope / "
                  f"Acceptance Criteria 实现并验证。只做 {task_id}，不要扩大范围。完成后按报告格式输出。")
        log(f"  派 claude -p …")
        rc, out = run(f'claude -p "{prompt}" --dangerously-skip-permissions',
                      timeout=5400, env_extra=None)
        with open(os.path.join(CACHE, f"{task_id}.log"), "w", encoding="utf-8") as f:
            f.write(out)
        log(f"  claude 退出码 {rc}，输出 {len(out)} 字符 → {CACHE}\\{task_id}.log")

        # Claude 自述失败 → 停
        blob = (out or "").upper()
        if "STATUS: FAIL" in blob or "STATUS: BLOCKED" in blob or "## STATUS\nFAIL" in blob:
            log(f"  ❌ Claude 自述 FAIL/BLOCKED → 停止流水线")
            save_state(status="stopped", failed=task_id, reason="claude reported FAIL/BLOCKED",
                       completed=completed)
            return 1

        # 门禁
        log(f"  跑门禁 …")
        rc2, out2 = run([PY, "scripts/hermes_gate.py", task_id], shell=False, timeout=3600)
        log(f"  门禁退出码 {rc2}")
        with open(os.path.join(CACHE, f"{task_id}.gate.log"), "w", encoding="utf-8") as f:
            f.write(out2)
        if rc2 != 0:
            log(f"  ❌ 门禁未通过 → 停止（详见 {CACHE}\\{task_id}.gate.log）")
            save_state(status="stopped", failed=task_id, reason="gate failed",
                       completed=completed)
            return 1

        # commit
        run("git add -A", shell=True)
        rc3, out3 = run(f'git -c user.name=hermes-dev -c user.email=dev@local commit -m "{commit_msg}"',
                        shell=True)
        m = re.search(r"\[master ([0-9a-f]+)\]", out3 or "")
        sha = m.group(1) if m else "(no new commit)"
        log(f"  ✅ {task_id} 完成，commit {sha}")
        completed.append({"task": task_id, "commit": sha})
        save_state(completed=completed)

    log("🎉 全部 TASK 完成")
    save_state(status="done", completed=completed, current=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
