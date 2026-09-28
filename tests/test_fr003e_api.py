"""test_fr003e_api.py · FR-003E：**API + UI 导入入口**。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么】
════════════════════════════════════════════════════════════════════════
    API   统一导入入口（预览 / 导入 / 导入记录 / 文档 / 已物化数据源 / 按地区）
          多文件**逐条**报成败；失败的文件也要看得见是为什么
    契约  错误体同时满足项目的统一形状与评审点名的结构化维度错误
          没有地区字段时：**409 + `{"code":"DIMENSION_UNAVAILABLE","dimension":"region",…}`**
    UI    页面上有「导入资料」「已入库数据源」「按地区」三块；
          「按地区」只在**数据源真的带地区字段**时才出现（没有就不显示这个入口）
    边界  前端只搬后端的结果，自己不排序、不聚合、不硬编码任何业务数字
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import fr003_helpers as helpers
from app.api import app
from app.importer import db

WEB_DIR = pathlib.Path(__file__).resolve().parents[1] / "web"
client = TestClient(app)          # 不跑 lifespan（不起预热线程），表由 fixture 建


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


def read(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def upload(files: list[tuple[str, bytes]]) -> list[tuple[str, tuple[str, bytes, str]]]:
    """多文件上传：同一个字段名 `files` **重复出现**（与前端 api.js 的 FormData 同一形状）。

    为什么是"列表套元组"而不是 `{"files": [ ... ]}`：多值字段在 multipart 里本来就是
    同名条目重复出现，httpx 只认列表形式；写成字典会发出一个格式不对的 body
    （服务端直接回 400 "There was an error parsing the body"）。
    """
    return [("files", (name, data, "application/octet-stream")) for name, data in files]


# ════════════════════════════════════════════════════════════════════════
# ① 预览：只看不落库
# ════════════════════════════════════════════════════════════════════════
def test_预览不写库(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    response = client.post("/api/imports/preview", files=upload([("地区销售.xlsx", path.read_bytes())]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok_count"] == 1

    preview = body["results"][0]
    assert preview["source_type"] == "excel"
    assert preview["will_create_dataset"] is True
    assert preview["will_create_document"] is False
    assert preview["has_region"] is True
    assert [hit["key"] for hit in preview["region_dimensions"]] == ["province"]
    # 字段与类型都摊开给用户看（导入前要能确认"会被识别成什么"）
    types = {column["name"]: column["type"] for column in preview["tables"][0]["columns"]}
    assert types == {"省份": "text", "销售额": "integer"}

    with db.readonly() as connection:
        assert int(db.scalar("SELECT COUNT(*) FROM datasets")) == 0
        assert int(db.scalar("SELECT COUNT(*) FROM imports")) == 0


def test_预览会说明PPT会双落(env, tmp_path) -> None:
    path = helpers.make_pptx(tmp_path / "销售分析.pptx", helpers.PPTX_SAMPLE)
    body = client.post(
        "/api/imports/preview", files=upload([("销售分析.pptx", path.read_bytes())])
    ).json()
    preview = body["results"][0]
    assert preview["will_create_document"] is True
    assert preview["will_create_dataset"] is True


def test_预览不支持的类型逐条报错(env) -> None:
    body = client.post(
        "/api/imports/preview", files=upload([("说明.txt", b"hello")])
    ).json()
    assert body["ok_count"] == 0
    assert body["results"][0]["error_code"] == "unsupported_type"


# ════════════════════════════════════════════════════════════════════════
# ② 导入
# ════════════════════════════════════════════════════════════════════════
def test_导入成功并给出这句话(env, tmp_path) -> None:
    path = helpers.make_pptx(tmp_path / "销售分析.pptx", helpers.PPTX_SAMPLE)
    body = client.post("/api/imports", files=upload([("销售分析.pptx", path.read_bytes())])).json()

    assert body["ok_count"] == 1 and body["failed_count"] == 0
    result = body["results"][0]
    assert result["ok"] is True
    assert result["summary_text"] == "销售分析.pptx → 已导入 1 个文档资料 + 1 个数据集"
    assert result["datasets"][0]["region_dimensions"][0]["label"] == "省份"
    assert result["original_kept"] is True


def test_多文件逐条报成败(env, tmp_path) -> None:
    """一个失败**不影响**其它：前端要能显示"3 个成功、1 个失败"，而不是整批一起红。"""
    good = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    body = client.post("/api/imports", files=upload([
        ("地区销售.xlsx", good.read_bytes()),
        ("坏文件.docx", b"not a docx at all"),
        ("说明.txt", b"hello"),
    ])).json()

    assert body["count"] == 3
    assert body["ok_count"] == 1
    assert body["failed_count"] == 2
    by_name = {item["filename"]: item for item in body["results"]}
    assert by_name["地区销售.xlsx"]["ok"] is True
    assert by_name["坏文件.docx"]["error_code"] == "signature_mismatch"
    assert by_name["说明.txt"]["error_code"] == "unsupported_type"
    assert by_name["说明.txt"]["rejected"] is True


def test_重复导入返回同一份import_id(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    data = path.read_bytes()
    first = client.post("/api/imports", files=upload([("地区销售.xlsx", data)])).json()
    second = client.post("/api/imports", files=upload([("地区销售.xlsx", data)])).json()

    assert second["results"][0]["idempotent"] is True
    assert second["results"][0]["import_id"] == first["results"][0]["import_id"]


def test_没有文件时报错而不是装作成功(env) -> None:
    response = client.post("/api/imports", files={"files": []})
    assert response.status_code in (400, 422)


# ════════════════════════════════════════════════════════════════════════
# ③ 导入记录 / 文档 / 已物化数据源
# ════════════════════════════════════════════════════════════════════════
def test_导入记录列表与单条详情(env, tmp_path) -> None:
    path = helpers.make_md(tmp_path / "季度分析.md", helpers.MD_SAMPLE)
    receipt = client.post(
        "/api/imports", files=upload([("季度分析.md", path.read_bytes())])
    ).json()["results"][0]

    listing = client.get("/api/imports").json()
    assert listing["total"] == 1
    assert listing["imports"][0]["import_id"] == receipt["import_id"]
    assert listing["imports"][0]["status_label"] == "已入库"

    detail = client.get(f"/api/imports/{receipt['import_id']}").json()
    assert detail["dataset"] is not None
    assert detail["source_file"]["dataset_count"] == 1
    assert detail["source_file"]["document_count"] == 1
    assert "已导入" in detail["source_file"]["summary_text"]


def test_导入记录不存在时404(env) -> None:
    response = client.get("/api/imports/imp_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "import_not_found"


def test_导入进来的文档正文能取到(env, tmp_path) -> None:
    path = helpers.make_md(tmp_path / "季度分析.md", helpers.MD_SAMPLE)
    receipt = client.post(
        "/api/imports", files=upload([("季度分析.md", path.read_bytes())])
    ).json()["results"][0]

    # 默认不带全文（列表/详情不该塞整篇正文），要正文得显式要
    light = client.get(f"/api/imports/documents/{receipt['document_id']}").json()
    assert "text" not in light
    assert light["char_count"] > 0
    assert "整体销售向好" in light["text_preview"]

    body = client.get(
        f"/api/imports/documents/{receipt['document_id']}",
        params={"include_text": "true"},
    ).json()
    assert "整体销售向好" in body["text"]
    assert "|" not in body["text"], "表格已经物化成数据集，正文里不该再糊一遍管道符"


def test_已物化数据源列表与详情(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = client.post(
        "/api/imports", files=upload([("地区销售.xlsx", path.read_bytes())])
    ).json()["results"][0]

    listing = client.get("/api/sources").json()
    assert listing["total"] == 1
    source = listing["sources"][0]
    assert source["dataset_id"] == receipt["dataset_id"]
    assert source["has_region"] is True
    assert source["status_label"] == "已入库"
    assert source["original_kept"] is True

    detail = client.get(f"/api/sources/{receipt['dataset_id']}").json()
    assert detail["tables"][0]["row_count"] == 3
    assert any(column["region_key"] == "province" for column in detail["columns"])


# ════════════════════════════════════════════════════════════════════════
# ④ 地区维度接口（含 ASSERT 26 的响应体形状）
# ════════════════════════════════════════════════════════════════════════
def test_有地区字段时可用性为真(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    dataset_id = client.post(
        "/api/imports", files=upload([("地区销售.xlsx", path.read_bytes())])
    ).json()["results"][0]["dataset_id"]

    body = client.get(f"/api/sources/{dataset_id}/region").json()
    assert body["available"] is True
    assert [item["key"] for item in body["dimensions"]] == ["province"]


def test_按地区查询返回广东100浙江200江苏300(env, tmp_path) -> None:
    """ASSERT 21/22 的接口面：维度在、数字对。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    dataset_id = client.post(
        "/api/imports", files=upload([("地区销售.xlsx", path.read_bytes())])
    ).json()["results"][0]["dataset_id"]

    body = client.get(f"/api/sources/{dataset_id}/region/query").json()
    assert {row["region"]: row["amount"] for row in body["rows"]} == {
        "广东": 100.0, "浙江": 200.0, "江苏": 300.0,
    }
    assert body["dimension"]["label"] == "省份"


def test_没有地区字段时按地区返回结构化错误(env, tmp_path) -> None:
    """★ ASSERT 26：**直接调用**也必须拿到那个结构化错误，而不是 500 或一个空数组。"""
    path = helpers.make_xlsx(tmp_path / "国家.xlsx", {"Sheet1": [
        ["Country", "销售额"], ["United Kingdom", 100], ["Germany", 200],
    ]})
    dataset_id = client.post(
        "/api/imports", files=upload([("国家.xlsx", path.read_bytes())])
    ).json()["results"][0]["dataset_id"]

    availability = client.get(f"/api/sources/{dataset_id}/region").json()
    assert availability["available"] is False

    response = client.get(f"/api/sources/{dataset_id}/region/query")
    assert response.status_code == 409
    body = response.json()
    # 评审点名的三个字段，就摆在最外层
    assert body["code"] == "DIMENSION_UNAVAILABLE"
    assert body["dimension"] == "region"
    assert body["message"] == "「国家」没有地区字段 —— 按地区看数据需要数据源本身带地区列（如 大区/省份/城市）。"
    # 项目冻结的统一错误形状也还在（纯增量，老客户端不受影响）
    assert body["error"]["code"] == "DIMENSION_UNAVAILABLE"
    assert body["detail"] == body["error"]["message"]
    # **没有**任何按国家拆出来的数字
    assert "rows" not in body


def test_数据源不存在时404(env) -> None:
    assert client.get("/api/sources/ds_nope").status_code == 404
    assert client.get("/api/sources/ds_nope/region/query").status_code == 404


# ════════════════════════════════════════════════════════════════════════
# ⑤ 前端：入口在、维度按数据源动态出、不硬编码业务数字
# ════════════════════════════════════════════════════════════════════════
def test_页面上有导入与已入库数据源两块() -> None:
    html = read("index.html")
    assert 'id="imp-file-input"' in html
    assert 'id="btn-imp-preview"' in html and 'id="btn-imp-run"' in html
    assert 'id="sources-body"' in html
    assert 'id="region-card"' in html
    # 一个入口支持六种格式（不是给每种格式各开一个入口）
    assert ".pptx" in html and ".md" in html and ".docx" in html and ".pdf" in html
    assert 'multiple' in html


def test_按地区只在数据源真有地区字段时才出现() -> None:
    """★ 评审 #5：禁止"假能力入口"（点了才说本数据源没有地区字段）。

    证据（静态层）：① 卡片默认为 `hidden`；② 只有拿到带地区字段的数据源时才会 `show`；
    ③ 数据源下拉里只列 `has_region` 为真的那些。
    """
    html, js = read("index.html"), read("app.js")
    assert re.search(r'id="region-card"[^>]*hidden', html), "按地区卡片默认必须是收起的"
    assert "function regionSources()" in js
    assert "item.has_region" in js, "下拉里只能列带地区字段的数据源"
    assert re.search(r"if \(!usable\.length\) \{\s*hide\(card\);\s*return;", js), \
        "没有可用的数据源时必须把整张卡收起来"
    # 可用性判断走后端那条端点（前端不自己看列名猜"有没有地区字段"）
    assert "sourceRegion:" in read("api.js")


def test_前端不做计算只搬后端结果() -> None:
    """地区那一块只允许把这些字段原样画出来，不许自己 sum / 排序。"""
    js = read("app.js")
    region_section = js[js.index("function renderRegionRows"): js.index("async function inspectDatasetFile")]
    assert ".sort(" not in region_section and ".reduce(" not in region_section
    assert "row.share" in region_section          # 占比也是后端给的
    assert "payload.total_amount" in region_section


def test_前端不硬编码业务数字() -> None:
    js = read("app.js") + read("api.js")
    for banned in ("600", "19950", "316412"):
        assert banned not in js, f"前端出现了疑似写死的业务数字：{banned}"


def test_统一导入沿用既有的五态与守卫() -> None:
    """导入是**要权限**的动作（与既有导入向导同一档），游客连文件都不选上传。"""
    html = read("index.html")
    segment = html[html.index('id="btn-imp-preview"') - 200: html.index('id="btn-imp-run"') + 60]
    assert 'data-guard="import"' in segment
