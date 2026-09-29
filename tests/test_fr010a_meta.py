"""test_fr010a_meta.py · FR-010-A 验收：系统元信息问答 / 能力边界 / 不绕弯 / 不泄内部名词。

════════════════════════════════════════════════════════════════════════
【这一组钉的是派单 §二 的 A3 / A4 / A5 / A6】
════════════════════════════════════════════════════════════════════════
    A3 系统 / 文件操作问题 → **直接查、直接答**
       「我刚刚导入的文件在哪里」要给真实文件名 / 类型 / 字数 / 入库时间 / 去哪个页面看，
       数据必须来自 FR-009-A 的导入记录与统一文档层（**不另写一套取数逻辑**）。
       ★ 反向：**真的空库**时必须明说"还没有导入过任何资料" + 怎么导入，**不许编文件名**；
         读不出来时必须说"这次没读到"，**不许说成"你没有导入过"**（那是句假话）。
    A4 不具备的能力 → 直接说没有（天气 / 预测：不编、不假装、不调不存在的能力）。
    A5 不绕弯子：任何回答都不许用「为了更好地帮助您…」这类开场白铺垫。
    A6 回答正文里**零**内部名词（BM25 / intent / document_qa / vector / tool 名 /
       接口路径 / 耗时 / response_mode / 置信度 / 分段 key）。

为什么这一组**全部不需要主数据集**：这三类问题本来就**不碰销售链路**
（`system_info` 连 `app.ai.tools` 都不 import，见最后一节的结构性隔离断言）。

不用 mock：导入记录是真的导进去的（真 PDF、真落库、真字数），
只不过程序层面的隔离目录指到 tmp_path（会话级 autouse 夹具已在 conftest 里）。
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fr003_helpers as helpers  # noqa: E402
from app.ai import general, routing, service, system_info  # noqa: E402
from app.importer import pipeline  # noqa: E402

AI_DIR = PROJECT_ROOT / "app" / "ai"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离目录 + 建表（换成 tmp_path 后必须 reset 一次，见 fr003_helpers）。"""
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    return paths


def answer_text(record: dict) -> str:
    """用户**看得见**的那部分（回答段正文 + 标题 + 提示语）—— 不泄内部名词就查这一块。"""
    answer = record.get("answer") or {}
    parts = [str(record.get("notice") or "")]
    for section in answer.get("sections") or []:
        parts.append(str(section.get("title") or ""))
        parts.append(str(section.get("text") or ""))
    return "\n".join(parts)


# ════════════════════════════════════════════════════════════════════════
# ① 判据（A3 元信息 / A3 身份 / A4 能力）—— 判得出来才可能答得上来
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "question",
    (
        "我刚刚导入的文件在哪里",
        "我最新导入了什么",
        "我导入了几份文件",
        "我上传了几次",
        "最近的导入记录在哪看",
        "上次导入的是什么",
    ),
)
def test_A3_元信息问题判得出来(question: str) -> None:
    assert system_info.is_meta_question(question) is True, question
    assert routing.classify(question).intent == routing.INTENT_SYSTEM_HELP


@pytest.mark.parametrize(
    "question",
    (
        "怎么导入数据",
        "导入资料怎么用",
        "上传支持哪些格式",
        "导出怎么操作",
        "我放的周报在哪里",
    ),
)
def test_A3_纯使用说明不被元信息抢走(question: str) -> None:
    """★ 「怎么导入」是**使用说明**（静态文案），不是"查我的导入记录" —— 判据不许放宽。

    「上传支持哪些格式」是这条判据最容易错的一个：它有"上传"、也有"哪些"，
    但它问的是**能力**，答成"你还没有导入过任何资料"就是答非所问
    （所以「什么/哪些」这类指代词必须配第一人称才算元信息）。
    """
    assert system_info.is_meta_question(question) is False, question
    # 而且路由上必须落到"使用说明"那一支，不是元信息那一支
    title, _text, kind = general.system_answer(question)
    assert kind == "help", f"{question} 被判成了 {kind}"


@pytest.mark.parametrize(
    "question,expected",
    (
        ("你是什么", True),
        ("你是谁", True),
        ("介绍一下你自己", True),
        # 明说要展开 → 走使用说明（"不问不倒长清单"）
        ("详细说说你能做什么", False),
        ("你支持哪些功能", False),
    ),
)
def test_A3_身份问题的判定(question: str, expected: bool) -> None:
    assert system_info.is_identity_question(question) is expected, question


@pytest.mark.parametrize(
    "question",
    ("你能查天气吗", "可以看天气预报吗", "支持查气温吗", "帮我预测下个月能卖多少"),
)
def test_A4_问能力的问题必须排在销售之前(question: str) -> None:
    """★ 顺序反了的后果不是"答得不好"：`预测下个月能卖多少` 会被当成 `卖了多少`。"""
    assert system_info.looks_like_capability_question(question) is True, question
    assert routing.classify(question).intent == routing.INTENT_SYSTEM_HELP, question


def test_A4_裸问天气不走能力分支() -> None:
    """★ 「今天天气怎么样」没在问能力 —— 它走**澄清**那条既有通道（FR-007 冻结的形状）。"""
    assert system_info.looks_like_capability_question("今天天气怎么样") is False


# ════════════════════════════════════════════════════════════════════════
# ② A3 · 真的空库：明说没有 + 怎么导入，**不许编文件名**
# ════════════════════════════════════════════════════════════════════════
def test_A3_空库时说没有而不是编一个文件名(env) -> None:
    record = service.ask("我刚刚导入的文件在哪里", use_llm=False)
    text = answer_text(record)

    assert record["status"] == "ok"
    assert record["sales_data_accessed"] is False, "这条一次都没读销售数据"
    assert "还没有导入过任何资料" in text
    # 「怎么导入」要给出可导入的格式（真话：从 models.SOURCE_TYPES 推导）
    assert "数据管理" in text
    assert any(word in text for word in ("Excel", "PDF", "docx", "Word")), text
    # ★ 不许编文件名：任何 《…》 或 *.pdf/*.xlsx 形态的"具体文件"都是编的
    assert not re.search(r"《[^》]+》", text), f"空库却报了一个文件名：{text}"
    assert not re.search(r"\S+\.(pdf|xlsx|csv|docx|pptx|md)", text), text


def test_A3_空库时回答里没有数字式的导入条数(env) -> None:
    """空库不能说"你导入了 0 份"这种像统计结果的话 —— 直接说"还没有导入过"。"""
    record = service.ask("我导入了几份文件", use_llm=False)
    text = answer_text(record)
    assert "还没有导入过任何资料" in text
    assert not re.search(r"共\s*0\s*(条|份|次)", text), text


# ════════════════════════════════════════════════════════════════════════
# ③ A3 · 有真实记录：文件名 / 类型 / 字数 / 时间 / 去哪儿看，逐项照抄
# ════════════════════════════════════════════════════════════════════════
def test_A3_查出真实导入记录并逐项说清(env, tmp_path) -> None:
    """Given 真导入一份 PDF → Then 回答里给出**这一份**的名字/类型/字数/时间/页面。"""
    pdf = helpers.make_pdf(tmp_path / "季度复盘.pdf", pages=["Quarterly review body."])
    receipt = pipeline.import_file(pdf, filename="季度复盘.pdf")
    assert receipt["status"] == "success"

    record = service.ask("我刚刚导入的文件在哪里", use_llm=False)
    text = answer_text(record)

    assert record["status"] == "ok"
    assert record["sales_data_accessed"] is False
    assert "季度复盘.pdf" in text, text
    assert "PDF" in text, "格式名要给人话（PDF 文档）"
    assert "已成功保存" in text
    # 字数与入库时间都要有 —— 而且字数必须与库里那一份对得上（不是我们现编的）
    assert re.search(r"[\d,]+ 字", text), text
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text), text
    # 去哪儿看：页面名要与真实页面对得上
    assert "数据管理" in text and "导入记录" in text and "文档资料" in text

    # 字数逐位对齐：从统一文档层的真实记录里取，不信任文案
    from app.repositories import unified_documents

    page, _total, _notes = unified_documents.list_documents(limit=5, offset=0)
    chars = page[0]["char_count"]
    assert f"{chars:,} 字" in text, f"回答里的字数与库里不一致：{text}"


def test_A3_问几份时才报总数(env, tmp_path) -> None:
    """A5「问题有多大，回答就做到多大」：没问"几份"就不报总数。

    两份文件的**内容必须不同**（同内容会被幂等去重成一份 —— 那测的就不是总数了）。
    """
    pipeline.import_file(
        helpers.make_pdf(tmp_path / "一.pdf", pages=["First document body."]), filename="一.pdf"
    )
    pipeline.import_file(
        helpers.make_pdf(tmp_path / "二.pdf", pages=["Second document body."]), filename="二.pdf"
    )

    ask_where = answer_text(service.ask("我刚刚导入的文件在哪里", use_llm=False))
    assert "一共" not in ask_where, f"没问总数却报了总数：{ask_where}"

    ask_count = answer_text(service.ask("我导入了几份文件", use_llm=False))
    assert "一共 2 条" in ask_count, ask_count
    assert "库里的文档资料共 2 份" in ask_count, ask_count


# ════════════════════════════════════════════════════════════════════════
# ④ A3 · 读不出来 ≠ 没有导入过（这两句话的差别是"诚实"本身）
# ════════════════════════════════════════════════════════════════════════
def test_A3_读不出来时必须说没读到不能说没有导入过(env, monkeypatch) -> None:
    from app.importer import db as importer_db

    def boom():
        raise OSError("本机存储暂时不可用（模拟）")

    monkeypatch.setattr(importer_db, "readonly", boom)
    record = service.ask("我刚刚导入的文件在哪里", use_llm=False)
    text = answer_text(record)

    # ★ 不许说成"空库"那句（"还没有导入过任何资料"）—— 那是另一件事
    assert "还没有导入过任何资料" not in text, f"把读失败说成了「你没导入过」：{text}"
    assert "读不出来" in text, text
    # 而"这不是没有导入过"这句对比必须在（它正是诚实的落点）
    assert "这不是「没有导入过」" in text, text


def test_A3_记录读不出来时也绝不编文件名(env, monkeypatch) -> None:
    from app.importer import db as importer_db

    monkeypatch.setattr(importer_db, "readonly", lambda: (_ for _ in ()).throw(OSError("x")))
    text = answer_text(service.ask("我最新导入了什么", use_llm=False))
    assert not re.search(r"《[^》]+》", text), text


# ════════════════════════════════════════════════════════════════════════
# ⑤ A4 · 不具备的能力：直接说没有
# ════════════════════════════════════════════════════════════════════════
def test_A4_天气能力明说没有(env) -> None:
    record = service.ask("你能查天气吗", use_llm=False)
    text = answer_text(record)

    assert record["status"] == "unsupported", "没有这项能力 —— 状态要如实，不是 ok 也不是 error"
    assert record["sales_data_accessed"] is False
    assert "没有天气数据" in text
    # 说清"我能做什么"，而不是只把用户推开
    assert "销售" in text and "报表" in text


def test_A4_预测能力明说给不了(env) -> None:
    record = service.ask("帮我预测下个月能卖多少", use_llm=False)
    text = answer_text(record)

    assert record["status"] == "unsupported"
    assert record["sales_data_accessed"] is False, "预测问题不许先算一遍历史数字再拒绝"
    assert "不做预测" in text or "给不了" in text, text
    # ★ 不许拿历史数字冒充预测：正文里一个销售额都不该有
    assert not re.search(r"[\d,]+\.\d{2}\s*元", text), f"用历史数字冒充了预测：{text}"


def test_A4_裸问天气给的是那句实话而不是通用没听懂(env) -> None:
    """走澄清通道（形状冻结），但用户看到的那句话要**更准确**：不是没听懂，是没有那份数据。"""
    record = service.ask("今天天气怎么样", use_llm=False)
    assert record["error"]["code"] == "intent_unparseable", "记录形状不变（FR-007 冻结）"
    assert record["sales_data_accessed"] is False
    assert "没有天气数据" in str(record["notice"]), record["notice"]


def test_A4_不假装_不调不存在的能力(env) -> None:
    """拒绝时不许"顺手查一下"：连数据画像都不该读（读了就会在记录里留下痕迹）。"""
    record = service.ask("你能查天气吗", use_llm=False)
    assert record.get("data_profile") is None
    assert record.get("tool") is None
    assert record.get("facts") is None


# ════════════════════════════════════════════════════════════════════════
# ⑥ A5 不绕弯子 + A6 不泄内部名词（对**每一条**非销售回答统一扫一遍）
# ════════════════════════════════════════════════════════════════════════
#: A6 点名的内部名词（tool 名从白名单里取，见下面 `_TOOL_NAMES`）
_BANNED_WORDS: tuple[str, ...] = (
    "BM25", "bm25", "intent", "Intent", "INTENT", "document_qa", "vector", "embedding",
    "response_mode", "置信度", "耗时", "接口路径", "分段 key", "KnowledgeBase",
)
#: A5 点名的绕弯子开场白
_FLUFF_OPENERS: tuple[str, ...] = (
    "为了更好地帮助您", "为了更好地帮助", "首先，", "您好，", "很高兴为您服务", "感谢您的提问",
)
_TOOL_NAMES: tuple[str, ...] = (
    "sales_summary", "sales_trend", "top_products", "sales_compare",
    "sales_breakdown_by_country",
)
#: /api/ 路径（内部名词里点名了"接口路径"）
_API_PATH = re.compile(r"/api/[A-Za-z0-9_{}/-]+")

_NON_SALES_QUESTIONS: tuple[str, ...] = (
    "你是什么",
    "你是谁",
    "详细介绍下你自己",
    "我刚刚导入的文件在哪里",
    "我导入了几份文件",
    "我最新导入了什么",
    "怎么导出数据",
    "怎么导入数据",
    "导出报表在哪里",
    "你能查天气吗",
    "帮我预测下个月能卖多少",
    "什么是毛利率",
    "你好",
)


@pytest.mark.parametrize("question", _NON_SALES_QUESTIONS)
def test_A5_A6_非销售回答里不绕弯也不出内部名词(env, question: str) -> None:
    record = service.ask(question, use_llm=False)
    text = answer_text(record)

    for bad in _FLUFF_OPENERS:
        assert bad not in text, f"「{question}」里出现了绕弯子开场白：{bad}"
    for bad in _BANNED_WORDS:
        assert bad not in text, f"「{question}」里出现了内部名词：{bad}"
    for name in _TOOL_NAMES:
        assert name not in text, f"「{question}」里出现了工具名：{name}"
    assert not _API_PATH.search(text), f"「{question}」里出现了接口路径：{_API_PATH.findall(text)}"


@pytest.mark.parametrize("question", _NON_SALES_QUESTIONS)
def test_A5_A6_非销售回答一律不读销售数据(env, question: str) -> None:
    record = service.ask(question, use_llm=False)
    assert record["sales_data_accessed"] is False, question
    assert record.get("data_profile") is None, question


def test_A5_澄清话术也不出内部名词() -> None:
    """「没听懂」那句字面量曾经写着"不知道该调哪个工具" —— 那是实现细节，不该给用户看。"""
    from app.ai import intent as intent_module

    message = intent_module.unparseable_message()
    for bad in _BANNED_WORDS:
        assert bad not in message, bad
    assert "工具" not in message, message
    assert "没听懂" in message
    assert "为了" not in message


def test_A5_身份回答就是一句话(env) -> None:
    """A5：一句话能答完的就一句话答完 —— 不许顺手列十几项能力。"""
    record = service.ask("你是什么", use_llm=False)
    sections = record["answer"]["sections"]
    assert len(sections) == 1
    text = sections[0]["text"]
    assert text == system_info.IDENTITY_LINE
    assert "\n" not in text, "身份回答不该是多段"
    assert len(text) <= 130, f"身份回答太长了（{len(text)} 字）：{text}"


# ════════════════════════════════════════════════════════════════════════
# ⑦ 结构性隔离：这条链路**不可能**读到销售数据（不靠提示词自律）
# ════════════════════════════════════════════════════════════════════════
_SALES_CHAIN_MODULES = (
    "tools", "app.ai.tools", "app.engine", "app.engine.executor", "app.engine.loader",
    "app.engine.metrics", "region_query",
)


def _imported_names(path: pathlib.Path) -> set[str]:
    """文件里 `import x` / `from x import y` 出现的全部模块名（AST 解析，不看注释）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("filename", ("system_info.py", "dashboard.py"))
def test_A3_A4_新模块不import销售链路(filename: str) -> None:
    """★ 能力边界要**结构性**成立：想在这里读销售数据，得先加一行 import —— 那行会被这条拦下。

    （dashboard.py 是例外里的例外：它是**看板**，要调 tools 拿确定性结果 ——
      所以它允许 import tools；但它**不许**碰 engine/executor，也不许自己 groupby。）
    """
    names = _imported_names(AI_DIR / filename)
    forbidden = _SALES_CHAIN_MODULES if filename == "system_info.py" else (
        "app.engine", "app.engine.executor", "app.engine.loader", "app.engine.metrics",
    )
    for module in forbidden:
        assert module not in names, f"{filename} 不该 import {module}（导入集合：{sorted(names)}）"


def test_A3_general不import销售链路() -> None:
    names = _imported_names(AI_DIR / "general.py")
    for module in (*_SALES_CHAIN_MODULES, "app.importer.pipeline"):
        assert module not in names, module


def test_A3_元信息回答不经过任何销售工具(env, monkeypatch) -> None:
    """把销售工具**换成炸弹**：只要这条链路碰一下，测试就当场炸出来。"""
    from app.ai import tools

    def bomb(*args, **kwargs):
        raise AssertionError("元信息回答不该调用任何销售工具")

    for name in ("dataset_profile", "sales_summary", "sales_trend", "top_products", "sales_compare"):
        monkeypatch.setattr(tools, name, bomb, raising=False)

    for question in ("你是什么", "我刚刚导入的文件在哪里", "你能查天气吗"):
        record = service.ask(question, use_llm=False)
        assert record["sales_data_accessed"] is False, question


def test_A3_判据只有一份实现() -> None:
    """★ 判路与回答必须同源：routing 用 `system_info.is_meta_question`，
    不许在 routing 里再抄一份词表（两处各抄一份，早晚漂移成"判得出来却答不上来"）。"""
    routing_source = (AI_DIR / "routing.py").read_text(encoding="utf-8")
    assert "system_info.is_meta_question" in routing_source
    general_source = (AI_DIR / "general.py").read_text(encoding="utf-8")
    assert "system_info.system_self_answer" in general_source


__all__ = ["answer_text"]
