"""test_fr008_region_chat.py · FR-008：**把「按地区」接进聊天**（地区意图 + 白名单工具）。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么】
════════════════════════════════════════════════════════════════════════
    A 正路    导入带「省份」的数据 → 问「各省份的销售额」→ 三条地区 + 与
              `/api/sources/{id}/region/query` **位级相等**（同一个数，不是"差不多"）
    B 分工    问「国家」照旧走 country 工具（Legacy 冻结，不许被地区抢走）
    C 缺席    没有任何带地区字段的数据源时问地区 → 明确说明 + **不含任何国家数字** + 非 5xx
    D 拒绝    问数据里没有的地区名（华南区）→ 照旧明确拒绝，不许编
    E 闸门    intent / tools / region_query 三层关系：工具就是那条确定性实现，没有第二套聚合
    F 选择    多个带地区字段的数据源 → 最近导入优先，且回答里**说明用的是哪一个**
    G 澄清    一个数据源有多个地区字段而用户没指明 → 先问一句，不替他挑
    H 能力    能力清单里的「地区分布」只在真有地区数据源时出现（假能力入口的防线）
    I 隔离    全程用隔离的 SRA_STATE_DIR / SRA_DB_PATH（不许碰真实库）

数字一律与 `region_query.sales_by_region()` 直接比对（那是 FR-003D 的确定性实现）——
测试**不重算**，只比对两条路径给的是不是同一个数。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import fr003_helpers as helpers
from app.api import app
from app.ai import intent as intent_module, region_source, service, tools
from app.importer import region_query

client = TestClient(app)

#: 评审给的样例（广东100 / 浙江200 / 江苏300），一字不改
REGION_SAMPLE = helpers.REGION_SAMPLE


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


def upload(files: list[tuple[str, bytes]]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, data, "application/octet-stream")) for name, data in files]


def import_region_csv(tmp_path, rows: list[list] | None = None, name: str = "地区销售.csv") -> dict:
    """真上传一份带「省份」列的 CSV（走 /api/imports 真管道，不 mock）。"""
    table = rows or REGION_SAMPLE
    text = "\n".join(",".join(str(cell) for cell in row) for row in table) + "\n"
    path = helpers.make_csv(tmp_path / name, text)
    body = client.post("/api/imports", files=upload([(name, path.read_bytes())])).json()
    assert body["ok_count"] == 1, body
    return body["results"][0]


def ask(question: str, **kwargs) -> dict:
    response = client.post("/api/chat", json={"question": question, "use_llm": False, **kwargs})
    assert response.status_code < 500, response.text
    return response.json()


def region_map(rows: list[dict]) -> dict[str, float]:
    return {str(row["region"]): float(row["amount"]) for row in rows}


# ════════════════════════════════════════════════════════════════════════
# A · 正路：导入带省份的数据 → 问「各省份的销售额」
# ════════════════════════════════════════════════════════════════════════
def test_A_各省份的销售额走确定性地区实现且数字位级相等(env, tmp_path):
    receipt = import_region_csv(tmp_path)
    dataset_id = receipt["dataset_id"]
    oracle = region_map(region_query.sales_by_region(dataset_id, top_n=100)["rows"])
    assert oracle == {"广东": 100.0, "浙江": 200.0, "江苏": 300.0}      # 先钉住事实本身

    record = ask("各省份的销售额")
    assert record["status"] == "degraded", record["notice"]          # 未接 LLM → 代码写的那两段
    # 走的是 FR-007 的路由层：销售类意图 → analysis 档（**没有**绕开路由自己拼回答）
    assert record["routing"]["intent"] == "sales_analysis"
    assert record["response_mode"] == "analysis"
    assert record["sales_data_accessed"] is True
    assert record["intent"]["intent"] == "sales_by_region"
    assert record["tool"]["name"] == "sales_by_region"
    assert record["tool"]["title"] == "地区分布"
    # ★ 位级相等：不是 approx，是同一个字典（同一个 pandas 实现算出来的）
    assert region_map(record["items"]) == oracle
    assert record["facts"]["total_amount"] == 600.0
    assert record["facts"]["source_dataset"]["dataset_id"] == dataset_id
    # 表格 + 合计都在（前端就是按 display 渲染"指标/数值/单位"那张表）
    labels = [row["label"] for row in record["tool"]["display"]]
    assert "总销售额" in labels and any("合计" in label for label in labels)
    # 回答正文里三条地区都出现（表格形式的明细）
    what = record["answer"]["sections"][0]["text"]
    for region in ("广东", "浙江", "江苏"):
        assert region in what, what
    # ★ 回答里说明了用的是哪个数据源（不是"神秘地算了个数"）
    assert "地区销售" in record["notice"]
    assert "没有拿 Country（国家）代替地区" in record["notice"]
    # 与接口两条路径同源：HTTP 那条也给出同一组数
    api_rows = client.get(f"/api/sources/{dataset_id}/region/query", params={"top_n": 100}).json()
    assert region_map(api_rows["rows"]) == region_map(record["items"])


def test_A2_按地区看销售额与大区分布也认(env, tmp_path):
    """没有时间词的三种问法都要认出来（**带时间词的**在 A3 单列：数据源没有日期列时不许硬筛）。"""
    import_region_csv(tmp_path)
    for question in ("按地区看销售额", "各地区销售额", "各省销售额", "大区分布"):
        record = ask(question)
        assert record["intent"]["intent"] == "sales_by_region", question
        assert record["tool"]["name"] == "sales_by_region", question
        assert region_map(record["items"]) == {"广东": 100.0, "浙江": 200.0, "江苏": 300.0}, question


def test_A3_没写时间就不筛时间_写了时间而数据源没有日期列则如实报错(env, tmp_path):
    """地区数据源可以没有日期列 —— 问句里没有时间时**不许**硬塞一个内置数据集区间进去。

    这一条防的是真实会踩的坑：内置数据集的默认区间是 2010-12 ~ 2011-12，
    拿它去筛一份 2019 年的导入数据，只会筛出 0 行（用户明明没问时间）。
    """
    # ① 数据源有日期列、问句没写时间 → 不筛（window 为 None），全部行都算
    dated = helpers.make_csv(
        tmp_path / "带日期.csv",
        "省份,销售额,日期\n广东,100,2019-03-01\n浙江,200,2019-03-02\n",
    )
    body = client.post("/api/imports", files=upload([("带日期.csv", dated.read_bytes())])).json()
    assert body["ok_count"] == 1, body
    record = ask("各省份的销售额")
    assert record["status"] == "degraded", record["notice"]
    assert record["facts"]["window"] is None                     # 确实没筛时间
    assert region_map(record["items"]) == {"广东": 100.0, "浙江": 200.0}
    # ② 同一份数据源、问句**真写了**时间 → 按那个时间窗筛（2019-03-01 当天只有广东）
    narrowed = ask("2019年3月1日各省份的销售额")
    assert narrowed["status"] == "degraded"
    assert narrowed["facts"]["window"]["start"] == "2019-03-01"
    assert region_map(narrowed["items"]) == {"广东": 100.0}
    # ③ 数据源**没有**日期列、却问了时间 → 如实报错（不是静默忽略时间条件、也不是 500）
    import_region_csv(tmp_path, name="无日期.csv")
    asked = ask("2011年11月各省份的销售额")
    assert asked["status"] == "unsupported"
    assert asked["facts"] is None
    assert "日期" in asked["notice"]


# ════════════════════════════════════════════════════════════════════════
# B · 分工：问「国家」照旧走 country（Legacy 冻结）
# ════════════════════════════════════════════════════════════════════════
def test_B_问国家仍然走country工具(env, tmp_path):
    import_region_csv(tmp_path)              # 就算库里有地区数据源，国家问题也不许被抢走
    record = ask("2011年11月销售额最高的10个国家")
    assert record["intent"]["intent"] == "sales_breakdown_by_country"
    assert record["tool"]["name"] == "sales_breakdown_by_country"
    assert record["facts"]["top_n"] == 10
    assert all("country" in item for item in record["items"])
    assert all("region" not in item for item in record["items"])
    # 两个 intent 的分工在词表层也是分开的：国家词不进地区词表，反之亦然
    assert intent_module.region_dimension_words("2011年11月销售额最高的10个国家") == []
    assert intent_module.region_dimension_words("各省份的销售额") != []


# ════════════════════════════════════════════════════════════════════════
# C · 缺席：一个带地区字段的数据源都没有
# ════════════════════════════════════════════════════════════════════════
def test_C_没有地区数据源时明确说明且不含任何国家数字(env, tmp_path):
    # 先导入一份**没有**地区列的数据（只有 Country 那种），确保"库里有数据但没有地区维度"
    path = helpers.make_csv(tmp_path / "只有国家.csv", "Country,销售额\nUK,100\nGermany,200\n")
    imported = client.post("/api/imports", files=upload([("只有国家.csv", path.read_bytes())])).json()
    assert imported["ok_count"] == 1, imported
    assert imported["results"][0]["datasets"][0]["region_dimensions"] == []

    response = client.post("/api/chat", json={"question": "按地区看销售额", "use_llm": False})
    assert response.status_code < 500                                  # 不是 5xx
    record = response.json()
    assert record["status"] == "unsupported"
    assert record["intent"]["intent"] == "unsupported"
    assert record["tool"] is None and record["facts"] is None
    reason = record["intent"]["reason"]
    assert "没有" in reason and "地区" in reason
    # ★ 怎么获得说清楚了（导入带地区列的数据）
    assert "导入" in reason and "省份" in reason
    # ★ 响应里不含任何国家维度的数字：facts 为 null、items 为 null，
    #   连"某个维度名 → 某个金额"的形状都不存在（想看也没得看）
    blob = json.dumps(record, ensure_ascii=False, default=str)
    assert '"facts": null' in blob and '"items": null' in blob
    assert '"amount"' not in blob                      # 一个金额字段都没有
    assert "UK" not in blob and "Germany" not in blob  # 也没有把国家名搬出来


def test_C2_没有地区数据源时能力清单里也不写地区分布(env):
    caps = client.get("/api/chat/capabilities").json()
    names = [item["name"] for item in caps["intents"]]
    assert "sales_by_region" not in names                       # 假能力入口的防线
    assert "区域/大区/片区" in caps["unsupported"]["dimensions"]  # 声明里照旧说清
    assert "省份/城市" in caps["unsupported"]["dimensions"]
    assert region_source.available() is False


def test_C3_有地区数据源时能力清单才写进地区分布(env, tmp_path):
    import_region_csv(tmp_path)
    caps = client.get("/api/chat/capabilities").json()
    names = [item["name"] for item in caps["intents"]]
    assert names[-1] == "sales_by_region"
    assert caps["intents"][-1]["title"] == "地区分布"
    assert "地区销售" in caps["unsupported"]["reason"]            # 说明当前用的是哪份数据源
    assert region_source.available() is True


# ════════════════════════════════════════════════════════════════════════
# D · 拒绝：数据里没有的地区名，不许编
# ════════════════════════════════════════════════════════════════════════
def test_D_数据里没有的地区名照旧明确拒绝(env, tmp_path):
    import_region_csv(tmp_path)
    record = ask("华南区2011年11月卖了多少")
    assert record["status"] == "unsupported"
    assert record["intent"]["intent"] == "unsupported"
    assert record["tool"] is None and record["facts"] is None
    assert "华南" in record["intent"]["reason"]
    # 没有编数字，也没有悄悄用导入的地区数据回答
    blob = json.dumps(record, ensure_ascii=False, default=str)
    for number in ("600", "300", "200", "100"):
        assert f'"amount": {number}' not in blob
    assert "广东" not in blob and "浙江" not in blob


def test_D2_有地区数据源但问的不是金额时也说清楚(env, tmp_path):
    import_region_csv(tmp_path)
    record = ask("各省份的退货率")
    assert record["status"] == "unsupported"
    assert record["facts"] is None
    # 说清"地区这块只做销售额分布"，而不是含糊其辞

    assert "销售额" in record["intent"]["reason"]


# ════════════════════════════════════════════════════════════════════════
# E · 闸门：只有一条确定性实现，工具就是它的出口
# ════════════════════════════════════════════════════════════════════════
def test_E_工具直接复用region_query没有第二套聚合():
    import inspect

    source = inspect.getsource(tools.sales_by_region)
    assert "region_query.sales_by_region(" in source        # 调的是 FR-003D 那条实现
    # 自己不算：分组/聚合一个都没有（只有把已经算好的行再加一遍的 math.fsum，
    # 那是"给合计栏看的"，不是第二条计算路径）
    assert "groupby" not in source and ".agg(" not in source and "pivot_table" not in source
    # 白名单里确实注册了这个工具（intent 名与工具名逐字相同才能被 run_tool 调到）
    assert "sales_by_region" in tools.TOOLS
    assert intent_module.INTENT_SALES_BY_REGION == tools.TOOLS["sales_by_region"].name


def test_E2_LLM那一层坏掉也不影响地区数字(env, tmp_path, monkeypatch):
    """把 LLM 整层弄坏，地区问答照样给出同样的数 —— 说明它根本没参与算数。"""
    from app.ai import llm as llm_module

    def explode(*args, **kwargs):
        raise AssertionError("地区问答不该走到 LLM 这一层（未接 LLM 时）")

    monkeypatch.setattr(llm_module, "chat", explode)
    import_region_csv(tmp_path)
    record = ask("各省份的销售额")
    assert region_map(record["items"]) == {"广东": 100.0, "浙江": 200.0, "江苏": 300.0}


def test_E3_参数层拒绝越界的top_n与倒置的日期(env, tmp_path):
    import_region_csv(tmp_path)
    parsed = intent_module.parse_by_keywords("各省份的销售额")
    assert parsed is not None and parsed.intent == "sales_by_region"
    def _validated(params: dict) -> object:
        return intent_module.ParsedIntent(intent="sales_by_region", params=params).validated_params()

    # 倒置的区间 → 参数校验直接拒（不猜、不悄悄换顺序）
    with pytest.raises(intent_module.IntentError):
        _validated({"start": "2011-12-01", "end": "2011-11-01"})
    with pytest.raises(intent_module.IntentError):
        _validated({"top_n": 9999})                     # 越界的 top_n
    with pytest.raises(intent_module.IntentError):
        _validated({"dataset_id": "随便挑一个"})          # 不许 LLM 自己指数据源（extra=forbid）
    # 没写时间 → start/end 都是 None（不硬塞内置数据集的区间）
    assert parsed.params["start"] is None and parsed.params["end"] is None
    assert _validated({}).top_n == tools.DEFAULT_REGION_TOP_N


# ════════════════════════════════════════════════════════════════════════
# F · 选择规则：多个带地区字段的数据源 → 最近导入优先，并说明用的是哪个
# ════════════════════════════════════════════════════════════════════════
def test_F_多个地区数据源时取最近导入并说明来源(env, tmp_path):
    first = import_region_csv(tmp_path, [["省份", "销售额"], ["广东", 1], ["浙江", 2]], name="第一批.csv")
    second = import_region_csv(tmp_path, [["省份", "销售额"], ["广东", 10], ["浙江", 20]], name="第二批.csv")
    assert region_source.choose()["record"]["dataset_id"] == second["dataset_id"]

    record = ask("各省份的销售额")
    assert record["facts"]["source_dataset"]["dataset_id"] == second["dataset_id"]
    assert region_map(record["items"]) == {"广东": 10.0, "浙江": 20.0}
    assert region_map(record["items"]) != region_map(
        region_query.sales_by_region(first["dataset_id"])["rows"]
    )
    # ★ 回答里说明了"有多个、为什么用它"
    assert "最近导入" in record["notice"]
    assert "第二批" in record["notice"]


def test_F2_没有地区字段的数据源不参与选择(env, tmp_path):
    path = helpers.make_csv(tmp_path / "客户表.csv", "客户,销售额\n甲,100\n乙,200\n")
    client.post("/api/imports", files=upload([("客户表.csv", path.read_bytes())]))
    assert region_source.choose() is None
    receipt = import_region_csv(tmp_path)
    assert region_source.choose()["record"]["dataset_id"] == receipt["dataset_id"]


# ════════════════════════════════════════════════════════════════════════
# G · 多个地区维度：不隐式猜，先请用户说清
# ════════════════════════════════════════════════════════════════════════
def test_G_多个地区字段时先澄清而不是替他挑(env, tmp_path):
    rows = [["大区", "省份", "销售额"], ["华南", "广东", 100], ["华东", "浙江", 200]]
    import_region_csv(tmp_path, rows, name="多维度.csv")
    record = ask("各地区销售额")
    assert record["status"] == "unsupported"
    assert record["facts"] is None                               # 一个数字都没算
    notice = record["notice"] + record["answer"]["sections"][0]["text"]
    assert "大区" in notice and "省份" in notice                 # 候选列出来了
    assert "请说明" in notice                                    # 是在问，不是默认挑一个
    # 指明了按哪个字段看 → 就出结果，且分组字段就是那个
    # （用户/模型说清 dimension 时才走这条；这里直接问底层工具，验"指了就认"）
    direct = tools.sales_by_region(dimension="省份")
    assert direct["status"] == "ok"
    assert region_map(direct["items"]) == {"广东": 100.0, "浙江": 200.0}
    assert direct["facts"]["dimension"]["key"] == "province"


# ════════════════════════════════════════════════════════════════════════
# H · 隔离：真实 state/ 与真实 data/app.db 一个字节都不许动
# ════════════════════════════════════════════════════════════════════════
def test_H_全程走隔离路径(env, tmp_path):
    """隔离是硬要求：库、会话记录、上传原文件都必须落在本用例的临时目录里。

    证据分两步：① 六个环境变量确实指向 tmp_path；② 真跑一次提问 + 一次导入后，
    库文件/会话文件出现在那个临时目录里（而不是项目根的 data/app.db 与 state/）。
    """
    import pathlib

    root = pathlib.Path(tmp_path)
    for key in helpers.ISOLATED_ENV:
        assert str(root) in env[key], (key, env[key])

    import_region_csv(tmp_path)
    record = service.ask("各省份的销售额", use_llm=False)
    assert record["conversation_id"].startswith("c_")
    assert pathlib.Path(env["SRA_DB_PATH"]).is_file()
    assert list(pathlib.Path(env["SRA_STATE_DIR"]).glob("*.json"))
    # 真实目录没被碰（项目根的 data/app.db 与 state/ 不在本次写入范围内）
    assert pathlib.Path(env["SRA_DB_PATH"]) != pathlib.Path("data/app.db").resolve()
