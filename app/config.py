"""配置。

★ 设计原则：所有"会变的数字"都集中在这里，不散落在业务代码里。
  理由：这套系统的难点是"平衡点每个客户都不一样"（见 docs/决策记录.md D1），
  所以这些值必须可被租户配置覆盖，而不是写死。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("KEFU_DB", DATA_DIR / "kefu.db"))


def _load_env() -> None:
    """读项目根 .env（与另两个项目一致的做法）。"""
    env = PROJECT_ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

# ── 模型 ────────────────────────────────────────────────────────────────
# ★ 主模型 / 降级模型。降级顺序见 docs/决策记录.md：
#   主模型 → 同厂商便宜档 → 不调模型直接给检索原文 → 只转人工。
#   **故意不默认放"免费模型"**：很多免费模型条款允许用输入训练，
#   而这里的输入是公司内部资料和客户对话，属于合规红线。
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_MODEL_CHEAP = os.environ.get("LLM_MODEL_CHEAP", "deepseek-chat")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "30"))

# ── 检索 ────────────────────────────────────────────────────────────────
CHUNK_CHARS = int(os.environ.get("CHUNK_CHARS", "420"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "80"))
TOP_K = int(os.environ.get("TOP_K", "6"))

# ── ★ 判定阈值 ───────────────────────────────────────────────────────────
# 这三个是"分级自动化"的分界线。客户可以用档位词覆盖，但默认值要保守。
#   score >= AUTO    → 自动发送
#   DRAFT <= score   → AI 起草，人确认
#   score <  DRAFT   → 直接转人工
THRESHOLD_AUTO = float(os.environ.get("THRESHOLD_AUTO", "0.72"))
THRESHOLD_DRAFT = float(os.environ.get("THRESHOLD_DRAFT", "0.45"))

# ★ 时效性惩罚：命中了旧文档时给检索质量打折。
#   没有这个，知识库里有 2023 和 2025 两版矛盾文件时，
#   两边分数都很高 → 置信度很高 → 自动发出过期答案（真实事故）。
STALE_AFTER_DAYS = int(os.environ.get("STALE_AFTER_DAYS", "730"))
STALE_PENALTY = float(os.environ.get("STALE_PENALTY", "0.6"))

# ── 人工接管 ────────────────────────────────────────────────────────────
# ★ 同一个人一天最多被接管几次（用户的反馈：
#   「人工接管同一个人的次数不能太多」）。
#
# ★ 为什么要有这个上限：
#   同一个人反复被接管，只有两种可能 ——
#     ① AI 一直答不好他这类问题（该去补知识库 / 改分级）
#     ② 客服答完就"结束"了，客户又问，又被转一次（该让会话留在人工手上）
#   两种都是**系统问题**，不是客户的问题。所以不是拦住不管，
#   而是**让这件事显形**：超了就在工作台上明确提示。
TAKEOVER_MAX_PER_DAY = int(os.environ.get("TAKEOVER_MAX_PER_DAY", "3"))
# 统计"一天"用的小时数（用滚动窗口，不是自然日 —— 23:59 和 00:01 不该算两天）
TAKEOVER_WINDOW_HOURS = int(os.environ.get("TAKEOVER_WINDOW_HOURS", "24"))

# ── 连发消息的合并窗口 ──────────────────────────────────────────
# ★ 客户经常把一句话拆成几条发（我想问一下 / 关于报价的事 / 能便宜点吗）。
#   不合并的话，AI 会对着**半句话**回答，而且转人工时队列里记的是中间那句，
#   客服拿到的需求是半截的（实测过）。
#
#   代价：每条消息的响应**多等这么多秒**。
#   2.5 秒是个折中 —— 比大多数人打字间隔长，又短到不觉得卡。
#   设成 0 可以关掉（紧急情况下恢复「立即回答」的行为）。
DEBOUNCE_SECONDS = float(os.environ.get("DEBOUNCE_SECONDS", "2.5"))
# 合并的上限（防止有人刷屏拼出一条超长消息）
DEBOUNCE_MAX_MESSAGES = int(os.environ.get("DEBOUNCE_MAX_MESSAGES", "6"))

# ── 安全 ────────────────────────────────────────────────────────────────
# ★ 这些是平台强制、客户不可关的底线（见 docs/决策记录.md D5）。
#   注意：这里是"漏网检测"，真正的拦截在 policy.py 里按意图判定。
BANNED_ROLE_WORDS = (
    "财务部", "法务部", "技术部", "售后部", "人事部", "采购部",
    "财务", "法务", "主管", "经理", "专员", "工号",
)
BANNED_COMMITMENT = (
    "一定", "保证", "百分百", "绝对", "承诺",
    "最低价", "内部价", "底价",
)

# 内部知识源目录（里程碑 1 先用最简单的连接器）
KB_DIR = Path(os.environ.get("KB_DIR", DATA_DIR / "kb"))
KB_DIR.mkdir(parents=True, exist_ok=True)
