"""tests/test_api.py · TASK-002C 验收测试（HTTP 接口层）。

════════════════════════════════════════════════════════════════════════
【本文件的验证思路 —— 为什么这样测才算数】
════════════════════════════════════════════════════════════════════════
被测：app/api.py（FastAPI 路由）+ app/state.py（执行记录落盘）

接口测试最容易测成"自己写、自己读"（把 mock 的数字当成真数字）。本文件刻意避开这点：

  ① **真上传**：上传的是仓库里那份 541,909 行的真实 xlsx（data/Online Retail.xlsx），
     断言 SHA256 == 冻结快照的哈希 → 证明"上传的字节就是那份数据"，不是随便造的小文件。
  ② **真计算**：/api/execute 的期望值 316,412.16 / 19,950 行来自**已由独立 Oracle 验过的 002A**
     （见 docs/TASKS.md 的 TASK-002A），不是本文件自己算出来的。接口串错了这里就会红。
  ③ **真下载**：用 /api/download 拿回来的字节流**用 openpyxl 打开**，逐个格子读回来比对
     —— 走的是"用户下载后打开文件"这条真实路径，而不是看接口返回的 JSON 字段自称多少。
  ④ **真落盘**：执行记录从 state/executions.json **文件里读**出来核对（不是看内存对象），
     确认 execution_id / 数据 SHA256 / 计算值 / 耗时 / 代码版本 / 校验结果都真写进去了。
  ⑤ **错误路径也算验收**：4xx/404 必须可见（002C 的 AC-04）—— 沉默失败比报错更危险。

隔离：三个环境变量（SRA_STATE_DIR / SRA_UPLOAD_DIR / SRA_OUTPUT_DIR）指向 pytest 临时目录，
所以测试**不会**往仓库的 state/、data/uploads/、outputs/ 里写东西。
（state.py 是**每次调用时**读环境变量的，所以测试改环境能生效，见 app/state.py 的说明。）

口径依据：docs/DECISIONS.md 的 D4（数字由代码算）/ D5（openpyxl 原地改）/ D10（失败不产出）
        / D11（执行记录绑定 spec+数据+代码版本）/ D16（销售额口径）/ D17-2（展示 2 位小数）。
"""

from __future__ import annotations

import hashlib
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
from openpyxl import load_workbook  # noqa: E402

from app import state  # noqa: E402
from app.api import app  # noqa: E402
from app.engine import loader  # noqa: E402

# ── 验收用的真实数据与区间（与 002A / 002B 用同一周，便于交叉核对）──────────
DATA_PATH = PROJECT_ROOT / "data" / "Online Retail.xlsx"
START = "2011-11-21"
END = "2011-11-27"

# 期望值来自 002A（独立 Oracle 验过）：区间原始行数 19,950；销售额 £316,412.16
EXPECTED_ROWS_IN_RANGE = 19950
EXPECTED_AMOUNT = 316412.16

# 接口支持的全部指标（= 模板 weekly_sales_template.xlsx 的 B4:B9 六个数据格）
ALL_METRICS = [
    "sales_amount",
    "rows_in_range",
    "rows_valid",
    "rows_excluded",
    "excluded_amount",
    "valid_qty_sum",
]

# 期望的单元格布局（写死在测试里：表达"我期望报表长这样"，不是"实现说是什么"）
EXPECTED_CELLS = {
    "sales_amount": ("B4", "#,##0.00"),
    "rows_in_range": ("B5", "#,##0"),
    "rows_valid": ("B6", "#,##0"),
    "rows_excluded": ("B7", "#,##0"),
    "excluded_amount": ("B8", "#,##0.00"),
    "valid_qty_sum": ("B9", "#,##0"),
}

READBACK_REL_TOL = 1e-9          # 与 002B 一致的读回容差（openpyxl 序列化损耗，非计算误差）

client = TestClient(app)


# ════════════════════════════════════════════════════════════════════════
# 测试环境隔离 + 共用 fixture
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module", autouse=True)
def isolated_dirs(tmp_path_factory):
    """把状态/上传/产出三个目录指到 pytest 临时目录，测试不污染仓库。"""
    root = tmp_path_factory.mktemp("api_state")
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
    """真上传 data/Online Retail.xlsx（54 万行），全程只做一次，模块内共用。"""
    with open(DATA_PATH, "rb") as handle:
        response = client.post(
            "/api/upload",
            files={"file": ("Online Retail.xlsx", handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def executed(uploaded) -> dict:
    """在真实上传的文件上执行一次真实计算 + 渲染，模块内共用（读 23MB xlsx 很慢）。"""
    response = client.post(
        "/api/execute",
        json={"file_id": uploaded["file_id"], "start": START, "end": END, "metrics": ALL_METRICS},
    )
    assert response.status_code == 200, response.text
    return response.json()


def banner(title: str) -> None:
    print("\n" + "─" * 72)
    print(title)
    print("─" * 72)


# ════════════════════════════════════════════════════════════════════════
# AC-02 健康检查
# ════════════════════════════════════════════════════════════════════════
def test_health_ok():
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    body = response.json()

    banner("/api/health")
    print(json.dumps(body, ensure_ascii=False, indent=2)[:900])

    assert body["status"] == "ok", body["problems"]
    assert body["service"] == "sales-report-agent"
    # 健康检查必须真的核对数据快照指纹（否则"健康"只是个摆设）
    assert body["data_snapshot"]["exists"] is True
    assert body["data_snapshot"]["match"] is True
    assert body["data_snapshot"]["sha256"] == loader.EXPECTED_SHA256
    assert body["template"]["exists"] is True
    assert body["problems"] == []


# ════════════════════════════════════════════════════════════════════════
# AC-02 数据字典（默认看冻结数据快照 → 8 个字段）
# ════════════════════════════════════════════════════════════════════════
def test_schema_snapshot_has_eight_fields():
    response = client.get("/api/schema")
    assert response.status_code == 200, response.text
    body = response.json()

    banner("/api/schema（默认：冻结数据快照）")
    print(f"rows={body['rows']}  column_count={body['column_count']}  seconds={body['seconds']}")
    for field in body["fields"]:
        print(
            f"  {field['name']:<12} dtype={field['dtype']:<16} 非空率={field['non_null_rate']:.6f} "
            f"样例={field['sample_values']}"
        )

    assert body["column_count"] == 8
    assert len(body["fields"]) == 8
    assert body["columns"] == [
        "InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
        "UnitPrice", "CustomerID", "Country",
    ]
    assert body["rows"] == 541909                                   # 冻结快照的真实行数

    # 非空率必须来自真实统计：CustomerID 有空值（D16-6 的脏数据特征），其余字段无空值
    by_name = {field["name"]: field for field in body["fields"]}
    assert by_name["CustomerID"]["null_count"] > 0
    assert by_name["CustomerID"]["non_null_rate"] < 1.0
    assert by_name["InvoiceNo"]["null_count"] == 0
    assert by_name["InvoiceDate"]["inferred_type"] == "datetime"


def test_schema_of_uploaded_file(uploaded):
    response = client.get("/api/schema", params={"file_id": uploaded["file_id"]})
    assert response.status_code == 200, response.text
    body = response.json()

    banner("/api/schema?file_id=<上传的文件>")
    print(f"source={body['source']['kind']}  filename={body['source']['filename']}")
    print(f"rows={body['rows']}  column_count={body['column_count']}")

    assert body["source"]["kind"] == "upload"
    assert body["source"]["file_id"] == uploaded["file_id"]
    assert body["rows"] == 541909
    assert body["column_count"] == 8


def test_schema_unknown_file_id_404():
    response = client.get("/api/schema", params={"file_id": "f_not_exist"})
    assert response.status_code == 404
    assert "未知的 file_id" in response.json()["detail"]


# ════════════════════════════════════════════════════════════════════════
# AC-02 上传（真上传 541,909 行）
# ════════════════════════════════════════════════════════════════════════
def test_upload_real_dataset(uploaded):
    banner("/api/upload（真上传 data/Online Retail.xlsx）")
    print(json.dumps({k: v for k, v in uploaded.items() if k != "column_names"}, ensure_ascii=False, indent=2))

    assert uploaded["rows"] == 541909
    assert uploaded["columns"] == 8
    assert uploaded["filename"] == "Online Retail.xlsx"
    assert uploaded["file_id"].startswith("f_")
    # 关键：上传的字节 = 冻结快照（否则后面 execute 的算术就没法归因到这份文件）
    assert uploaded["sha256"] == loader.EXPECTED_SHA256
    # 落盘的文件真的存在，且大小与上传字节数一致
    stored = pathlib.Path(uploaded["stored_path"])
    assert stored.exists()
    assert stored.stat().st_size == uploaded["size_bytes"]


def test_upload_rejects_unsupported_type(tmp_path):
    """AC-04：上传非 xlsx → 4xx 且带可读错误信息。"""
    bad = tmp_path / "notes.txt"
    bad.write_text("这不是表格", encoding="utf-8")
    response = client.post("/api/upload", files={"file": ("notes.txt", bad.read_bytes(), "text/plain")})

    banner("/api/upload（非表格文件 → 期望 4xx）")
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))

    assert response.status_code == 400
    assert "不支持的文件类型" in response.json()["detail"]


def test_upload_corrupted_xlsx_returns_400(tmp_path):
    """后缀对但内容不是 xlsx（改名骗过校验）→ 解析失败也是 4xx，不是 500。"""
    fake = tmp_path / "fake.xlsx"
    fake.write_bytes(b"PK\x03\x04 this is not a real xlsx")
    response = client.post("/api/upload", files={"file": ("fake.xlsx", fake.read_bytes(), "application/vnd.ms-excel")})

    banner("/api/upload（后缀对但内容坏 → 期望 4xx）")
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2)[:400])

    assert response.status_code == 400
    assert "解析失败" in response.json()["detail"]
    # 坏文件不能留在 uploads/ 里（D10 的精神：宁可没有文件）
    leftovers = [p.name for p in state.upload_dir().glob("*fake*")]
    assert leftovers == []


def test_upload_csv_is_accepted_but_not_executable(tmp_path):
    """csv 能上传（002C 要求支持），但**执行**会被明确拒绝（002A 只读 xlsx，改它=改计算逻辑）。"""
    csv_path = tmp_path / "tiny.csv"
    csv_path.write_text("InvoiceNo,Quantity\n10001,3\n10002,5\n", encoding="utf-8")
    response = client.post("/api/upload", files={"file": ("tiny.csv", csv_path.read_bytes(), "text/csv")})
    assert response.status_code == 201, response.text
    csv_file = response.json()
    assert csv_file["rows"] == 2 and csv_file["columns"] == 2

    rejected = client.post(
        "/api/execute",
        json={"file_id": csv_file["file_id"], "start": START, "end": END, "metrics": ["sales_amount"]},
    )

    banner("/api/execute（对 csv → 期望明确拒绝，不猜）")
    print(f"HTTP {rejected.status_code}")
    print(json.dumps(rejected.json(), ensure_ascii=False, indent=2))

    assert rejected.status_code == 422
    assert "只支持 .xlsx" in rejected.json()["detail"]


# ════════════════════════════════════════════════════════════════════════
# AC-02 / AC-03 执行 + 下载（真算、真文件）
# ════════════════════════════════════════════════════════════════════════
def test_execute_returns_real_amount(executed):
    banner(f"/api/execute（{START} ~ {END}，全 6 个指标）")
    print(json.dumps(executed, ensure_ascii=False, indent=2)[:1600])

    assert executed["status"] == "success"
    assert executed["execution_id"].startswith("x_")
    # 金额：展示 2 位小数（D17-2），全精度原值同时在（复核用）
    assert executed["amount"] == pytest.approx(EXPECTED_AMOUNT, abs=1e-6)
    assert abs(executed["amount_full"] - EXPECTED_AMOUNT) < 0.01
    assert executed["amount"] == round(executed["amount_full"], 2)
    # 行数来自 002A（独立 Oracle 验过）
    assert executed["rows_in_range"] == EXPECTED_ROWS_IN_RANGE
    assert executed["rows_valid"] + executed["rows_excluded"] == executed["rows_in_range"]
    # 六个指标都写进去了（证明指标目录 -> 受限 DSL -> 模板单元格这条链是通的）
    assert set(executed["metrics"]) == set(ALL_METRICS)
    # 产出文件真的在盘上（不是"接口说产出了"）
    excel = pathlib.Path(executed["excel_path"])
    assert excel.exists() and excel.stat().st_size > 0
    assert executed["excel_size_bytes"] == excel.stat().st_size
    assert executed["template"].endswith("weekly_sales_template.xlsx")


def test_download_returns_real_xlsx_readable_by_openpyxl(executed):
    """AC-03：下载回来的字节用 openpyxl 打开，逐个格子核对数字与数字格式。"""
    response = client.get(f"/api/download/{executed['execution_id']}")
    assert response.status_code == 200, response.text
    content = response.content

    banner(f"/api/download/{executed['execution_id']}")
    print(f"HTTP {response.status_code}")
    print(f"content-type: {response.headers['content-type']}")
    print(f"content-disposition: {response.headers.get('content-disposition')}")
    print(f"下载字节数: {len(content)}（盘上文件 {executed['excel_size_bytes']} 字节）")
    print(f"下载内容 SHA256: {hashlib.sha256(content).hexdigest()}")
    print(f"盘上文件 SHA256: {hashlib.sha256(pathlib.Path(executed['excel_path']).read_bytes()).hexdigest()}")

    assert len(content) == executed["excel_size_bytes"]
    assert content[:2] == b"PK"                                     # xlsx 就是 zip
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    workbook = load_workbook(BytesIO(content))                      # 真用 openpyxl 打开
    assert workbook.sheetnames == ["周销售报表"]
    sheet = workbook["周销售报表"]

    print("\n读回值（openpyxl）：")
    for metric, (address, number_format) in EXPECTED_CELLS.items():
        cell = sheet[address]
        expected = executed["metrics"][metric]
        read_back = cell.value
        tolerance = max(1e-9, abs(float(expected)) * READBACK_REL_TOL)
        print(
            f"  {address} {metric:<16} 读回={read_back!r} 写入={expected!r} "
            f"格式={cell.number_format!r}"
        )
        assert isinstance(read_back, (int, float)) and not isinstance(read_back, bool)
        assert abs(float(read_back) - float(expected)) <= tolerance
        assert cell.number_format == number_format

    # 金额格：读回的原值就是全精度，Excel 里按 #,##0.00 显示成 316,412.16（D17-2）
    assert sheet["B4"].value == pytest.approx(EXPECTED_AMOUNT, rel=1e-9)
    assert f"{sheet['B4'].value:,.2f}" == "316,412.16"
    assert sheet["B5"].value == EXPECTED_ROWS_IN_RANGE


def test_download_unknown_execution_404():
    response = client.get("/api/download/x_not_exist")
    banner("/api/download/x_not_exist（期望 404）")
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 404
    assert "未知的 execution_id" in response.json()["detail"]


# ════════════════════════════════════════════════════════════════════════
# AC-04 错误路径
# ════════════════════════════════════════════════════════════════════════
def test_execute_unknown_file_id_404():
    response = client.post(
        "/api/execute",
        json={"file_id": "f_not_exist", "start": START, "end": END, "metrics": ["sales_amount"]},
    )
    banner("/api/execute（不存在的 file_id → 期望 404）")
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 404
    assert "未知的 file_id" in response.json()["detail"]


def test_execute_rejects_unknown_metric(uploaded):
    response = client.post(
        "/api/execute",
        json={"file_id": uploaded["file_id"], "start": START, "end": END, "metrics": ["profit"]},
    )
    banner("/api/execute（引擎产不出的指标 → 期望 4xx，不猜）")
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 422
    assert "不支持的指标" in response.json()["detail"]


def test_execute_rejects_reversed_range(uploaded):
    response = client.post(
        "/api/execute",
        json={"file_id": uploaded["file_id"], "start": END, "end": START, "metrics": ["sales_amount"]},
    )
    banner("/api/execute（起始晚于结束 → 期望 422）")
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    assert response.status_code == 422


def test_execute_rejects_foreign_data_file(tmp_path):
    """不是那份冻结快照的文件：**拒绝算**，而不是偷偷用别的数据算出个数字（D10）。

    这是本 TASK 的已知边界（见 app/api.py 文件头）：002A 的 executor 写死读冻结快照，
    支持任意上传文件需要改 002A 的计算逻辑，被 002C 指令禁止 → 这里选择明确报错。
    """
    # 造一个"形状像但不是同一份数据"的 xlsx：把真实文件里的一行数字改掉 → 哈希必然不同
    other = tmp_path / "other.xlsx"
    workbook = load_workbook(DATA_PATH, read_only=True)
    sheet = workbook.active
    rows = []
    for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        rows.append(row)
        if index >= 200:
            break
    workbook.close()
    rows[-1] = tuple(0 if isinstance(value, (int, float)) else value for value in rows[-1])

    from openpyxl import Workbook
    small = Workbook()
    small_sheet = small.active
    for row in rows:
        small_sheet.append(list(row))
    small.save(other)

    upload = client.post("/api/upload", files={"file": ("other.xlsx", other.read_bytes(), "application/vnd.ms-excel")})
    assert upload.status_code == 201, upload.text
    response = client.post(
        "/api/execute",
        json={"file_id": upload.json()["file_id"], "start": START, "end": END, "metrics": ["sales_amount"]},
    )

    banner("/api/execute（非冻结快照的文件 → 期望明确拒绝，不出数字）")
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))

    assert response.status_code == 422
    assert "不是同一份数据" in response.json()["detail"]
    # 拒绝发生在计算之前，所以既没有新产出文件，也没有多出一条执行记录
    # （outputs/ 里只应有那个成功执行的产出，见 test_download_returns_real_xlsx...）
    assert len(list(state.output_dir().glob("*.xlsx"))) == 1


# ════════════════════════════════════════════════════════════════════════
# AC-05 执行记录落盘
# ════════════════════════════════════════════════════════════════════════
def test_executions_endpoint_lists_record(executed):
    response = client.get("/api/executions")
    assert response.status_code == 200, response.text
    body = response.json()

    banner("/api/executions")
    print(f"count={body['count']}  total={body['total']}")
    first = body["executions"][0]
    print(json.dumps({k: v for k, v in first.items() if k not in ("spec", "validations")}, ensure_ascii=False, indent=2))

    assert body["total"] >= 1
    assert body["executions"][0]["execution_id"] == executed["execution_id"]


def test_execution_record_persisted_with_audit_fields(executed):
    """AC-05：从**文件**里读执行记录，核对 D11 要求的绑定信息都在。"""
    path = state.executions_file()
    assert path.exists(), f"执行记录没有落盘：{path}"
    payload = json.loads(path.read_text(encoding="utf-8"))
    record = next(
        item for item in payload["executions"] if item["execution_id"] == executed["execution_id"]
    )

    banner(f"state/executions.json 里的执行记录：{record['execution_id']}")
    print(json.dumps({k: v for k, v in record.items() if k != "spec"}, ensure_ascii=False, indent=2)[:1800])
    print("\n冻结的 Spec（D11 审计凭据）：")
    print(json.dumps(record["spec"], ensure_ascii=False, indent=2))

    assert record["status"] == "success"
    assert record["data_sha256"] == loader.EXPECTED_SHA256          # 数据哈希
    assert record["snapshot_sha256"] == loader.EXPECTED_SHA256
    assert record["data_snapshot_match"] is True
    assert record["amount"] == pytest.approx(EXPECTED_AMOUNT, abs=1e-6)   # 计算值（全精度）
    assert record["amount_display"] == pytest.approx(EXPECTED_AMOUNT, abs=1e-6)
    assert record["seconds"] > 0                                     # 耗时
    assert record["compute_seconds"] > 0 and record["render_seconds"] > 0
    assert record["code_version"]                                    # 代码版本（git 短哈希或 unknown）
    assert record["excel_path"] and pathlib.Path(record["excel_path"]).exists()
    # 校验结果：executor 的 6 项自检 + renderer 的读回自检都要在记录里
    assert record["validations"]["checks"], "缺少 executor 的自检项"
    assert all(check["passed"] for check in record["validations"]["checks"])
    assert record["render_verification"]["passed"] is True
    assert len(record["render_verification"]["items"]) == len(ALL_METRICS)
    # 冻结的 Spec：口径 + 单元格映射都随记录一起留档
    spec = record["spec"]
    assert spec["spec_id"].startswith("adhoc-f_")
    assert spec["time_range"]["start"] == START and spec["time_range"]["end"] == END
    assert spec["output"]["cells"] == {metric: EXPECTED_CELLS[metric][0] for metric in ALL_METRICS}
    assert all(metric["dsl"]["exclude"] == ["cancelled", "negative_qty", "nonpositive_price"]
               for metric in spec["metrics"])


def test_rejected_request_creates_no_execution_record(tmp_path):
    """入参级拒绝（file_id 不存在）**不**计入执行记录 —— 没有可绑定的数据源，不该冒充一次执行。

    注：计算/渲染阶段的失败（有数据源、有 Spec）会落一条 status=failed 的记录
    （见 app/api.py 的 _record_failure），那种失败在本测试里无法在不改动 002A/002B 的前提下构造出来。
    """
    response = client.post(
        "/api/execute",
        json={"file_id": "f_does_not_exist", "start": START, "end": END, "metrics": ["sales_amount"]},
    )
    assert response.status_code == 404
    # file_id 不存在属于"请求就错了"，不该记成一次执行尝试（没有可绑定的数据源）
    payload = json.loads(state.executions_file().read_text(encoding="utf-8"))
    assert all(item["status"] == "success" for item in payload["executions"])
