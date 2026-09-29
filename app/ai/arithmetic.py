"""arithmetic.py · 纯算术问题：**受限 AST 计算器**（FR-007）。

════════════════════════════════════════════════════════════════════════
【为什么要有这个文件（而不是让模型"心算"）】
════════════════════════════════════════════════════════════════════════
FR-007 的真实复现：用户问「1+1等于多少？」时，系统把它当成销售汇总，
去算了一张 7 行的销售表 —— 答非所问，而且用户拿到的数字与问题毫无关系。

正确的做法有两条硬约束（施工指令 §四 + §六①）：
    ① 数学**不经过 LLM**：模型不做算术，数字由这台计算器给出；
    ② **不许 eval / exec**：`eval("__import__('os').system(...)")` 这种输入必须
       在解析阶段就被拒绝，而不是"执行之前先检查一下字符串"。

所以这里走的是一条**白名单 AST** 路径：

    识别 → 抠表达式 → 长度/位数/深度限制 → ast.parse(mode="eval")
         → 只允许 Constant(int/float) / UnaryOp(+ -) / BinOp(+ - * / % **)
         → 递归求值（自己写，不调内置 eval）

`ast.parse` 只负责把**文本变成树**，它不执行任何东西；真正危险的那一步
（把树变回动作）是我们自己递归做的，且只认白名单里的节点类型。
遇到 Name / Call / Attribute / Subscript / 推导式 一律拒绝 —— 这是"结构性拒绝"，
不是黑名单式地去找 `__import__` 这个词。

════════════════════════════════════════════════════════════════════════
【资源攻击也要拦（评审点名）】
════════════════════════════════════════════════════════════════════════
`10 ** 100000000` 不执行任何用户代码，但它会让本进程算到天荒地老 —— 这同样是攻击。
所以在**求值之前**先卡四道闸：表达式长度 / 单个数字位数 / 括号嵌套深度 / 幂指数上限，
求值之后再卡一道结果数量级。任何一道不过 → 明确拒绝（说清是哪一道），绝不"算到一半崩"。

════════════════════════════════════════════════════════════════════════
【明确不做（施工指令 §四末条）】
════════════════════════════════════════════════════════════════════════
不做通用数学引擎：不引 SymPy、不解方程、不做单位换算、不做科学计算、不开放数学函数
（sin/log/sqrt 一律不在白名单里）。这里只回答"两个数加减乘除"这一档问题。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

# ── 安全闸门（数值全部是**保守**值：宁可拒一个正常表达式，也不放一个资源炸弹进来）──
MAX_EXPRESSION_LENGTH = 120        # 表达式字符数上限
MAX_LITERAL_DIGITS = 15            # 单个数字字面量的位数上限
MAX_TOTAL_DIGITS = 40              # 整条表达式里数字位数之和的上限
MAX_DEPTH = 8                      # 括号/运算嵌套深度上限
MAX_POW_EXPONENT = 1000            # 幂指数绝对值上限（10**100000000 在这一道被拒）
MAX_ABS_RESULT = 1e15              # 结果绝对值上限（超过就不是"算个数"了）


class Rejected(Exception):
    """表达式被安全闸门拒绝（带**给用户看**的人话原因）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# ① 识别：这句话是不是一道算术题
# ════════════════════════════════════════════════════════════════════════
# 全角/花样写法统一成 ASCII 运算符（用户从 Word/微信里粘过来常常是全角的）
_OPERATOR_FOLD = {
    "×": "*", "✕": "*", "✖": "*", "·": "*", "＊": "*",
    "÷": "/", "／": "/", "∕": "/",
    "−": "-", "－": "-", "—": "-", "–": "-",
    "＋": "+", "＝": "=", "(": "(", ")": ")",
    "（": "(", "）": ")",
}
# 中文数词运算符（"1加1"这种写法也算算术，但**只在明确是算术题时**才认）
_WORD_OPERATORS = (("加上", "+"), ("减去", "-"), ("乘以", "*"), ("除以", "/"), ("加", "+"), ("减", "-"), ("乘", "*"), ("除", "/"))

# 表达式允许出现的字符（数字 / 运算符 / 括号 / 小数点 / 空白）
_EXPR_CHARS = "0123456789.()+-*/%^ \t"
_EXPR_CANDIDATE_RE = re.compile(rf"[{re.escape(_EXPR_CHARS)}]{{3,}}")

# 日期长相（2011-11-01 / 2011/11）：它们**长得像减法**，必须挡在识别之前，
# 否则「2011-11-01 到 2011-11-07 的销售趋势」会被当算术题。
_DATE_LIKE_RE = re.compile(r"\d{4}\s*[-/.]\s*\d{1,2}(\s*[-/.]\s*\d{1,2})?")
_DATE_WORD_RE = re.compile(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*[日号]")

# 一问就说明"这是算术题"的收尾语（"1+1等于多少"里的"等于多少"）
_ARITH_TAIL_WORDS = (
    "等于多少", "等于几", "等于啥", "是多少", "是几", "等于", "得多少", "得几",
    "=?", "＝?", "=", "计算结果", "算出来",
)
# 开头语（"帮我算一下 3*7"）
_ARITH_HEAD_WORDS = ("请问", "帮我算", "帮我计算", "算一下", "算算", "计算一下", "计算", "口算", "求", "请问一下", "麻烦算")
# 出现这些词就**不是**算术题（业务实体优先；宁可让销售链路去处理，也不抢过来算错）
_BUSINESS_WORDS = (
    "销售", "销售额", "营业额", "营收", "金额", "订单", "客户", "顾客", "买家", "产品", "商品",
    "货号", "编码", "库存", "退货", "退款", "取消", "复购", "数据", "报表", "周报", "月报",
    "报告", "趋势", "占比", "国家", "地区", "价格", "单价", "毛利", "利润", "成本",
    "元", "美元", "单", "位",
    # 系统类（那些归 SYSTEM_HELP，不该被算术抢走）
    "导出", "下载", "导入", "上传", "登录", "密码", "帮助",
)
# 概念类问题（"什么是 __init__ 方法"里的 `__` 不该被当成注入）—— 优先让普通问答去答
_CONCEPT_MARKERS = ("什么是", "是什么意思", "指的是", "怎么理解", "解释一下", "的定义", "是什么")
# 代码/注入长相（大小写不敏感；**结构上拒绝**，不是去找某个词再删掉）
_CODE_MARKERS = (
    "__", "import", "eval(", "exec(", "system(", "open(", "subprocess", "os.", "lambda",
    "`", "$(", ";", "&&", "||", ">", "<",
)


def _fold(text: str) -> str:
    """把全角/花样写法折成 ASCII（只动运算符与括号，不动汉字）。"""
    out = []
    for char in text:
        if char.isdigit() and ord(char) > 0x2000:            # 全角数字 ０-９
            out.append(chr(ord(char) - 0xFEE0))
        else:
            out.append(_OPERATOR_FOLD.get(char, char))
    return "".join(out)


def _strip_question_parts(text: str) -> str:
    """去掉问句的头尾语气词，留下表达式那一段（去不掉就原样返回）。"""
    body = text.strip().strip("？?。.!！,， ")
    for word in _ARITH_HEAD_WORDS:
        if body.startswith(word):
            body = body[len(word):]
            break
    for word in _ARITH_TAIL_WORDS:
        if body.endswith(word):
            body = body[: -len(word)]
            break
    return body.strip().strip("？?。.!！,， ")


def _has_binary_operator(expr: str) -> bool:
    """必须**两项之间**有运算符 —— 光有 `-3`（负号）不算算术题。

    左边允许是数字或右括号（`(-3)+5` 里的 `+` 左边就是 `)`）。
    """
    return bool(re.search(r"[)\d]\s*(\*\*|[-+*/%^])\s*[\d(]", expr))


def extract_expression(question: str) -> str | None:
    """从问句里抠出算术表达式；抠不出来返回 None（= 这不是一道算术题）。

    识别规则（严进）：**必须**是一道明确的算术题 ——
      · 整句就是表达式（"1+1"、"12345×6789"），或
      · 有明确的算术收尾语（"等于多少 / 是多少 / 计算一下 / 算一下"）
    并且**不含**任何业务/系统词（销售额、订单、导出…），也不是日期长相。
    """
    text = _fold((question or "").strip())
    if not text or len(text) > MAX_EXPRESSION_LENGTH * 2:
        return None
    if any(word in text for word in _BUSINESS_WORDS):
        return None
    # 中文数词写法先折成符号（"3加7等于多少"→"3+7等于多少"）
    for word, symbol in _WORD_OPERATORS:
        text = text.replace(word, symbol)

    body = _strip_question_parts(text)
    marked = body != text.strip().strip("？?。.!！,， ")     # 真的削掉了头/尾的语气词

    candidates: list[str] = []
    if body and all(char in _EXPR_CHARS for char in body):
        candidates.append(body)
    # 句子里夹着表达式的（"帮我算一下 3*7 是多少"）：取最长的一段候选
    candidates.extend(match.group(0) for match in _EXPR_CANDIDATE_RE.finditer(text))

    best: str | None = None
    for candidate in candidates:
        expr = _clean(candidate)
        if expr is None or not _has_binary_operator(expr):
            continue
        # 只有"整句就是表达式"或"明确标了是算术题"才认；否则交给业务链路
        whole = all(char in _EXPR_CHARS for char in body)
        if not (whole or marked):
            continue
        if best is None or len(expr) > len(best):
            best = expr
    return best


def _clean(candidate: str) -> str | None:
    """修剪候选串：去掉首尾多余的运算符/空白，挡掉日期长相与超长表达式。

    首位的 `-` / `+` **保留**（"-3+5" 里的负号是表达式的一部分）；
    只削掉打头的 `*` `/` `%` `^`（它们不可能合法地出现在开头）与结尾多余的运算符。
    """
    expr = candidate.strip()
    if _DATE_LIKE_RE.search(expr) or _DATE_WORD_RE.search(expr):
        return None
    expr = re.sub(r"^[*/%^]+", "", expr)
    expr = re.sub(r"[+\-*/%^]+$", "", expr).strip()
    if not expr or len(expr) > MAX_EXPRESSION_LENGTH:
        return None
    if not re.search(r"\d", expr):
        return None
    if not all(char in _EXPR_CHARS for char in expr):
        return None
    return re.sub(r"\s+", " ", expr).strip()


# ════════════════════════════════════════════════════════════════════════
# ② 求值：白名单 AST，自己递归，不用 eval
# ════════════════════════════════════════════════════════════════════════
_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}
_UNARY_OPS = {ast.UAdd: lambda v: v, ast.USub: lambda v: -v}
_POW = "**"


def _literal_digits(node: ast.Constant) -> int:
    value = node.value
    if isinstance(value, int):
        return len(str(abs(value)))
    return len(re.sub(r"\D", "", repr(float(value))))


def _eval_node(node: ast.AST, depth: int) -> float:
    if depth > MAX_DEPTH:
        raise Rejected(f"算式嵌套太深（超过 {MAX_DEPTH} 层），我不算。")

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, depth)

    if isinstance(node, ast.Constant):
        value = node.value
        # bool 是 int 的子类，"True+1" 不算算术题 —— 结构上就不在白名单里
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Rejected("算式里出现了不是数字的东西，我只算数字。")
        if _literal_digits(node) > MAX_LITERAL_DIGITS:
            raise Rejected(f"算式里的数字太长（超过 {MAX_LITERAL_DIGITS} 位），我不算。")
        return float(value)

    if isinstance(node, ast.UnaryOp):
        handler = _UNARY_OPS.get(type(node.op))
        if handler is None:
            raise Rejected("算式里用了不支持的符号。")
        return handler(_eval_node(node.operand, depth + 1))

    if isinstance(node, ast.BinOp):
        handler = _BIN_OPS.get(type(node.op))
        if handler is None:
            raise Rejected("算式里用了不支持的运算（只支持 + - * / % **）。")
        if isinstance(node.op, ast.Pow):
            # 幂指数**先查再算**：10**100000000 不能先算出来再判断大小
            exponent_node = node.right
            sign = 1.0
            if isinstance(exponent_node, ast.UnaryOp) and isinstance(exponent_node.op, ast.USub):
                exponent_node, sign = exponent_node.operand, -1.0
            if not isinstance(exponent_node, ast.Constant) or isinstance(exponent_node.value, bool):
                raise Rejected("幂运算的指数只能是普通数字，我不算这种写法。")
            if abs(float(exponent_node.value)) > MAX_POW_EXPONENT:
                raise Rejected(f"幂指数太大（超过 {MAX_POW_EXPONENT}），我不算。")
            left, right = _eval_node(node.left, depth + 1), sign * float(exponent_node.value)
        else:
            left, right = _eval_node(node.left, depth + 1), _eval_node(node.right, depth + 1)
        if isinstance(node.op, (ast.Div, ast.Mod)) and right == 0:
            raise Rejected("除数是 0，没法算（数学上这是没有定义的）。")
        value = float(handler(left, right))
        if value != value or value in (float("inf"), float("-inf")):
            raise Rejected("算出来的结果不是一个正常数字，我不算。")
        if abs(value) > MAX_ABS_RESULT:
            raise Rejected(f"结果超出我能算的范围（超过 {MAX_ABS_RESULT:,.0f}），我不算。")
        return value

    # 其余一律拒绝：Name / Call / Attribute / Subscript / 推导式 / Lambda / 比较 …
    # `__import__("os")` 会走到这里 —— 被拒的原因是"节点类型不在白名单"，
    # 而不是"字符串里出现了 __import__ 这个词"。
    raise Rejected("算式里出现了不支持的写法（我只认数字和 + - * / % **）。")


# ════════════════════════════════════════════════════════════════════════
# ③ 对外：识别 + 求值 + 渲染
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ArithResult:
    """一次算术回答的全部内容（expression 原样留痕，供审计）。"""

    expression: str
    value: float | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.value is not None and self.error is None

    def render(self) -> str:
        """给用户看的那一行（成功是 "1 + 1 = 2"，失败是明确的一句人话）。"""
        if not self.ok:
            return f"{self.expression} —— {self.error}"
        return f"{_display(self.expression)} = {format_number(self.value)}"


def _overlong_expression(question: str) -> str | None:
    """"长得就是一条算式、只是太长"—— 这一类要**明确拒绝**，不是含糊地"没看懂"。

    （长度限制写在 `_clean` 里，那条路会把它整条丢掉 → solve 返回 None → 落到"没听懂"。
      资源攻击里最没有技术含量的那一种就是靠超长表达式耗时间，所以它得有自己的说法。）
    """
    text = _fold((question or "").strip())
    if any(word in text for word in _BUSINESS_WORDS):
        return None
    body = _strip_question_parts(text)
    if len(body) <= MAX_EXPRESSION_LENGTH:
        return None
    if not all(char in _EXPR_CHARS for char in body):
        return None
    if not _has_binary_operator(body):
        return None
    if _DATE_LIKE_RE.search(body) or _DATE_WORD_RE.search(body):
        return None
    return body


def _paren_depth(expression: str) -> int:
    """括号的最大嵌套深度（AST 只反映运算嵌套，**反映不出括号**，所以单独数一遍）。"""
    depth = deepest = 0
    for char in expression:
        if char == "(":
            depth += 1
            deepest = max(deepest, depth)
        elif char == ")":
            depth -= 1
    return max(deepest, depth)


def _code_like(question: str) -> ArithResult | None:
    """长得像**代码/注入**的输入 → 明确拒绝（而不是"没看懂"）。

    为什么要单独认这一类：`__import__("os")` / `open("x")` 这种输入本身**不是算式**
    （没有数字、没有运算符），如果只按"不是算术题，返回 None"处理，它会被路由丢给
    "没听懂"那一条 —— 用户和我们自己都看不出"它被安全地拒绝了"。
    这里给它一个**说得出口的拒绝理由**，测试也能据此断言"拒绝"而不是"没接住"。

    边界：概念类问题（"什么是 __init__ 方法"）优先——那种问题归普通问答，不在这里拒。
    """
    text = (question or "").strip()
    if not text or len(text) > MAX_EXPRESSION_LENGTH:
        return None
    if any(marker in text for marker in _CONCEPT_MARKERS):
        return None
    lowered = text.lower()
    if not any(marker in lowered for marker in _CODE_MARKERS):
        return None
    if any(word in text for word in _BUSINESS_WORDS):
        return None
    return ArithResult(
        expression=text,
        value=None,
        error="这个写法我不算 —— 我只认数字和 + - * / % ** 这些运算，不会去执行代码。",
    )


def format_number(value: float) -> str:
    """把计算结果写成**确定的字符串**（不带 0.30000000000000004 这种浮点尾巴）。"""
    rounded = round(float(value), 10)
    if rounded == int(rounded) and abs(rounded) < 1e15:
        return str(int(rounded))
    text = f"{rounded:.10f}".rstrip("0").rstrip(".")
    return text or "0"


_POW_PLACEHOLDER = "\x00POW\x00"


def _display(expression: str) -> str:
    """展示用的表达式：运算符两侧补一个空格（纯排版，**不动运算**）。

    `**` 先换成占位符再排版，否则会被下面那条 `*` 的规则拆成 `* *`。
    """
    text = re.sub(r"\s*\*\*\s*", _POW_PLACEHOLDER, expression)
    text = re.sub(r"\s*([+\-*/%])\s*", r" \1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(_POW_PLACEHOLDER, " ** ")


def solve(question: str) -> ArithResult | None:
    """一句话 → 算术结果；**不是算术题**返回 None（调用方据此走别的分支）。

    返回 None 与"返回一个 error 结果"是两件不同的事：
      · None            = 这不是算术题（交给路由去判别的意图）
      · error 非空      = 这是算术题，但被安全闸门拒了 / 除零了（如实说出来）
    """
    attack = _code_like(question)
    if attack is not None:
        return attack
    overlong = _overlong_expression(question)
    if overlong is not None:
        return ArithResult(
            overlong[:40] + "…",
            None,
            f"算式太长（超过 {MAX_EXPRESSION_LENGTH} 个字符），我不算。",
        )
    expression = extract_expression(question)
    if expression is None:
        return None
    if _paren_depth(expression) > MAX_DEPTH:
        return ArithResult(expression, None, f"括号嵌套太深（超过 {MAX_DEPTH} 层），我不算。")
    digits = len(re.sub(r"\D", "", expression))
    if digits > MAX_TOTAL_DIGITS:
        return ArithResult(expression, None, f"算式里的数字太多（超过 {MAX_TOTAL_DIGITS} 位），我不算。")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return ArithResult(expression, None, "这不是一个完整的算式，我没法算。")
    try:
        value = _eval_node(tree, 0)
    except Rejected as rejected:
        return ArithResult(expression, None, rejected.message)
    except ZeroDivisionError:                      # 兜底：% 与 / 之外不会有
        return ArithResult(expression, None, "除数是 0，没法算（数学上这是没有定义的）。")
    except (OverflowError, ValueError):
        return ArithResult(expression, None, "算出来的数太大，超出我能算的范围。")
    return ArithResult(expression, value, None)


__all__ = [
    "MAX_ABS_RESULT",
    "MAX_DEPTH",
    "MAX_EXPRESSION_LENGTH",
    "MAX_LITERAL_DIGITS",
    "MAX_POW_EXPONENT",
    "MAX_TOTAL_DIGITS",
    "ArithResult",
    "Rejected",
    "extract_expression",
    "format_number",
    "solve",
]
