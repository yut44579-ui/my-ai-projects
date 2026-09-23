"""tests/test_tasks.py · TASK-002D 验收测试（任务层：建任务 / 看 Spec / 执行 / 执行历史）。

════════════════════════════════════════════════════════════════════════
【本文件的验证思路 —— 为什么这样测才算数】
════════════════════════════════════════════════════════════════════════
被测：app/api.py 的 5 个任务端点 + app/state.py 的任务落盘 + app/spec/models.py 的 time_range 结构

任务层最容易测成"自己造 ID、自己读自己"（前端最怕的就是 task_id 是假的）。本文件刻意避开：

  ① **task_id 只能来自后端**：所有断言里的 task_id / spec / download_url 都取自**前一步响应**的字段，
     没有任何一处手写 ID 或手写 Spec（AC 钉死："禁止硬编码 Spec 或复用 execution_id 冒充 task_id"）。
  ② **真计算**：run 出来的 316,412.16 / 19,950 行来自已被独立 Oracle 验过的 002A（docs/TASKS.md），
     不是本文件自己算的。run 走的是"任务里冻结的 Spec"，串错了这里就会红。
  ③ **真下载**：用 run 返回的 download_url 把文件取回来，用 openpyxl **打开读格子**，
     走的是"用户点下载后打开文件"这条真实路径，而不是看 JSON 自称多少。
  ④ **失败必须留痕**：数据哈希不符时，run 要拒绝 + 落一条 status=failed 的 run 记录 + 不出文件
     + 不返回 download_url（R005 required_change #4）—— 这条是"宁可不产出也不错算"（D10）的落地。
  ⑤ **刷新可重读**：GET /api/tasks/{task_id} 读回来的 Spec 必须与建任务时的一致（前端刷新后靠它）。
  ⑥ **既有端点没被动过**：002C 的 18 条测试在 tests/test_api.py 里照跑（本文件不重复），
     另加一条"统一错误体不破坏旧契约"的断言（顶层 detail 必须原样还在）。

隔离：三个环境变量（SRA_STATE_DIR / SRA_UPLOAD_DIR / SRA_OUTPUT_DIR）指向 pytest 临时目录，
所以测试**不会**往仓库的 state/、data/uploads/、outputs/ 里写东西。

口径依据：docs/DECISIONS.md 的 D4 / D5 / D9 / D10 / D11 / D12 / D13 / D16 / D17-2，
以及外部评审 R005（`D:\\GPT_Project_Reviews\\reviews\\005-task-layer-review.md`）的 12 项 required_changes。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from io import BytesIO

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402

from app import state  # noqa: E402
from app.api import app  # noqa: E402
from app.engine import loader  # noqa: E402

# ── 与 002A/002B/002C 同一份真实数据、同一周（便于交叉核对）───────────────
DATA_PATH = PROJECT_ROOT / "data" / "Online Retail.xlsx"
START = "2011-11-21"
END = "2011-11-27"
EXPECTED_ROWS_IN_RANGE = 19950          # 来自 002A（独立 Oracle 验过）
EXPECTED_AMOUNT = 316412.16

ALL_METRICS = [
    "sales_amount",
    "rows_in_range",
    "rows_valid",
    "rows_excluded",
    "excluded_amount",
    "valid_qty_sum",
]

client = TestClient(app)


def banner(title: str) -> None:
    print(f"\n{'─' * 78}\n▶ {title}\n{'─' * 78}")


# ════════════════════════════════════════════════════════════════════════
# 测试环境隔离 + 共用 fixture（真上传/真执行都很慢，模块内只做一次）
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module", autouse=True)
def isolated_dirs(tmp_path_factory):
    """把状态/上传/产出三个目录指到 pytest 临时目录，测试不污染仓库。"""
    root = tmp_path_factory.mktemp("task_state")
    keys = ("SRA_STATE_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["SRA_STATE_DIR"] = str(root / "state")
    os.environ["SRA_UPLOAD_DIR"] = str(root / "uploads")
    os.environ["SRA_OUTPUT_DIR"] = str(root / "outputs")
    try:
        yield root
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="module")
def uploaded(isolated_dirs) -> dict:
    """真上传 data/Online Retail.xlsx（54 万行），模块内只做一次。"""
    with open(DATA_PATH, "rb") as handle:
        response = client.post(
            "/api/upload",
            files={
                "file": (
                    "Online Retail.xlsx",
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def created_task(uploaded) -> dict:
    """建一个任务（便捷路径）——**只固化不执行**，所以很快。"""
    response = client.post(
        "/api/tasks",
        json={
            "file_id": uploaded["file_id"],
            "start": START,
            "end": END,
            "metrics": ALL_METRICS,
            "name": "验收用·上周销售汇总",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def ran_task(created_task) -> dict:
    """按任务执行一次（真算 + 真渲染，很慢）—— run 的响应由各测试断言。"""
    response = client.post(f"/api/tasks/{created_task['task_id']}/run")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def foreign_task(tmp_path_factory) -> dict:
    """建一个**绑定了非冻结快照文件**的任务：用来验证"执行时被拒绝且留痕"（R005 #4）。

    造法同 002C 的 test_execute_rejects_foreign_data_file：取真实文件的前 200 行、
    把最后一行的数字清零后另存 → 长得像但不是同一份字节（哈希必然不同）。
    """
    tmp = tmp_path_factory.mktemp("foreign")
    other = tmp / "other.xlsx"
    workbook = load_workbook(DATA_PATH, read_only=True)
    rows = []
    for index, row in enumerate(workbook.active.iter_rows(values_only=True), start=1):
        rows.append(row)
        if index >= 200:
            break
    workbook.close()
    rows[-1] = tuple(0 if isinstance(value, (int, float)) else value for value in rows[-1])

    small = Workbook()
    for row in rows:
        small.active.append(list(row))
    small.save(other)

    upload = client.post("/api/upload", files={"file": ("other.xlsx", other.read_bytes(), "application/vnd.ms-excel")})
    assert upload.status_code == 201, upload.text
    response = client.post(
        "/api/tasks",
        json={
            "file_id": upload.json()["file_id"],
            "start": START,
            "end": END,
            "metrics": ["sales_amount"],
            "name": "验收用·非快照数据（应当被拒绝）",
        },
    )
    assert response.status_code == 201, response.text     # 建任务不拦（拦在执行时，且要留痕）
    return response.json()


# ════════════════════════════════════════════════════════════════════════
# ① 建任务（POST /api/tasks）
# ════════════════════════════════════════════════════════════════════════
def test_create_task_convenience_path_shape(created_task, uploaded):
    """便捷路径建任务：返回 task_id + **完整冻结 Spec**（③ 区块的真数据源）。"""
    banner("POST /api/tasks（便捷路径）")
    body = created_task
    print(json.dumps({k: v for k, v in body.items() if k != "spec"}, ensure_ascii=False, indent=2))
    print("\n冻结的 Spec：")
    print(json.dumps(body["spec"], ensure_ascii=False, indent=2))

    assert body["task_id"].startswith("t_"), f"task_id 应由后端生成（t_ 前缀）：{body['task_id']}"
    assert body["status"] == "created"
    assert body["file_id"] == uploaded["file_id"]
    assert body["data_sha256"] == loader.EXPECTED_SHA256      # 绑定的就是冻结快照那份数据
    assert body["data_snapshot_match"] is True
    assert "warnings" not in body

    spec = body["spec"]
    assert spec["spec_id"] == f"task-{body['task_id']}"       # Spec 归属于该任务
    assert spec["time_range"] == {
        "mode": "absolute",
        "start": START,
        "end": END,
        "tz": "Asia/Shanghai",
        "time_field": "InvoiceDate",
        "semantics": "含首尾全天（闭区间 start 00:00:00 ~ end 23:59:59.999）",
    }
    assert [metric["name"] for metric in spec["metrics"]] == ALL_METRICS
    assert spec["output"]["cells"] == {
        "sales_amount": "B4", "rows_in_range": "B5", "rows_valid": "B6",
        "rows_excluded": "B7", "excluded_amount": "B8", "valid_qty_sum": "B9",
    }
    # 建任务**不执行**：还没有 run，也没有产出
    assert body["run_count"] == 0 and body["last_run_at"] is None
    assert list(state.output_dir().glob("*.xlsx")) == []


def test_create_task_full_spec_path_matches_convenience(uploaded, created_task):
    """完整 Spec 路径：**把上一步后端返回的 Spec 原样提交**，应产出同 shape 的快照（R005 #3）。

    这条同时证明了"任务层不是前端私有协议"——TASK-005/007 可以拿一份现成 Spec 直接落成任务。
    """
    submitted = json.loads(json.dumps(created_task["spec"]))     # 深拷贝后端给的 Spec
    submitted["spec_id"] = "submitted-from-client"
    submitted["version"] = 3
    response = client.post(
        "/api/tasks",
        json={"file_id": uploaded["file_id"], "spec": submitted, "name": "验收用·由完整 Spec 建的任务"},
    )

    banner("POST /api/tasks（完整 Spec 路径）")
    print(f"HTTP {response.status_code}")
    assert response.status_code == 201, response.text
    body = response.json()
    print(json.dumps({k: v for k, v in body.items() if k != "spec"}, ensure_ascii=False, indent=2))

    assert body["task_id"] != created_task["task_id"]
    assert body["spec"]["spec_id"] == "submitted-from-client"     # 客户端的 spec_id 被尊重（D11 版本标识）
    assert body["spec"]["version"] == 3
    assert body["spec"]["metrics"] == created_task["spec"]["metrics"]
    assert body["spec"]["output"] == created_task["spec"]["output"]
    assert body["name"] == "验收用·由完整 Spec 建的任务"


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"file_id": "FILL", "start": START, "end": END, "metrics": ["sales_amount"], "spec": {}}, "两条路径同时给"),
        ({"file_id": "FILL", "start": START, "end": END}, "便捷路径缺 metrics"),
        ({"file_id": "FILL", "spec": None, "metrics": ["sales_amount"]}, "只给了 metrics"),
    ],
)
def test_create_task_rejects_ambiguous_or_incomplete_input(uploaded, payload, reason):
    """两条路径二选一，含糊/不完整一律 422（不猜 —— 铁律 4 的同一条精神）。"""
    payload["file_id"] = uploaded["file_id"]
    response = client.post("/api/tasks", json=payload)
    banner(f"POST /api/tasks（{reason} → 期望 422）")
    print(f"HTTP {response.status_code}：{json.dumps(response.json()['detail'], ensure_ascii=False)[:200]}")
    assert response.status_code == 422


def test_create_task_rejects_relative_time_range(uploaded):
    """相对区间（"上周"）在 002D 里**建任务时就拒绝**：schema 冻结了，但解析属 TASK-007。"""
    response = client.post(
        "/api/tasks",
        json={
            "file_id": uploaded["file_id"],
            "spec": {
                "spec_id": "relative-1",
                "name": "上周销售",
                "version": 1,
                "data_source": {"kind": "excel", "path": "placeholder.xlsx"},
                "time_range": {"mode": "last_week", "tz": "Asia/Shanghai"},
                "metrics": [{"name": "sales_amount", "dsl": {"op": "sum", "field": "sales_amount"}}],
                "output": {"template": "templates/weekly_sales_template.xlsx", "sheet": "周报", "cells": {"sales_amount": "B4"}},
                "created_at": "2026-09-23T00:00:00+08:00",
            },
        },
    )
    banner("POST /api/tasks（mode=last_week → 期望 422 + relative_time_not_implemented）")
    print(f"HTTP {response.status_code}\n{json.dumps(response.json(), ensure_ascii=False, indent=2)}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "relative_time_not_implemented"


def test_create_task_rejects_unknown_metric_and_file(uploaded):
    """未知指标 / 未知 file_id：错误码要能区分（前端按 code 分支，不靠猜）。"""
    unknown_metric = client.post(
        "/api/tasks",
        json={"file_id": uploaded["file_id"], "start": START, "end": END, "metrics": ["profit_margin"]},
    )
    banner("POST /api/tasks（未知指标 → 422 unknown_metric）")
    print(json.dumps(unknown_metric.json(), ensure_ascii=False, indent=2))
    assert unknown_metric.status_code == 422
    assert unknown_metric.json()["error"]["code"] == "unknown_metric"

    unknown_file = client.post(
        "/api/tasks",
        json={"file_id": "f_not_exist", "start": START, "end": END, "metrics": ["sales_amount"]},
    )
    banner("POST /api/tasks（未知 file_id → 404 file_id_not_found）")
    print(json.dumps(unknown_file.json(), ensure_ascii=False, indent=2))
    assert unknown_file.status_code == 404
    assert unknown_file.json()["error"]["code"] == "file_id_not_found"


def test_create_task_rejects_inconsistent_spec(uploaded, created_task):
    """提交的 Spec 与指标目录/模板对不上 → 422（**早报错**，别等执行到一半才炸）。

    覆盖三处：算子对不上、单元格映射被改、数据源指向别的文件。
    """
    base = json.loads(json.dumps(created_task["spec"]))

    wrong_op = json.loads(json.dumps(base))
    wrong_op["metrics"][0]["dsl"] = {"op": "count", "field": "sales_amount"}
    response = client.post("/api/tasks", json={"file_id": uploaded["file_id"], "spec": wrong_op})
    banner("POST /api/tasks（Spec 算子与目录不符 → 422 metric_dsl_mismatch）")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "metric_dsl_mismatch"

    wrong_cells = json.loads(json.dumps(base))
    wrong_cells["output"]["cells"]["sales_amount"] = "Z99"
    response = client.post("/api/tasks", json={"file_id": uploaded["file_id"], "spec": wrong_cells})
    banner("POST /api/tasks（Spec 单元格不在模板预留格 → 422 cells_mismatch）")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cells_mismatch"

    wrong_source = json.loads(json.dumps(base))
    wrong_source["data_source"] = {"kind": "excel", "path": "data/Online Retail.xlsx"}
    response = client.post("/api/tasks", json={"file_id": uploaded["file_id"], "spec": wrong_source})
    banner("POST /api/tasks（Spec 数据源不是该 file_id 的文件 → 422 data_source_mismatch）")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "data_source_mismatch"


def test_create_task_warns_when_data_is_not_the_snapshot(foreign_task):
    """绑定了非冻结快照的数据：任务能建（要留执行痕迹），但**必须给出可见警告**。"""
    banner("POST /api/tasks（非快照数据 → 建得成，但带 warnings）")
    print(json.dumps(foreign_task.get("warnings"), ensure_ascii=False, indent=2))
    assert foreign_task["data_snapshot_match"] is False
    assert foreign_task["warnings"] and "冻结数据快照" in foreign_task["warnings"][0]


# ════════════════════════════════════════════════════════════════════════
# ② 刷新后可重读（GET /api/tasks/{task_id}）+ 列表（分页）
# ════════════════════════════════════════════════════════════════════════
def test_get_task_rereads_frozen_spec(created_task):
    """GET /api/tasks/{task_id}：刷新页面后靠它重读出**同一个** Spec（不许前端写死）。"""
    response = client.get(f"/api/tasks/{created_task['task_id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    banner("GET /api/tasks/{task_id}（重读）")
    print(f"task_id={body['task_id']}  spec_id={body['spec_id']}  status={body['status']}")

    assert body["task_id"] == created_task["task_id"]
    assert body["spec"] == created_task["spec"]        # 逐字一致：建任务时返回的就是落盘的那份
    assert body["run_url"] == f"/api/tasks/{created_task['task_id']}/run"
    assert body["runs_url"] == f"/api/tasks/{created_task['task_id']}/runs"


def test_get_task_unknown_404():
    response = client.get("/api/tasks/t_not_exist")
    banner("GET /api/tasks/t_not_exist → 期望 404")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "task_not_found"


def test_list_tasks_paginates_and_summarises(created_task):
    """列表：分页参数齐全、摘要里有区间/指标/单元格、执行统计来自真实 run 记录。"""
    response = client.get("/api/tasks", params={"limit": 2, "offset": 0})
    assert response.status_code == 200, response.text
    body = response.json()
    banner("GET /api/tasks?limit=2&offset=0")
    print(json.dumps(body, ensure_ascii=False, indent=2)[:1200])

    assert body["limit"] == 2 and body["offset"] == 0
    assert body["count"] == min(2, body["total"])   # 一页最多 2 条
    assert body["total"] >= 3                       # 便捷路径 + 完整 Spec 路径 + 非快照那个

    # 分页：offset 要真的跳过前面几条（不是每次都返回同一页）
    page2 = client.get("/api/tasks", params={"limit": 2, "offset": 2}).json()
    assert page2["offset"] == 2 and page2["count"] == max(0, min(2, body["total"] - 2))
    first_page_ids = {item["task_id"] for item in body["tasks"]}
    assert not (first_page_ids & {item["task_id"] for item in page2["tasks"]}), "第二页不该重复第一页"

    listed = client.get("/api/tasks", params={"limit": 50}).json()["tasks"]
    summary = next(item for item in listed if item["task_id"] == created_task["task_id"])
    assert summary["spec_summary"]["start"] == START and summary["spec_summary"]["end"] == END
    assert summary["spec_summary"]["metrics"] == ALL_METRICS
    assert summary["spec_summary"]["cells"]["sales_amount"] == "B4"
    assert summary["status"] in state.TASK_STATUSES
    assert "spec" not in summary                   # 列表不塞完整 Spec（详情端点才给）


# ════════════════════════════════════════════════════════════════════════
# ③ 按任务执行（POST /api/tasks/{task_id}/run）
# ════════════════════════════════════════════════════════════════════════
def test_run_task_uses_frozen_spec_and_returns_real_numbers(ran_task, created_task):
    """真执行：数字来自冻结的 Spec，且与 002A 的独立 Oracle 值一致。"""
    banner("POST /api/tasks/{task_id}/run")
    print(json.dumps(ran_task, ensure_ascii=False, indent=2))

    assert ran_task["task_id"] == created_task["task_id"]
    assert ran_task["status"] == "success"
    assert ran_task["amount"] == pytest.approx(EXPECTED_AMOUNT, abs=1e-6)
    assert ran_task["rows_in_range"] == EXPECTED_ROWS_IN_RANGE
    assert ran_task["rows_valid"] + ran_task["rows_excluded"] == EXPECTED_ROWS_IN_RANGE
    assert ran_task["range"] == {"start": START, "end": END}
    assert ran_task["resolved_range"] == {"start": START, "end": END}
    assert ran_task["spec_id"] == created_task["spec"]["spec_id"]
    assert ran_task["spec_version"] == created_task["spec"]["version"]
    assert ran_task["data_sha256"] == loader.EXPECTED_SHA256
    assert ran_task["verification"]["passed"] is True
    assert ran_task["verification"]["items"]
    assert ran_task["seconds"] > 0


def test_run_response_hides_server_absolute_path(ran_task):
    """R005 #7：对外只给相对路径 + download_url，不给服务器绝对路径（xlsx 只留在落盘记录里）。"""
    banner("run 响应里不应出现服务器绝对路径")
    leaked = [key for key, value in ran_task.items() if isinstance(value, str) and (":\\" in value or ":\\\\" in value)]
    print(f"含绝对路径的字段：{leaked or '（无）'}")
    print(f"excel_rel_path={ran_task['excel_rel_path']!r}  download_url={ran_task['download_url']!r}")
    assert "excel_path" not in ran_task
    assert leaked == []
    assert not pathlib.Path(ran_task["excel_rel_path"]).is_absolute()
    # 产出目录在默认位置时是 "outputs/x_xxx.xlsx"；测试把产出目录指到了临时目录，
    # 这时退化成文件名（见 _relative_output_path 的说明）—— 两种都不含绝对路径
    assert ran_task["excel_rel_path"].endswith(".xlsx")
    assert ran_task["download_url"] == f"/api/download/{ran_task['execution_id']}"
    # 落盘记录里**仍然**保留服务器绝对路径（内部审计要看文件究竟在哪）
    payload = json.loads(state.executions_file().read_text(encoding="utf-8"))
    stored = next(item for item in payload["executions"] if item["execution_id"] == ran_task["execution_id"])
    assert pathlib.Path(stored["excel_path"]).is_absolute() and pathlib.Path(stored["excel_path"]).exists()


def test_run_task_marks_task_has_run_and_records_are_linked(ran_task, created_task):
    """状态机 created → has_run；且执行记录里绑上了 task_id（D11）。"""
    detail = client.get(f"/api/tasks/{created_task['task_id']}").json()
    banner("执行后重读任务状态")
    print(f"status={detail['status']}  run_count={detail['run_count']}  last_run_at={detail['last_run_at']}")
    assert detail["status"] == "has_run"
    assert detail["run_count"] >= 1

    payload = json.loads(state.executions_file().read_text(encoding="utf-8"))
    record = next(item for item in payload["executions"] if item["execution_id"] == ran_task["execution_id"])
    print(f"\n落盘记录：task_id={record['task_id']}  resolved_range={record['resolved_range']}  base_date={record['base_date']}")
    assert record["task_id"] == created_task["task_id"]
    assert record["resolved_range"] == {"start": START, "end": END}
    assert record["base_date"] is None                    # 绝对模式没有基准日
    assert record["spec_id"] == created_task["spec"]["spec_id"]
    assert payload["schema_version"] == state.SCHEMA_VERSION


def test_run_task_download_is_real_xlsx(ran_task):
    """用 run 返回的 download_url 取回文件，**用 openpyxl 打开读格子**（真下载，不是看 JSON 自称）。"""
    response = client.get(ran_task["download_url"])
    banner(f"GET {ran_task['download_url']} → openpyxl 读回")
    assert response.status_code == 200, response.text
    assert response.content[:2] == b"PK", "xlsx 是 zip，必须以 PK 开头（不是拼出来的假文件）"

    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook[ran_task["sheet"]]
    print(f"sheet={sheet.title}  B4={sheet['B4'].value!r}  B5={sheet['B5'].value!r}  B9={sheet['B9'].value!r}")
    assert sheet["B4"].value == pytest.approx(EXPECTED_AMOUNT, abs=1e-6)
    assert sheet["B5"].value == EXPECTED_ROWS_IN_RANGE
    assert sheet["B4"].number_format == "#,##0.00"         # 模板样式保留（D5）
    assert ran_task["excel_size_bytes"] == len(response.content)


def test_run_task_rejects_foreign_data_and_records_failed_run(foreign_task):
    """数据哈希不符：**拒绝执行 + 落 status=failed 的 run + 不出文件 + 无 download_url**（R005 #4）。"""
    before = sorted(path.name for path in state.output_dir().glob("*.xlsx"))
    response = client.post(f"/api/tasks/{foreign_task['task_id']}/run")
    after = sorted(path.name for path in state.output_dir().glob("*.xlsx"))

    banner("POST /api/tasks/{task_id}/run（非快照数据 → 期望 422 + failed run）")
    print(f"HTTP {response.status_code}\n{json.dumps(response.json(), ensure_ascii=False, indent=2)}")
    assert response.status_code == 422
    # 任务绑定的哈希 ≠ 冻结快照的哈希 —— 这正是 R005 #4 说的"data_sha256 不符"
    assert response.json()["error"]["code"] == "data_snapshot_mismatch"

    runs = client.get(f"/api/tasks/{foreign_task['task_id']}/runs").json()
    failed = [run for run in runs["runs"] if run["status"] == "failed"]
    print(f"\n该任务的 run 记录：{len(runs['runs'])} 条（失败 {len(failed)} 条）")
    print(json.dumps(failed[0], ensure_ascii=False, indent=2) if failed else "（没有失败记录 —— 不合格）")
    assert failed, "拒绝执行必须留下 status=failed 的 run 记录（D10 的审计要求）"
    assert "data_snapshot_mismatch" in failed[0]["error"]
    assert failed[0]["stage"] == 0                     # 0 = 执行前的数据核对阶段
    assert failed[0]["output_rel_path"] is None        # 失败不产出（D10）
    assert failed[0]["download_url"] is None
    assert after == before, "被拒绝的执行不得产出任何文件"
    # 失败不改任务状态：失败的是这次执行，不是任务定义（state.py 的 TASK_STATUSES 说明）
    assert client.get(f"/api/tasks/{foreign_task['task_id']}").json()["status"] == "created"


def test_run_task_detects_replaced_upload_record(created_task):
    """上传记录里的哈希与任务绑定的不一致（文件被换过）→ `data_mismatch` + failed run。

    与上一条的区别：上一条是"绑的就不是快照"，这条是"绑的是快照，但上传记录被改过"——
    两种数据不合格都要拒绝且留痕，错误码要能区分（前端按 code 分支）。
    """
    uploads_file = state.uploads_file()
    original = uploads_file.read_text(encoding="utf-8")
    payload = json.loads(original)
    target = next(item for item in payload["uploads"] if item["file_id"] == created_task["file_id"])
    target["sha256"] = "0" * 64                     # 模拟"文件被换成了另一份"
    uploads_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        response = client.post(f"/api/tasks/{created_task['task_id']}/run")
    finally:
        uploads_file.write_text(original, encoding="utf-8")     # 复原，别影响后面的测试

    banner("POST /api/tasks/{task_id}/run（上传记录被换成别的哈希 → 期望 422 data_mismatch）")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "data_mismatch"


def test_run_task_unknown_404():
    response = client.post("/api/tasks/t_not_exist/run")
    banner("POST /api/tasks/t_not_exist/run → 期望 404")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "task_not_found"


def test_run_task_records_failure_when_upload_record_gone(created_task):
    """上传记录被清掉（state/uploads.json 没了）→ 拒绝 + 留痕 + 410（不假装能算）。"""
    uploads_file = state.uploads_file()
    backup = uploads_file.with_suffix(".json.bak")
    uploads_file.rename(backup)
    try:
        response = client.post(f"/api/tasks/{created_task['task_id']}/run")
    finally:
        backup.rename(uploads_file)

    banner("POST /api/tasks/{task_id}/run（上传记录被清 → 期望 410 + failed run）")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "upload_record_gone"

    runs = client.get(f"/api/tasks/{created_task['task_id']}/runs").json()["runs"]
    assert any(run["error"] and "upload_record_gone" in run["error"] for run in runs)


# ════════════════════════════════════════════════════════════════════════
# ④ 执行历史（GET /api/tasks/{task_id}/runs）
# ════════════════════════════════════════════════════════════════════════
def test_task_runs_lists_history_with_pinned_fields(ran_task, created_task):
    """R005 #6 钉死的字段清单：成功/失败记录**都有这些键**（失败的值是 null，不隐藏字段）。"""
    response = client.get(f"/api/tasks/{created_task['task_id']}/runs")
    assert response.status_code == 200, response.text
    body = response.json()
    banner("GET /api/tasks/{task_id}/runs")
    print(f"count={body['count']}  total={body['total']}  task_id={body['task_id']}")
    print(json.dumps(body["runs"][0], ensure_ascii=False, indent=2))

    pinned = {
        "run_id", "execution_id", "task_id", "status", "created_at", "spec_id", "spec_version",
        "time_range", "resolved_range", "base_date", "data_sha256", "code_version", "verification",
        "seconds", "error", "output_rel_path", "download_url",
    }
    # 字段清单对**每一条**记录都要成立（成功与失败形状一致，前端不用写两套取值逻辑）
    for run in body["runs"]:
        assert pinned <= set(run), f"缺少钉死的字段：{pinned - set(run)}"
    assert body["task_id"] == created_task["task_id"]
    assert all(run["task_id"] == created_task["task_id"] for run in body["runs"])   # 只含本任务的

    # 该任务跑过两次：先成功一次，后又因"上传记录被换"被拒一次（见上面的 data_mismatch 测试）。
    # 两次都在历史里，这里挑**成功**那一次核对产出信息。
    success = next(run for run in body["runs"] if run["status"] == "success")
    assert success["execution_id"] == ran_task["execution_id"]
    assert success["output_rel_path"] == ran_task["excel_rel_path"]
    assert success["download_url"] == ran_task["download_url"]
    assert success["time_range"]["mode"] == "absolute"       # 冻结的声明
    assert success["resolved_range"] == {"start": START, "end": END}   # 实际算的区间
    assert success["amount"] == pytest.approx(EXPECTED_AMOUNT, abs=1e-6)
    assert success["base_date"] is None
    # 失败那条也要在历史里（拒绝执行必须留痕）
    assert [run for run in body["runs"] if run["status"] == "failed"]


def test_task_runs_paginates(created_task):
    response = client.get(f"/api/tasks/{created_task['task_id']}/runs", params={"limit": 1, "offset": 0})
    assert response.status_code == 200, response.text
    body = response.json()
    banner("GET /api/tasks/{task_id}/runs?limit=1")
    print(f"count={body['count']}  total={body['total']}  limit={body['limit']}  offset={body['offset']}")
    assert body["limit"] == 1 and body["offset"] == 0
    assert body["count"] <= 1 and body["total"] >= 1


def test_task_runs_unknown_task_404():
    response = client.get("/api/tasks/t_not_exist/runs")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "task_not_found"


# ════════════════════════════════════════════════════════════════════════
# ⑤ 统一错误体与既有契约的兼容（R005 #5 + #10）
# ════════════════════════════════════════════════════════════════════════
def test_error_body_shape_is_uniform_on_new_and_old_endpoints():
    """新端点与**既有端点**的错误体形状一致；既有 `detail` 原样保留（冻结契约）。"""
    banner("统一错误体：{error:{code,message,detail}} + 兼容的 detail")
    new = client.post(
        "/api/tasks",
        json={"file_id": "f_not_exist", "start": START, "end": END, "metrics": ["sales_amount"]},
    ).json()
    old = client.post(
        "/api/execute",
        json={"file_id": "f_not_exist", "start": START, "end": END, "metrics": ["sales_amount"]},
    ).json()
    print("新端点（/api/tasks）：", json.dumps(new, ensure_ascii=False))
    print("既有端点（/api/execute）：", json.dumps(old, ensure_ascii=False))

    for body in (new, old):
        assert set(body["error"]) == {"code", "message", "detail"}
        assert body["error"]["code"] == "file_id_not_found"
        assert body["detail"] == body["error"]["message"]      # 旧契约读到的还是那句话
    assert "未知的 file_id" in old["detail"]                    # 002C 的测试就是这么断言的


def test_validation_error_keeps_list_detail():
    """pydantic 校验失败（422）：新形状有 code，`detail` 仍是 FastAPI 原始的错误列表。"""
    response = client.post("/api/tasks", json={"start": START})
    banner("POST /api/tasks（请求体缺字段 → 422 validation_error）")
    print(f"HTTP {response.status_code}\n{json.dumps(response.json(), ensure_ascii=False, indent=2)[:600]}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert isinstance(response.json()["detail"], list)


def test_openapi_documents_error_shape():
    """R005 #5 要求"在 OpenAPI 中体现"：/openapi.json 里能看到统一错误体与任务端点。"""
    spec = client.get("/openapi.json").json()
    banner("GET /openapi.json（错误体与任务端点是否在文档里）")
    assert "ErrorResponse" in spec["components"]["schemas"]
    task_post = spec["paths"]["/api/tasks"]["post"]
    codes = set(task_post["responses"])
    print(f"/api/tasks 的响应码：{sorted(codes)}")
    assert {"404", "410", "422", "500", "503"} <= codes
    assert spec["info"]["version"] == "0.2.0"
