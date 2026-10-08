"""test_fr003f_acceptance.py · FR-003F：**评审那 32 条验收断言，逐条落地**。

════════════════════════════════════════════════════════════════════════
【这一组怎么用】
════════════════════════════════════════════════════════════════════════
每条断言的函数名里都带编号（`ASSERT_01_…`），评审原文的编号与这里的编号一一对应。
跑这一组就能回答"32 条过没过"：

    .venv/Scripts/python.exe -m pytest tests/test_fr003f_acceptance.py -v

前六组各自还有更细的用例（A/B/C/D/E 那六个文件）；这里是**逐条点名**的那一份，
每条都给得出证据（数字、类型、代码级断言，而不是"跑通了"）。

════════════════════════════════════════════════════════════════════════
【测试隔离是硬要求】
════════════════════════════════════════════════════════════════════════
`tests/conftest.py` 有一条**会话级 autouse** 的隔离夹具，把六个 SRA_* 路径全部指到临时目录；
本文件里每个用例再各自收窄一次（`fr003_helpers.isolate`）。
`ASSERT_19` 直接拿真实 `state/` 与真实 `data/app.db` 的 mtime/大小做前后对比。
"""

from __future__ import annotations

import inspect
import pathlib

import pytest
from fastapi.testclient import TestClient

import fr003_helpers as helpers
from app.api import app
from app.importer import db, models, normalize, parsers, pipeline, region_query, regions

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "web"
client = TestClient(app)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


def upload(files: list[tuple[str, bytes]]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, data, "application/octet-stream")) for name, data in files]


def import_via_api(tmp_path: pathlib.Path, name: str, data: bytes) -> dict:
    body = client.post("/api/imports", files=upload([(name, data)])).json()
    assert body["ok_count"] == 1, body
    return body["results"][0]


# ══════════════════════════════════════════════════════════════════════════
# A 格式
# ══════════════════════════════════════════════════════════════════════════
def test_ASSERT_01_xlsx_可导入(env, tmp_path) -> None:
    receipt = import_via_api(
        tmp_path, "地区销售.xlsx",
        helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE}).read_bytes(),
    )
    assert receipt["dataset_id"] and receipt["datasets"][0]["row_count"] == 3


def test_ASSERT_02_xlsm_可导入(env, tmp_path) -> None:
    import pandas as pd

    path = tmp_path / "地区销售.xlsm"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(helpers.REGION_SAMPLE[1:], columns=helpers.REGION_SAMPLE[0]) \
            .to_excel(writer, sheet_name="Sheet1", index=False)
    receipt = import_via_api(tmp_path, "地区销售.xlsm", path.read_bytes())
    assert receipt["source_type"] == "excel" and receipt["dataset_id"]


def test_ASSERT_03_csv_可导入(env, tmp_path) -> None:
    data = "省份,销售额\n广东,100\n浙江,200\n".encode("utf-8")
    receipt = import_via_api(tmp_path, "地区.csv", data)
    assert receipt["source_type"] == "csv" and receipt["dataset_id"]


def test_ASSERT_04_docx_可导入(env, tmp_path) -> None:
    data = helpers.make_docx(tmp_path / "周报.docx").read_bytes()
    receipt = import_via_api(tmp_path, "周报.docx", data)
    assert receipt["document_id"] and receipt["documents"][0]["char_count"] > 0


def test_ASSERT_05_pdf_可导入(env, tmp_path) -> None:
    data = helpers.make_pdf(tmp_path / "报告.pdf", ["Quarterly report"]).read_bytes()
    receipt = import_via_api(tmp_path, "报告.pdf", data)
    assert receipt["document_id"]


def test_ASSERT_06_pptx_含正文产生文档(env, tmp_path) -> None:
    data = helpers.make_pptx(tmp_path / "只有正文.pptx", [
        {"title": "上季度分析", "body": "整体向好。"},
    ]).read_bytes()
    receipt = import_via_api(tmp_path, "只有正文.pptx", data)
    assert receipt["document_count"] >= 1
    assert receipt["documents"][0]["char_count"] > 0


def test_ASSERT_07_pptx_含表格产生数据集(env, tmp_path) -> None:
    data = helpers.make_pptx(tmp_path / "只有表.pptx", [
        {"title": "销售", "tables": [[["省份", "销售额"], ["广东", "100"], ["浙江", "200"]]]},
    ]).read_bytes()
    receipt = import_via_api(tmp_path, "只有表.pptx", data)
    assert receipt["dataset_count"] >= 1
    assert receipt["datasets"][0]["row_count"] == 2


def test_ASSERT_08_md_含正文产生文档(env, tmp_path) -> None:
    data = "# 说明\n\n这是一段普通正文。\n".encode("utf-8")
    receipt = import_via_api(tmp_path, "说明.md", data)
    assert receipt["document_id"]


def test_ASSERT_09_md_标准表格产生数据集(env, tmp_path) -> None:
    data = "| 地区 | 销售额 |\n|---|---:|\n| 华东 | 100 |\n| 华南 | 200 |\n".encode("utf-8")
    receipt = import_via_api(tmp_path, "表格.md", data)
    assert receipt["dataset_id"] and receipt["datasets"][0]["row_count"] == 2


def test_ASSERT_10_不支持的扩展名明确报错且零写入(env, tmp_path) -> None:
    body = client.post("/api/imports", files=upload([("数据.txt", b"province,100\n")])).json()
    assert body["ok_count"] == 0
    assert body["results"][0]["error_code"] == "unsupported_type"
    with db.readonly() as connection:
        assert int(db.scalar("SELECT COUNT(*) FROM datasets")) == 0
        assert int(db.scalar("SELECT COUNT(*) FROM documents")) == 0
        assert int(db.scalar("SELECT COUNT(*) FROM source_files")) == 0


# ══════════════════════════════════════════════════════════════════════════
# B 双落
# ══════════════════════════════════════════════════════════════════════════
def test_ASSERT_11_正文与表格的PPTX两个计数都大于等于1(env, tmp_path) -> None:
    data = helpers.make_pptx(tmp_path / "销售分析.pptx", helpers.PPTX_SAMPLE).read_bytes()
    receipt = import_via_api(tmp_path, "销售分析.pptx", data)
    assert receipt["document_count"] >= 1
    assert receipt["dataset_count"] >= 1


def test_ASSERT_12_文档与数据集共享同一个来源文件编号(env, tmp_path) -> None:
    from app.importer import store

    data = helpers.make_pptx(tmp_path / "销售分析.pptx", helpers.PPTX_SAMPLE).read_bytes()
    receipt = import_via_api(tmp_path, "销售分析.pptx", data)
    with db.readonly() as connection:
        import_record = store.get_import(connection, receipt["import_id"])
        dataset = store.get_dataset(connection, import_record["dataset_id"])
        document = store.get_document(connection, import_record["document_id"])
    assert dataset["source_file_id"] == document["source_file_id"]


def test_ASSERT_13_同一文件重复导入不产生重复业务数据(env, tmp_path) -> None:
    data = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE}).read_bytes()
    first = import_via_api(tmp_path, "地区销售.xlsx", data)
    second = import_via_api(tmp_path, "地区销售.xlsx", data)

    assert second["import_id"] == first["import_id"]
    assert second["idempotent"] is True
    with db.readonly() as connection:
        assert int(db.scalar("SELECT COUNT(*) FROM datasets")) == 1
        assert int(db.scalar("SELECT COUNT(*) FROM dataset_rows")) == 3


def test_ASSERT_14_导入失败不留半成品(env, tmp_path) -> None:
    """解析失败：零 dataset / 零 document；数据库那块一起回滚（见 B 组的事务用例）。"""
    body = client.post("/api/imports", files=upload([("空的.csv", b"province,amount\n")])).json()
    assert body["ok_count"] == 0
    with db.readonly() as connection:
        assert int(db.scalar("SELECT COUNT(*) FROM datasets")) == 0
        assert int(db.scalar("SELECT COUNT(*) FROM documents")) == 0
        assert int(db.scalar("SELECT COUNT(*) FROM dataset_rows")) == 0


# ══════════════════════════════════════════════════════════════════════════
# C 入库
# ══════════════════════════════════════════════════════════════════════════
def test_ASSERT_15_原文件移走后数据集仍可查询(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = import_via_api(tmp_path, "地区销售.xlsx", path.read_bytes())

    preserved = pathlib.Path(receipt["original_path"])
    assert preserved.exists(), "原文件应当另存一份留底"
    # 把手上这份**和**留底那一份都删掉 —— 数据仍然查得到，说明查询真的走库
    path.unlink()
    preserved.unlink()

    result = region_query.sales_by_region(receipt["dataset_id"])
    assert result["total_amount"] == 600.0
    assert {row["region"]: row["amount"] for row in result["rows"]} == {
        "广东": 100.0, "浙江": 200.0, "江苏": 300.0,
    }


def test_ASSERT_16_重启后数据集仍存在(env, tmp_path) -> None:
    """用**新进程**读同一个库 —— 只有真的落盘才读得到。"""
    import json
    import os
    import subprocess
    import sys

    data = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE}).read_bytes()
    receipt = import_via_api(tmp_path, "地区销售.xlsx", data)

    code = (
        "import json;from app.importer import store, db;"
        "c=db.connect();"
        f"print(json.dumps(len(store.list_dataset_tables(c, {receipt['dataset_id']!r}))))"
    )
    finished = subprocess.run(
        [sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True, text=True, timeout=120,
    )
    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout.strip()) == 1


def test_ASSERT_17_重启后查询结果与导入前一致(env, tmp_path) -> None:
    import json
    import os
    import subprocess
    import sys

    data = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE}).read_bytes()
    receipt = import_via_api(tmp_path, "地区销售.xlsx", data)
    before = region_query.sales_by_region(receipt["dataset_id"])["rows"]

    code = (
        "import json;from app.importer import region_query;"
        f"print(json.dumps(region_query.sales_by_region({receipt['dataset_id']!r})['rows'],"
        " ensure_ascii=False))"
    )
    finished = subprocess.run(
        [sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True, text=True, timeout=120,
    )
    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout.strip()) == before


def test_ASSERT_18_数据库写入失败则整体失败(env, tmp_path, monkeypatch) -> None:
    """提交那一步炸了 → 请求整体失败（500），库里**一个残件都不留**。

    这里刻意让最后一步失败：那一步发生时 dataset / document 其实都已经插进去了，
    能回滚干净才说明"整条链路是一个事务"。
    """
    from app.importer import store

    def boom(*args, **kwargs):
        raise RuntimeError("写库失败")

    data = helpers.make_pptx(tmp_path / "销售分析.pptx", helpers.PPTX_SAMPLE).read_bytes()
    monkeypatch.setattr(store, "insert_import", boom)

    with pytest.raises(RuntimeError):                     # 未预期错误 → 500（不吞成"成功"）
        client.post("/api/imports", files=upload([("销售分析.pptx", data)]))

    with db.readonly() as connection:
        assert int(db.scalar("SELECT COUNT(*) FROM datasets")) == 0
        assert int(db.scalar("SELECT COUNT(*) FROM documents")) == 0
        assert int(db.scalar("SELECT COUNT(*) FROM imports")) == 0


def test_ASSERT_19_测试不写真实库与真实状态目录(env, tmp_path) -> None:
    """★ 这一条点名过"已经犯过的错"（607 条测试污染），所以它看的是**真实文件动没动**。"""
    import conftest                                       # tests/ 在 sys.path 上，直接 import

    watched = [
        PROJECT_ROOT / "data" / "app.db",
        PROJECT_ROOT / "state" / "conversations.json",
        PROJECT_ROOT / "state" / "datasets.json",
    ]
    before = {path: (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
              for path in watched}

    import_via_api(tmp_path, "地区销售.xlsx",
                   helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE}).read_bytes())

    after = {path: (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
             for path in watched}
    assert before == after
    # 会话级隔离夹具确实生效了（不是"什么都没跑"导致的假通过）
    import os

    assert os.environ["SRA_DB_PATH"] not in {str(path) for path in watched}
    assert conftest.isolated_paths()["SRA_STATE_DIR"] != str(PROJECT_ROOT / "state")


# ══════════════════════════════════════════════════════════════════════════
# D 地区
# ══════════════════════════════════════════════════════════════════════════
def _region_dataset(tmp_path: pathlib.Path):
    data = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE}).read_bytes()
    return import_via_api(tmp_path, "地区销售.xlsx", data)


def test_ASSERT_20_识别省份为地区字段(env, tmp_path) -> None:
    receipt = _region_dataset(tmp_path)
    assert [item["key"] for item in receipt["datasets"][0]["region_dimensions"]] == ["province"]


def test_ASSERT_21_地区维度出现在UI(env, tmp_path) -> None:
    """UI 面的证据：① 后端给出可用维度；② 页面按它动态生成下拉项；③ 没有就不显示这张卡。"""
    receipt = _region_dataset(tmp_path)
    availability = client.get(f"/api/sources/{receipt['dataset_id']}/region").json()
    assert availability["available"] is True
    assert availability["dimensions"][0]["label"] == "省份"

    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "renderRegionDimensions()" in js
    assert 'id="region-dimension"' in (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_ASSERT_22_按地区查询广东100浙江200江苏300(env, tmp_path) -> None:
    receipt = _region_dataset(tmp_path)
    body = client.get(f"/api/sources/{receipt['dataset_id']}/region/query").json()
    assert {row["region"]: row["amount"] for row in body["rows"]} == {
        "广东": 100.0, "浙江": 200.0, "江苏": 300.0,
    }


def test_ASSERT_23_删掉省份重新导入则地区维度消失(env, tmp_path) -> None:
    first = _region_dataset(tmp_path)
    assert first["datasets"][0]["region_dimensions"]

    plain = helpers.make_xlsx(tmp_path / "没有省份.xlsx", {"Sheet1": [["销售额"], [100], [200]]})
    second = import_via_api(tmp_path, "没有省份.xlsx", plain.read_bytes())
    assert second["datasets"][0]["region_dimensions"] == []
    assert client.get(f"/api/sources/{second['dataset_id']}/region").json()["available"] is False


def test_ASSERT_24_只有Country时地区维度不存在(env, tmp_path) -> None:
    data = helpers.make_xlsx(tmp_path / "国家.xlsx", {"Sheet1": [
        ["Country", "销售额"], ["United Kingdom", 100], ["Germany", 200],
    ]}).read_bytes()
    receipt = import_via_api(tmp_path, "国家.xlsx", data)
    assert receipt["datasets"][0]["region_dimensions"] == []


def test_ASSERT_25_只有Country时禁止把Country映射成地区(env, tmp_path) -> None:
    """从识别逻辑的**内部**证明：`Country` 这个名字连候选都进不去。"""
    assert regions._name_is_candidate("Country") is None
    assert regions._name_is_candidate("国家") is None

    data = helpers.make_xlsx(tmp_path / "国家.xlsx", {"Sheet1": [
        ["Country", "销售额"], ["United Kingdom", 100], ["Germany", 200],
    ]}).read_bytes()
    receipt = import_via_api(tmp_path, "国家.xlsx", data)
    assert receipt["datasets"][0]["region_dimensions"] == []


def test_ASSERT_26_无地区字段时直接请求返回DIMENSION_UNAVAILABLE(env, tmp_path) -> None:
    data = helpers.make_xlsx(tmp_path / "国家.xlsx", {"Sheet1": [
        ["Country", "销售额"], ["United Kingdom", 100], ["Germany", 200],
    ]}).read_bytes()
    receipt = import_via_api(tmp_path, "国家.xlsx", data)

    response = client.get(f"/api/sources/{receipt['dataset_id']}/region/query")
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == models.DIMENSION_UNAVAILABLE
    assert body["dimension"] == "region"
    assert "没有地区字段" in body["message"]
    # 没有任何按国家拆出来的数字混在里面
    assert "rows" not in body and "total_amount" not in body


# ══════════════════════════════════════════════════════════════════════════
# E 安全闸门
# ══════════════════════════════════════════════════════════════════════════
#: `_BANNED_DIMENSIONS` 的**冻结快照**（2026-09-29，FR-003 开工前的原样）。
#: ASSERT 27 说的是"FR-003 不得修改它" —— 所以这里逐字钉住，改一个字就红。
_FROZEN_BANNED_DIMENSIONS: tuple[tuple[str, ...], ...] = (
    ("VIP", "vip", "Vip", "客户等级", "会员等级", "客户级别", "客户分层",
     "大客户", "重点客户", "高价值客户", "企业客户", "个人客户", "普通客户",
     "客户行业", "客户地区", "客户地域", "客户渠道", "客户生命周期"),
    ("区域", "大区", "片区", "地区", "华南", "华东", "华北", "华中",
     "西南", "西北", "东北", "东南", "长三角", "珠三角",
     "北美", "南美", "欧洲", "亚洲", "亚太", "美洲", "大洲"),
    ("省份", "省", "城市", "门店", "店铺", "网点", "仓库", "渠道", "销售员", "业务员",
     "客户经理", "部门", "团队", "负责人",
     "上海", "北京", "广州", "深圳", "杭州", "成都", "天津", "重庆"),
    ("毛利", "利润", "成本", "折扣", "税额", "税率", "运费"),
)


def test_ASSERT_27_不得修改_BANNED_DIMENSIONS() -> None:
    """逐条与冻结快照比。**不碰它**这件事必须在代码层面可验证，而不是靠自觉。"""
    from app.ai import intent

    actual = tuple(tuple(keywords) for keywords, _reason in intent._BANNED_DIMENSIONS)
    assert actual == _FROZEN_BANNED_DIMENSIONS, "FR-003 动了 _BANNED_DIMENSIONS —— 红线"


def test_ASSERT_27_内置数据集问地区照旧被拦下() -> None:
    """行为面的另一半：`_BANNED_DIMENSIONS` 的**语义**也没变 ——
    内置数据源（只有 Country、没有地区字段）被问'按地区'时，仍然是"不支持"，不是去算。"""
    from app.ai import intent

    parsed = intent.guard_unsupported("看看各个地区的销售额")
    assert parsed is not None, "内置数据源问地区必须被代码拦下"
    assert parsed.intent == intent.INTENT_UNSUPPORTED
    assert "没有「区域」字段" in parsed.reason
    # 与地区无关的问题不受影响（闸门不是"一律拒绝"）
    assert intent.guard_unsupported("上个月卖了多少") is None


def test_ASSERT_28_不得绕过number_guard() -> None:
    """数字闸门还在，且**仍然**挡得住 LLM 编出来的数。"""
    from app.ai import answer

    report = answer.number_guard("本月销售额 999,999 元，涨了 300%", {"600"})
    assert report["checked"] is True
    assert report["passed"] is False
    assert set(report["violations"]) == {"300", "999999"}, "没在白名单里的数字必须被挑出来"
    # 白名单里真有这个数 → 通过（不是"一律报错"）
    assert answer.number_guard("本月销售额 600 元", {"600"})["passed"] is True


def test_ASSERT_29_不得绕过currency_guard() -> None:
    """币种闸门还在：口径是人民币「元」，别处出现 £/$ 要被挑出来。"""
    from app.ai import answer

    assert answer.currency_guard("本月销售额 600 元") == []
    assert answer.currency_guard("本月销售额 £600")     # 非空 = 检出了不该出现的币种


def test_ASSERT_30_地区聚合结果来自确定性代码(env, tmp_path) -> None:
    """结果必须等于**另写一条** pandas 算路算出来的数（独立 Oracle）。"""
    import pandas as pd

    receipt = _region_dataset(tmp_path)
    stored = pd.DataFrame(helpers.table_rows(receipt["dataset_id"]))
    oracle = stored.groupby("省份")["销售额"].sum().to_dict()

    body = client.get(f"/api/sources/{receipt['dataset_id']}/region/query", params={"top_n": 100}).json()
    assert {row["region"]: row["amount"] for row in body["rows"]} == \
        {str(key): float(value) for key, value in oracle.items()}
    # 而且算数那一段的源码里用的是 pandas 的 groupby（不是 SQL 聚合）
    assert ".groupby(" in inspect.getsource(region_query)


def test_ASSERT_31_LLM不直接计算地区销售额(env, tmp_path, monkeypatch) -> None:
    """把 LLM 那一层整个**弄坏**，地区查询照样给出同样的数 —— 说明它根本没参与。"""
    from app.ai import answer as answer_module
    from app.ai import llm as llm_module

    def explode(*args, **kwargs):
        raise AssertionError("地区计算不该走到 LLM 这一层")

    monkeypatch.setattr(llm_module, "chat", explode, raising=False)
    monkeypatch.setattr(answer_module, "compose", explode, raising=False)

    receipt = _region_dataset(tmp_path)
    body = client.get(f"/api/sources/{receipt['dataset_id']}/region/query").json()
    assert body["total_amount"] == 600.0


def test_ASSERT_32_LLM输出的数字继续受数字闸门约束() -> None:
    """地区这块的答案由代码直接产出（不经过 LLM）；而**凡是经过 LLM 的文本**，
    仍然要过同一道 `number_guard`（在 answer.py 里，FR-003 一行没改）。"""
    from app.ai import answer

    source = inspect.getsource(answer)
    assert "number_guard(raw_text, allowed)" in source, "数字闸门在回答链路上必须还在"
    # 编造的地区数字同样会被挡下（白名单里没有它）
    report = answer.number_guard("华东地区销售额 88888 元", {"600"})
    assert report["passed"] is False
    assert "88888" in report["violations"]
