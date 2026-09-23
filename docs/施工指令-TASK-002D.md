# 施工指令 · TASK-002D（任务层最小实现）· 回溯补记

> ⚠️ **本文件的来历**：TASK-002D 是 R005 评审期间**临时插入**的任务（004A 报 BLOCKED → 老板拍板"走方案 a：先补任务层"），
> 派发时**没有**正式指令文件。按铁律 0"每个 TASK 必须有 Scope / Out of Scope / Acceptance Criteria"，
> 施工完成后由 Claude Code **回溯补记**本文件，把当时的实际范围与验收标准固化下来。
> **规范性来源**（以这两份为准，本文件若与它们冲突，以它们为准）：
> - `D:\GPT_Project_Reviews\reviews\005-task-layer-review.md`（评审原文，APPROVED_WITH_CONDITIONS + 12 项 required_changes）
> - `D:\GPT_Project_Reviews\decisions\003-task-layer.md`（裁决 + 用户拍板）
> 项目侧对应决策见 `docs/DECISIONS.md` 的 **D19**。

## Objective

补齐"任务"这一层，让前端（004A）能真走完"上传 → 建任务 → 看 Spec → 执行 → 下载 → 看记录"的十步闭环，
**不靠造假**：002C 只有 ad-hoc 的 `/api/execute`（临时区间），没有任务实体、没有后端 Spec 来源、
执行记录里没有 `task_id` —— 前端要做十步闭环只能自己编，所以 004A 报 BLOCKED。本 TASK 把这三处补上。

## Background

- 铁律 3：**首次理解 → 人工确认 → 固化成任务**，之后执行不再调 LLM（D9）。
- 铁律 5 / D10：失败不产出文档；失败也要**留痕**（执行记录里 `status=failed`）。
- D11：执行记录必须绑定 spec 版本 / 数据快照 / 模板 / 代码版本 / execution_id / 校验结果。

## Scope

1. **5 个任务端点**：`POST /api/tasks`、`GET /api/tasks`、`GET /api/tasks/{task_id}`、
   `POST /api/tasks/{task_id}/run`、`GET /api/tasks/{task_id}/runs`。
2. **任务落盘** `state/tasks.json`（`schema_version` + 冻结的 Spec 快照），复用 `app/state.py` 既有读写与锁。
3. **`time_range` schema 定形**：`{mode, start?, end?, tz?}`，`mode ∈ {absolute, last_week, last_month}`，
   **只实现 `absolute`**；相对模式**明确报错**（`relative_time_not_implemented`），不猜（铁律 4）。
4. **建任务两条入参路径**：便捷路径 `{file_id, start, end, metrics}` 与完整 Spec `{file_id, spec}`，
   产出**同一 shape** 的 Spec 快照。
5. **执行记录扩展**：`task_id` / `spec_id` / `spec_version` / `resolved_range` / `base_date`；
   成功与失败记录的**字段集合对齐**。
6. **统一错误体** `{error:{code,message,detail}}`（**保留顶层 `detail`** 兼容 002C 契约）+ 全局异常处理器。
7. **抽公共执行管线** `_execute_and_record()`，供 `/api/execute` 与 run 共用（**不重写算数与渲染**，D4/D5）。
8. **文档**：`docs/TASKS.md` / `docs/DECISIONS.md`（D19）/ 本文件。

## Out of Scope（**一律不做**，只记录）

- ❌ LLM 解析（`/api/parse`、`/api/spec/{id}` → **TASK-005**）—— 本 TASK **不伪造** AI 解析结果。
- ❌ 定时调度 / MySQL 落库 / 任务锁（**TASK-007**）；相对时间的**解析实现**（本 TASK 只冻结 schema）。
- ❌ 多用户与权限、CORS、任务编辑与 spec 版本 bump 的 UI。
- ❌ 任意上传文件取数（需改造 002A，已上报待裁决）。
- ❌ 前端（**004A**）。
- ❌ 改动 002A/002B 的计算与渲染逻辑；改动既有 6 个 `/api/*` 端点的语义与契约。

## Dependencies

| 依赖 | 用途 | 状态 |
|---|---|---|
| fastapi / uvicorn / pydantic / python-multipart / httpx | 接口层与测试 | ✅ 已批准（D17-1 / D18） |
| **无新增第三方依赖** | —— | 本次未引入任何新库 |

## Acceptance Criteria

- **AC-01** `/api/health` 报 `version=0.2.0`、`task=TASK-002D`，且 `state.tasks` 计数可读。
- **AC-02** 真上传 `data/Online Retail.xlsx`（541909 行 / 8 列）后能建任务，返回**真实 `task_id`** 与冻结 Spec；
  数据与快照一致时**无 `warnings`**；不一致时**能建**但带警告（拦截留到执行时）。
- **AC-03** `GET /api/tasks/{task_id}` 重读出的 Spec 与建任务时**逐字段一致**（前端刷新后不靠内存变量）。
- **AC-04** `POST /api/tasks/{task_id}/run` 真执行：`amount=316412.16`、`rows_in_range=19950`、
  `rows_excluded=296`、`resolved_range={start:2011-11-21,end:2011-11-27}`、有 `verification`、有 `code_version`。
- **AC-05** `download_url`（**相对路径**）能下到真 xlsx，openpyxl 读回金额 316412.16 / 行数 19950，
  且 B4 数字格式仍是 `#,##0.00`（D17-2 / D5）。
- **AC-06** 新端点响应**不含服务器绝对路径**（无 `excel_path`，`template` 为相对路径）。
- **AC-07** 数据不是冻结快照时：run **被拒**（`data_snapshot_mismatch`）+ **落 failed run**
  （`status=failed`、无 `output_rel_path`、无 `download_url`、**磁盘无新产出文件**）+ task 状态**不变**（D10）。
- **AC-08** 状态机 `created → has_run`（跑成功后）；**失败不改** task 状态；`run_count` 由执行记录现算。
- **AC-09** `GET /api/tasks` 分页（`limit`/`offset`/`count`/`total`）+ 每条带 `spec_summary` 与执行统计。
- **AC-10** 统一错误体：新端点与老端点都返回 `{error:{code,message,...}}`，**老端点仍保留顶层 `detail`**。
- **AC-11** 既有契约不动：`tests/test_api.py` 18 条 + 002A/002B 的测试**全绿**；`/api/execute` 的外部响应不变。
- **AC-12** ★ **值必须来自前一步响应**：本 TASK 的所有验证（含 `scripts/task_smoke.py`）中，
  `task_id` / `execution_id` / `download_url` / `spec` 一律取自**上一步 HTTP 响应体**，
  不得硬编码、不得读前端的常量（R005 条件 #12）。

## Verification Commands

```bash
# 1) 任务层测试（26 条）
.venv/Scripts/python.exe -m pytest tests/test_tasks.py -q

# 2) 全量回归（002A+002B+002C+002D，既有 36 条必须继续全绿）
.venv/Scripts/python.exe -m pytest -q

# 3) ★ 真服务冒烟（起真 uvicorn、发真 HTTP、真下载并用 openpyxl 读回）
.venv/Scripts/python.exe scripts/task_smoke.py
#    （跑在 outputs/ 下的临时沙箱里，不污染 state/*.json；脚本会打印沙箱路径）

# 4) 起服务给人肉走查（004A 用）
.venv/Scripts/python.exe scripts/serve.py --port 8500
```

> `curl` 在本机权限系统里被拒（安全策略）→ 取证统一用 **httpx** 打真服务；
> 用户如想自己对照，可在输入框用 `! curl ...` 手动跑。
