"""test_fr003d_regions.py · FR-003D：**地区维度识别 + 确定性计算**（本 TASK 的核心）。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么】
════════════════════════════════════════════════════════════════════════
    识别    四项条件同时满足才算地区字段（字段名 + 非空率 + 唯一值个数 + 离散分类）
    分类    门店 → 组织类、渠道 → 渠道类，**不许塞进地区**
    不猜    同时有大区/省份/城市时**不替你挑一个**（要问用户）
    缺席    没有地区字段 → 维度不存在；后端**结构化报错**，绝不 fallback 到 Country
    算数    地区聚合的结果必须等于**另一条独立算路**（pandas 直接 groupby）算出来的数
    口径    交易明细型数据源走 D16 掩码（取消单 / 负数量 / 非正单价），与问答链路同一套
"""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

import fr003_helpers as helpers
from app.importer import db, models, normalize, pipeline, region_query, regions


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


def _profiles(columns: list[str], rows: list[list]) -> list[dict]:
    _names, _rows, profiles = normalize.normalize_table(columns, rows)
    return [dict(profile) for profile in profiles]


def _hit(columns: list[str], rows: list[list]) -> list[dict]:
    return regions.detect_dimensions(_profiles(columns, rows))


# ════════════════════════════════════════════════════════════════════════
# ① 识别（评审 #4：四条同时满足）
# ════════════════════════════════════════════════════════════════════════
def test_省份是地区字段(env) -> None:
    """ASSERT 20：`{广东:100, 浙江:200, 江苏:300}` → 认出「省份」。"""
    hits = _hit(["省份", "销售额"], [["广东", 100], ["浙江", 200], ["江苏", 300]])
    assert [hit["key"] for hit in hits] == ["province"]
    assert hits[0]["field"] == "省份"
    assert hits[0]["category"] == "region"


@pytest.mark.parametrize("name,key", [
    ("大区", "region"), ("区域", "region"), ("销售区域", "region"),
    ("省份", "province"), ("省", "province"), ("所在省", "province"),
    ("城市", "city"), ("地市", "city"),
    ("区县", "district"), ("所属县", "district"),
])
def test_候选项清单里的名字都认得出(name, key) -> None:
    hits = _hit([name], [["A"], ["B"], ["C"]])
    assert hits and hits[0]["key"] == key


@pytest.mark.parametrize("name", ["门店", "店铺", "网点"])
def test_门店算组织类_不进地区(name) -> None:
    """评审 #4 原话：「门店…有时只是销售组织」。所以它是别的类别，不是地区。"""
    hits = _hit([name], [["A店"], ["B店"], ["C店"]])
    assert hits and hits[0]["key"] == "store"
    assert hits[0]["category"] == "other"
    assert regions.region_dimensions(_profiles([name], [["A店"], ["B店"]])) == []


@pytest.mark.parametrize("name", ["渠道", "销售渠道"])
def test_渠道不进地区(name) -> None:
    """线上/线下/经销商跟地理毫无关系 —— 塞进「按地区」是分类错误。"""
    hits = _hit([name], [["线上"], ["线下"], ["经销商"]])
    assert hits and hits[0]["key"] == "channel"
    assert regions.region_dimensions(_profiles([name], [["线上"], ["线下"]])) == []


def test_不是只看字段名_自由文本不算地区() -> None:
    """「地区说明」这种长描述列：名字像，值是自由文本 → **必须被挡掉**。"""
    descriptions = [
        ["北京市朝阳区某街道的仓库因为下雨导致配送延迟两天，已经和客户沟通过了"],
        ["上海市浦东新区的新仓库提前一周完成验收，本季度可以投入使用，容量翻倍"],
        ["广州市天河区的门店装修期间暂停营业，预计下个月月中重新开业并做促销"],
    ]
    assert regions.detect_dimensions(_profiles(["地区说明"], descriptions)) == []


def test_不是只看字段名_唯一值过多的不算() -> None:
    """每一行都不一样的列是标识，不是分类。"""
    values = [[f"地区编号{i:04d}"] for i in range(regions.REGION_MAX_DISTINCT + 20)]
    assert regions.detect_dimensions(_profiles(["地区"], values)) == []


def test_不是只看字段名_几乎全空的列不算() -> None:
    values = [["广东"], [None], [None], [None], [None], [None]]
    assert regions.detect_dimensions(_profiles(["省份"], values)) == []


def test_不是只看字段名_只有一个取值的列不算() -> None:
    assert regions.detect_dimensions(_profiles(["省份"], [["广东"], ["广东"]])) == []


def test_不是只看字段名_数值列不算地区() -> None:
    """「省份代码 1001」是数字，不是分类 —— 类型不对就出局。"""
    assert regions.detect_dimensions(_profiles(["省份"], [[1001], [1002], [1003]])) == []


def test_单字候选不许按子串乱匹配() -> None:
    """`上市时间` 不含"市"，`区域经理姓名` 不是地区 —— 单字候选只认完全相等或短后缀。"""
    assert regions.detect_dimensions(_profiles(["上市时间"], [["2011"], ["2012"]])) == []
    assert regions.detect_dimensions(_profiles(["区域经理"], [["张三"], ["李四"]])) == []


# ════════════════════════════════════════════════════════════════════════
# ② 不猜：多个地区字段时把选择权交回用户
# ════════════════════════════════════════════════════════════════════════
def test_多个地区字段全部保留且不替你挑(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "多级地区.xlsx", {"Sheet1": [
        ["大区", "省份", "城市", "销售额"],
        ["华东", "江苏", "南京", 100],
        ["华东", "浙江", "杭州", 200],
        ["华南", "广东", "深圳", 300],
    ]})
    receipt = pipeline.import_file(path)
    keys = [item["key"] for item in receipt["datasets"][0]["region_dimensions"]]
    assert keys == ["region", "province", "city"], "宏观到微观的顺序，三个都留着"
    with pytest.raises(models.ImporterError) as caught:
        region_query.sales_by_region(receipt["dataset_id"])
    assert caught.value.code == "DIMENSION_AMBIGUOUS"
    assert caught.value.extra["dimension"] == "region"


def test_指定维度就按它算(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "多级地区.xlsx", {"Sheet1": [
        ["大区", "省份", "销售额"],
        ["华东", "江苏", 100],
        ["华东", "浙江", 200],
        ["华南", "广东", 300],
    ]})
    receipt = pipeline.import_file(path)
    by_region = region_query.sales_by_region(receipt["dataset_id"], dimension="region")
    by_province = region_query.sales_by_region(receipt["dataset_id"], dimension="province")
    assert {row["region"]: row["amount"] for row in by_region["rows"]} == {
        "华东": 300.0, "华南": 300.0,
    }
    assert {row["region"]: row["amount"] for row in by_province["rows"]} == {
        "江苏": 100.0, "浙江": 200.0, "广东": 300.0,
    }


# ════════════════════════════════════════════════════════════════════════
# ③ 没有地区字段：不出现、且后端拦得住
# ════════════════════════════════════════════════════════════════════════
def test_只有国家时地区维度不存在(env, tmp_path) -> None:
    """ASSERT 24：`Country` 不是地区字段。"""
    path = helpers.make_xlsx(tmp_path / "国家.xlsx", {"Sheet1": [
        ["Country", "销售额"], ["United Kingdom", 100], ["Germany", 200], ["France", 300],
    ]})
    receipt = pipeline.import_file(path)
    assert receipt["datasets"][0]["region_dimensions"] == []


def test_只有国家时禁止把国家映射成地区(env, tmp_path) -> None:
    """ASSERT 25 + ASSERT 26：直接请求按地区 → **结构化报错**，绝不拿国家顶替。"""
    path = helpers.make_xlsx(tmp_path / "国家.xlsx", {"Sheet1": [
        ["Country", "销售额"], ["United Kingdom", 100], ["Germany", 200],
    ]})
    receipt = pipeline.import_file(path)

    with pytest.raises(models.ImporterError) as caught:
        region_query.sales_by_region(receipt["dataset_id"])

    error = caught.value
    assert error.code == models.DIMENSION_UNAVAILABLE
    assert error.extra["dimension"] == "region"
    assert "没有地区字段" in error.message
    # 结果里**没有**任何按国家拆出来的数字（不是"算出来了但没显示"）
    assert "rows" not in error.extra


def test_后台的维度可用性接口也照实说(env, tmp_path) -> None:
    """前端据此**不显示**「按地区」；后端这道门是防线，不是"只靠前端隐藏"。"""
    path = helpers.make_xlsx(tmp_path / "国家.xlsx", {"Sheet1": [["Country", "销售额"], ["UK", 1], ["DE", 2]]})
    receipt = pipeline.import_file(path)
    view = region_query.region_view(receipt["dataset_id"])
    assert view["available"] is False
    assert view["dimensions"] == []
    assert view["message"] == "当前数据源没有地区字段"


def test_删掉省份那一列重新导入地区维度就消失了(env, tmp_path) -> None:
    """ASSERT 23。"""
    with_province = helpers.make_xlsx(tmp_path / "有省份.xlsx", {
        "Sheet1": helpers.REGION_SAMPLE,
    })
    first = pipeline.import_file(with_province)
    assert first["datasets"][0]["region_dimensions"]

    without = helpers.make_xlsx(tmp_path / "没有省份.xlsx", {
        "Sheet1": [["销售额"], [100], [200], [300]],
    })
    second = pipeline.import_file(without)
    assert second["datasets"][0]["region_dimensions"] == []
    with pytest.raises(models.ImporterError) as caught:
        region_query.sales_by_region(second["dataset_id"])
    assert caught.value.code == models.DIMENSION_UNAVAILABLE


# ════════════════════════════════════════════════════════════════════════
# ④ 算数：结果必须等于另一条独立算路
# ════════════════════════════════════════════════════════════════════════
def test_按地区查询返回广东100_浙江200_江苏300(env, tmp_path) -> None:
    """ASSERT 22：评审给的样例，数字一个不差。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = pipeline.import_file(path)
    result = region_query.sales_by_region(receipt["dataset_id"])

    assert {row["region"]: row["amount"] for row in result["rows"]} == {
        "广东": 100.0, "浙江": 200.0, "江苏": 300.0,
    }
    assert result["total_amount"] == 600.0
    assert result["dimension"]["key"] == "province"


def test_结果与独立算路一致(env, tmp_path) -> None:
    """ASSERT 30 的证据：拿库里的行**另写一条 pandas 算路**，两边必须逐位相等。"""
    rows = [["省份", "销售额"]] + [[f"省{i % 7}", (i * 37) % 500] for i in range(120)]
    path = helpers.make_xlsx(tmp_path / "多行.xlsx", {"Sheet1": rows})
    receipt = pipeline.import_file(path)

    stored = pd.DataFrame(helpers.table_rows(receipt["dataset_id"]))
    oracle = stored.groupby("省份")["销售额"].sum()

    result = region_query.sales_by_region(receipt["dataset_id"], top_n=None)
    from_api = {row["region"]: row["amount"] for row in result["rows"]}
    assert from_api == {str(key): float(value) for key, value in oracle.items()}


def test_地区为空的行走不进任何地区但要说清楚(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "空地区.xlsx", {"Sheet1": [
        ["省份", "销售额"], ["广东", 100], [None, 50], ["浙江", 200],
    ]})
    receipt = pipeline.import_file(path)
    result = region_query.sales_by_region(receipt["dataset_id"])

    assert {row["region"]: row["amount"] for row in result["rows"]} == {"广东": 100.0, "浙江": 200.0}
    assert result["rows_skipped_no_region"] == 1
    assert any("没有地区值" in note for note in result["notes"])
    assert result["total_amount"] == 300.0, "空地区的那 50 不该被算进任何地区"


# ════════════════════════════════════════════════════════════════════════
# ⑤ 交易明细型数据源：走与问答链路同一套 D16 口径
# ════════════════════════════════════════════════════════════════════════
def _transaction_rows() -> list[list]:
    return [
        ["InvoiceNo", "省份", "Quantity", "UnitPrice", "InvoiceDate"],
        ["1001", "广东", 2, 50.0, "2011-01-05"],       # 100 有效
        ["1002", "广东", 1, 30.0, "2011-01-06"],       # 30 有效
        ["C1003", "广东", 1, 999.0, "2011-01-07"],     # 取消单 → 排除
        ["1004", "浙江", -3, 20.0, "2011-01-08"],      # 负数量 → 排除
        ["1005", "浙江", 4, 0.0, "2011-01-09"],        # 零单价 → 排除
        ["1006", "浙江", 5, 40.0, "2011-01-10"],       # 200 有效
    ]


def test_交易明细按数量乘单价并套D16掩码(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "交易明细.xlsx", {"Sheet1": _transaction_rows()})
    receipt = pipeline.import_file(path)
    result = region_query.sales_by_region(receipt["dataset_id"])

    assert result["measure"]["kind"] == "quantity_x_unitprice"
    assert result["measure"]["apply_d16_mask"] is True
    assert {row["region"]: row["amount"] for row in result["rows"]} == {
        "广东": 130.0, "浙江": 200.0,
    }


def test_交易明细按时间窗筛选走含首尾全天(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "交易明细.xlsx", {"Sheet1": _transaction_rows()})
    receipt = pipeline.import_file(path)
    result = region_query.sales_by_region(
        receipt["dataset_id"], start="2011-01-05", end="2011-01-09",
    )
    # 01-05 与 01-06 有效；01-07 取消、01-08 负数量、01-09 零单价 —— 都落在窗内但都被排除
    assert {row["region"]: row["amount"] for row in result["rows"]} == {"广东": 130.0}
    assert result["window"]["semantics"] == "含首尾全天（闭区间 start 00:00:00 ~ end 23:59:59.999）"


def test_没有日期列却要按时间段筛选时明确报错(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = pipeline.import_file(path)
    with pytest.raises(models.ImporterError) as caught:
        region_query.sales_by_region(receipt["dataset_id"], start="2011-01-01", end="2011-01-31")
    assert caught.value.code == "window_unavailable"


def test_既没有金额列也没有数量单价时明确报错(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "只有地区.xlsx", {"Sheet1": [
        ["省份", "备注"], ["广东", "a"], ["浙江", "b"],
    ]})
    receipt = pipeline.import_file(path)
    with pytest.raises(models.ImporterError) as caught:
        region_query.sales_by_region(receipt["dataset_id"])
    assert caught.value.code == "measure_unavailable"


# ════════════════════════════════════════════════════════════════════════
# ⑥ ASSERT 31：LLM 不参与地区销售额的计算
# ════════════════════════════════════════════════════════════════════════
def test_地区计算这一层完全不碰LLM() -> None:
    """**代码级证据**：这条链路里的每个模块都没有任何 LLM / AI 的引用。

    ASSERT 31 说的"LLM 不直接计算地区销售额"，在实现上就是"这条链路里根本没有 LLM"：
    取数在 frames.py、持久化在 store.py、计算在 region_query.py（pandas），
    三个都不 import app.ai / openai / llm —— 没有 LLM，它就没有机会算错。
    """
    from app.importer import frames, store

    source = (
        inspect.getsource(region_query)
        + inspect.getsource(frames)
        + inspect.getsource(store)
    )
    for banned in ("app.ai", "openai", "llm", "deepseek", "prompt", "completion"):
        assert banned not in source, f"地区计算这层出现了 LLM 相关引用：{banned}"


def test_地区计算只依赖确定性代码与SQL取数(env, tmp_path) -> None:
    """ASSERT 30 的代码级证据：计算用的是 pandas 的 groupby，不是 SQL 聚合。

    直接查 `region_query` 的源码：聚合调用必须出现在 Python 里（`.groupby(`），
    而 SQL 那一侧只允许出现 `SELECT payload`（`store.load_rows` 只取行，不聚合）。
    """
    from app.importer import store

    compute_source = inspect.getsource(region_query)
    assert ".groupby(" in compute_source
    store_source = inspect.getsource(store.load_rows)
    assert "SUM(" not in store_source.upper()
    assert "GROUP BY" not in store_source.upper()
