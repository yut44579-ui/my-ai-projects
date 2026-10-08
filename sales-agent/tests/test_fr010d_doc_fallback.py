"""test_fr010d_doc_fallback.py · FR-010-D 验收：资料问答的判据漏网 + 澄清档不许空回答。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么（派单 §验收 1–6）】
════════════════════════════════════════════════════════════════════════
   修一 · 判据漏网
    D-1 点名了资料 → 兜底检索           「供应商与物流.md 里说成本会怎样？」不再回"没听懂"：
                                        命中就**带出处回答**（与 B 同一种格式），
                                        没命中就说"没有找到"+列库
    D-2 库为空时不兜底                  一份资料都没有 → 保持既有澄清（**不许**凭空兜底）
    D-3 不许放宽 `_CONTENT_ASKS`        「怎么导入资料？」仍是使用说明（兜底**不许**抢它）
    D-4 顺序不变                        销售各档 / 使用说明 / 元信息 / 算术 / 概念**一位不动**
    D-5 兜底不碰销售数据                走兜底这条路时，销售工具的入口一次都不能被调用

   修二 · 澄清档有正文
    D-6 `answer.text` 非空              落进 clarify 的问题，回答区有正文（不再整块空掉），
                                        且正文与 notice **同一句话**、状态仍是 error

**【全部是真文件、真导入、真检索】**：样例资料是现写的真 Markdown，经 `pipeline.import_bytes`
（与 HTTP 上传同一条管道）真入库；检索读的是库里的正文，没有一处把 `doc_index.search` /
`doc_qa.answer_question` 换成假的。

★ 关于 `is_document_fallback("怎么导入资料？") is True`（D-3 里那条"看着矛盾"的断言）：
  兜底判据是 **①∧¬②**（点了名资料 ∧ 没用"问内容"那套说法），「怎么导入资料？」**确实满足它** ——
  但它早在①那一档就被"使用说明"判走了，**轮不到兜底**（`route.fallback is False`）。
  所以这一组同时钉住两件事：判据本身够宽（漏网的那类才进得来），
  而**路由的先后**保证使用说明不会被抢。只钉前一半会漏掉真正的风险（施工中实测到过：
  回答侧若自己重判一遍，就会把"怎么导入资料"答成"资料里没有找到关于「怎么导入」的内容"）。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fr003_helpers as helpers  # noqa: E402
from app.ai import doc_index, doc_qa, routing, service  # noqa: E402
from app.importer import pipeline  # noqa: E402

AI_DIR = PROJECT_ROOT / "app" / "ai"

#: 与 Hermes 复验时那份素材同构的资料（有"成本预期"一节、写着"预计明年…下降 12%"）
SUPPLIER_DOC = """# 供应商与物流

## 一、供应商结构

目前主要供应商集中在华东，两家占了大头，交付周期在两周左右。

## 二、物流方式

干线以整车为主，零担为辅，旺季会临时加价。

## 三、成本预期

预计明年单位物流成本下降 12%，前提是干线拼车比例提到六成以上。

## 四、风险

油价与运力是主要不确定因素。
"""

#: Hermes 复验时那四条"过不去"的问法（**原样照抄**）—— 注意它们**四种命运不同**，
#: 每一条的期望都在下面的用例里写明了为什么（有两条被 FR-010-C 的联合分析判走，
#: 那一条排在兜底之前，本 TASK 不许动它）。
HERMES_QUESTIONS = (
    "资料说明年物流成本会怎样？",
    "资料里说明年物流成本会怎样？",
    "物流成本明年会怎样？",
    "供应商与物流.md 里说成本会怎样？",
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(service.llm, "available", lambda: False)
    return None


def import_supplier_doc(tmp_path: pathlib.Path) -> None:
    path = helpers.make_md(tmp_path / "供应商与物流.md", SUPPLIER_DOC)
    pipeline.import_bytes(path.read_bytes(), "供应商与物流.md")


def ask(question: str) -> dict:
    return service.ask(question, use_llm=False)


def text_of(record: dict) -> str:
    return ((record.get("answer") or {}).get("text") or "")


# ════════════════════════════════════════════════════════════════════════
# ① 判据：兜底那一类判得出来（①∧¬②），既有入口那一类**判得出来的照旧判得出来**
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "question",
    (
        "供应商与物流.md 里说成本会怎样？",
        "资料里说成本预期会怎样？",
        "《供应商与物流.md》里说单位物流成本怎么样？",
    ),
)
def test_D1_点名了资料却没用问内容那套说法_判得出来(question: str) -> None:
    assert doc_qa.points_at_document(question) is True, question
    assert doc_qa.has_content_ask(question) is False, question
    assert doc_qa.is_document_fallback(question) is True, question


@pytest.mark.parametrize(
    "question",
    (
        "资料里提到的成本预期是什么？",       # ①∧② → 走既有入口，**不归兜底**
        "资料里有没有写员工绩效考核标准？",
        "今天天气怎么样",                     # 连①（点名资料）都不成立
        "2011年11月一共卖了多少？",
        "什么是毛利率？",
    ),
)
def test_D1_既有资料问答与不相干的问法都不归兜底(question: str) -> None:
    """★ 兜底只收"点了名资料 ∧ 没用问内容那套说法"那一种；别的一律不碰。

    （「怎么导入资料？」**判据上**是归兜底的 —— 它被①那一档判走，不走兜底这条路；
      那一条由 `test_D3_使用说明不会被兜底抢答成资料内容` 单独钉。）
    """
    assert doc_qa.is_document_fallback(question) is False, question


def test_D1_判据只有一份实现_路由与回答两侧同一个函数() -> None:
    """判路与回答必须同源（与 B/C 同一条规矩）：两处各抄一份词表，早晚漂移。"""
    routing_source = (AI_DIR / "routing.py").read_text(encoding="utf-8")
    assert "doc_qa.is_document_fallback" in routing_source
    assert "doc_qa.has_documents" in routing_source
    # 回答侧**不许**自己重判一遍：它只听路由的结论（route.fallback）
    service_source = (AI_DIR / "service.py").read_text(encoding="utf-8")
    assert "doc_qa.answer_question(question, fallback=route.fallback)" in service_source


# ════════════════════════════════════════════════════════════════════════
# ② 兜底的两条触发条件：点了名资料 + 库里有资料
# ════════════════════════════════════════════════════════════════════════
def test_D1_库里有资料时兜底生效_并且回答带出处(env, tmp_path) -> None:
    """命中：兜底走的是**同一套**检索与出处（不是另一套回答）。"""
    import_supplier_doc(tmp_path)
    question = "资料里说成本预期会怎样？"

    # 判据②确实不放行它（证明这次是**兜底**答出来的，不是既有入口）
    assert doc_qa.answer_question(question) is None, question

    record = ask(question)
    assert record["response_mode"] == "help", record["response_mode"]
    assert record["routing"]["intent"] == routing.INTENT_SYSTEM_HELP
    assert record["routing"]["fallback"] is True          # 记录里说清"这次是兜底档"
    assert record["status"] == "ok", record["status"]

    text = text_of(record)
    assert "《供应商与物流.md》第 3 节 · 三、成本预期" in text, text     # 出处（与 B 同一种写法）
    assert "明年单位物流成本下降 12%" in text, text                       # 原句引用（一个字不改写）
    assert "资料中预计" in text, text                                     # B5：属性词照旧
    # 机器可读的出处也在，且与正文里那句同源
    sources = (record["answer"] or {}).get("sources") or []
    assert [item["filename"] for item in sources] == ["供应商与物流.md"], sources
    assert sources[0]["position_title"] == "三、成本预期", sources[0]
    # 兜底这条路同样不读销售数据
    assert record["sales_data_accessed"] is False
    assert record["facts"] is None and record["tool"] is None


def test_D1_库里有资料但检索没命中_说没找到而不是没听懂(env, tmp_path) -> None:
    import_supplier_doc(tmp_path)
    record = ask("供应商与物流.md 里说成本会怎样？")

    assert record["response_mode"] == "help"
    assert record["status"] == "unsupported"              # "我诚实地说没有"—— 与 AC-03 同一档
    text = text_of(record)
    assert text.startswith("我在当前已导入的资料里没有找到关于"), text
    assert "《供应商与物流.md》—— 共 4 节" in text, text                  # 列库（既有文案）
    assert "没听懂" not in text, "兜底以后不许再回'没听懂'"
    assert "没听懂" not in (record.get("error") or {}).get("message", "")


def test_D2_库为空时不兜底_保持既有澄清(env) -> None:
    """一份资料都没有 → 没有可检索的东西，兜底没有意义（保持既有行为）。"""
    question = "供应商与物流.md 里说成本会怎样？"
    assert doc_qa.has_documents() is False
    assert routing.classify(question).fallback is False
    assert routing.classify(question).intent == routing.INTENT_CLARIFY

    record = ask(question)
    assert record["response_mode"] == "clarify"
    assert record["status"] == "error"
    assert (record.get("error") or {}).get("code") == "intent_unparseable"
    assert "没听懂" in (record.get("error") or {}).get("message", "")


def test_D2_导入前后同一个问句的档位对照(env, tmp_path) -> None:
    """同一条问句：导入前=澄清（既有），导入后=资料检索兜底 —— 差别**只**来自"库里有资料"。"""
    question = "供应商与物流.md 里说成本会怎样？"
    before = ask(question)
    assert (before["response_mode"], before["routing"]["fallback"]) == ("clarify", False)

    import_supplier_doc(tmp_path)
    assert doc_qa.has_documents() is True
    after = ask(question)
    assert (after["response_mode"], after["routing"]["fallback"]) == ("help", True)


def test_D2_点名了库里没有的那份资料时也走兜底出口(env, tmp_path) -> None:
    """库里有别的资料、点名的那份不存在 → B 的既有出口（如实说没有 + 列出库里真有的），
    不是"没听懂"。"""
    import_supplier_doc(tmp_path)
    record = ask("《不存在的资料》里说成本会怎样？")
    assert record["response_mode"] == "help"
    text = text_of(record)
    assert "库里没有叫" in text, text
    assert "《供应商与物流.md》" in text, text          # 列库（B4：不许挑一份近似的顶上）
    assert "没听懂" not in text


# ════════════════════════════════════════════════════════════════════════
# ③ 不许被抢：使用说明 / 元信息 / 销售 / 报告 / 概念 / 算术 一位不动
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "question,expected_intent,expected_mode",
    (
        ("怎么导入资料？", routing.INTENT_SYSTEM_HELP, routing.MODE_HELP),
        ("怎么导出数据", routing.INTENT_SYSTEM_HELP, routing.MODE_HELP),
        ("我导入的文件在哪？", routing.INTENT_SYSTEM_HELP, routing.MODE_HELP),
        ("我导入了几份文件", routing.INTENT_SYSTEM_HELP, routing.MODE_HELP),
        ("你能做什么", routing.INTENT_SYSTEM_HELP, routing.MODE_HELP),
        ("你能查天气吗", routing.INTENT_SYSTEM_HELP, routing.MODE_HELP),
        ("2011年11月一共卖了多少？", routing.INTENT_DATA_LOOKUP, routing.MODE_DIRECT),
        ("2011年11月的销售趋势", routing.INTENT_SALES_ANALYSIS, routing.MODE_ANALYSIS),
        ("帮你生成上周周报", routing.INTENT_REPORT_GENERATION, routing.MODE_REPORT),
        ("什么是毛利率？", routing.INTENT_GENERAL_QA, routing.MODE_GENERAL),
        ("1+1等于多少？", routing.INTENT_ARITHMETIC, routing.MODE_DIRECT),
        ("今天天气怎么样", routing.INTENT_CLARIFY, routing.MODE_CLARIFY),
    ),
)
def test_D4_兜底排在最后_前面每一档照旧(env, tmp_path, question, expected_intent, expected_mode) -> None:
    """★ 库里有资料（兜底**具备触发条件**）时，前面那些档该抢的照旧抢。"""
    import_supplier_doc(tmp_path)
    route = routing.classify(question)
    assert route.intent == expected_intent, (question, route.intent)
    assert route.response_mode == expected_mode, (question, route.response_mode)


def test_D3_使用说明不会被兜底抢答成资料内容(env, tmp_path) -> None:
    """★ 施工中实测到过的坑：回答侧若自己重判一遍判据，「怎么导入资料」就会被答成
    "资料里没有找到关于「怎么导入」的内容" —— 这一条把它钉死。"""
    import_supplier_doc(tmp_path)
    question = "怎么导入资料？"
    assert doc_qa.is_document_fallback(question) is True     # 判据上成立（见模块开头那段说明）
    assert routing.classify(question).fallback is False      # 但轮不到兜底（①那一档判走了）

    record = ask(question)
    text = text_of(record)
    assert "在「数据管理」页点上传" in text, text              # 真·使用说明
    assert "没有找到关于" not in text, text
    assert record["routing"]["fallback"] is False


def test_D4_元信息档也不被兜底抢(env, tmp_path) -> None:
    import_supplier_doc(tmp_path)
    record = ask("我导入的文件在哪？")
    assert record["status"] == "ok"
    assert "导入记录" in text_of(record), text_of(record)


# ════════════════════════════════════════════════════════════════════════
# ④ 兜底这条路**一次都不碰销售数据**（结构性：读数据的入口全换成炸弹）
# ════════════════════════════════════════════════════════════════════════
def test_D5_兜底与澄清都不碰销售数据(env, tmp_path, monkeypatch) -> None:
    import_supplier_doc(tmp_path)

    from app.ai import tools
    from app.engine import executor, loader

    touched: list[str] = []

    def bomb(name):
        def _inner(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f"非销售分支调用了 {name} —— 销售数据被读了")
        return _inner

    monkeypatch.setattr(tools, "run_tool", bomb("tools.run_tool"))
    monkeypatch.setattr(tools, "dataset_profile", bomb("tools.dataset_profile"))
    monkeypatch.setattr(loader, "load_raw", bomb("loader.load_raw"))
    monkeypatch.setattr(executor, "compute_sales_amount", bomb("executor.compute_sales_amount"))

    for question in ("资料里说成本预期会怎样？",      # 兜底命中
                     "供应商与物流.md 里说成本会怎样？",  # 兜底未命中
                     "怎么导入资料？",                # 使用说明
                     "今天天气怎么样"):               # 澄清
        record = ask(question)
        assert record["sales_data_accessed"] is False, question
        assert record["facts"] is None and record["tool"] is None, question
        assert record["data_profile"] is None, question
    assert touched == [], touched


# ════════════════════════════════════════════════════════════════════════
# ⑤ 修二：澄清档给出正文（与 notice 同一句话），状态一字不改
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "question",
    ("今天天气怎么样", "帮我订一张去上海的机票", "今天天气怎么样？", "讲个笑话"),
)
def test_D6_澄清档有正文且与提示条同一句话(env, question: str) -> None:
    record = ask(question)
    assert record["response_mode"] == "clarify", question
    text = text_of(record)

    assert text, f"{question} 的回答正文还是空的"
    assert text == record["notice"], "正文与提示条必须是同一句话（页面上不许两种说法）"
    sections = (record["answer"] or {}).get("sections") or []
    assert [section["key"] for section in sections] == ["answer"], sections
    assert sections[0]["source"] == "code", sections[0]
    # 状态与错误码一个字没动（FR-007 的验收点）
    assert record["status"] == "error", question
    assert (record.get("error") or {}).get("code") == "intent_unparseable", question


def test_D6_澄清正文零数字零内部名词(env) -> None:
    """正文里不许出现**阿拉伯数字**（`①…⑥` 是序号字符，不算 —— 与既有验收同一条口径），
    也不许出现内部名词（`doc_qa.BANNED_WORDS` 那张表直接扫）。"""
    for question in ("今天天气怎么样", "帮我订一张去上海的机票"):
        text = text_of(ask(question))
        assert not any(char in "0123456789" for char in text), (question, text)
        for banned in doc_qa.BANNED_WORDS:
            assert banned not in text, (question, banned, text)
        assert "工具" not in text and "接口" not in text, (question, text)


def test_D6_澄清正文在上限之内不是一句绕弯子的话(env) -> None:
    """正文就是那句话本身：不出现"请换个问法/换个说法再问"这类绕弯子的引导语。"""
    text = text_of(ask("帮我订一张去上海的机票"))
    assert "没听懂这个问题" in text, text
    assert "换个问法" not in text and "请换" not in text, text


# ════════════════════════════════════════════════════════════════════════
# ⑥ Hermes 那四条问法（原样照抄）一条条说清现在的命运
# ════════════════════════════════════════════════════════════════════════
def test_D1_Hermes四条问法的现状与理由(env, tmp_path) -> None:
    """★ 如实钉住四条问法**各自**落在哪一档 —— 不许把它们笼统写成"都修好了"。

    ① 「资料说明年物流成本会怎样？」    → **联合分析**（FR-010-C 的判据："明年"在它的
       产出词表里，且该档排在兜底之前）—— 本 TASK 不许动 joint，所以这条不由资料问答回答；
    ② 「资料里说明年物流成本会怎样？」  → 同上；
    ③ 「物流成本明年会怎样？」          → 它**没点名任何资料**（points_at_document=False），
       兜底的触发条件不成立 → 仍是澄清（这一条已如实记进交付报告，需要 Hermes 裁决）；
    ④ 「供应商与物流.md 里说成本会怎样？」 → **走兜底**（本次修好的那一条）→ 检索未命中 → "没找到"。
    """
    import_supplier_doc(tmp_path)
    intents = {question: routing.classify(question).intent for question in HERMES_QUESTIONS}
    assert intents["资料说明年物流成本会怎样？"] == routing.INTENT_JOINT_ANALYSIS, intents
    assert intents["资料里说明年物流成本会怎样？"] == routing.INTENT_JOINT_ANALYSIS, intents
    assert intents["物流成本明年会怎样？"] == routing.INTENT_CLARIFY, intents
    assert intents["供应商与物流.md 里说成本会怎样？"] == routing.INTENT_SYSTEM_HELP, intents

    # ④ 是被兜底接住的那一条：走的是资料问答的出口（没命中 → 说没找到），不再是"没听懂"
    record = ask("供应商与物流.md 里说成本会怎样？")
    assert "没听懂" not in text_of(record)
    # ③ 触发条件不成立：它一个字都没提资料
    assert doc_qa.points_at_document("物流成本明年会怎样？") is False


__all__: list[str] = []
