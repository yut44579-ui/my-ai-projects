"""个人资料与 AI 供应商（TASK-037）。

═══════════════════════════════════════════════════════════════════════
【本模块解决什么】
═══════════════════════════════════════════════════════════════════════
    用户要求「个人中心」要有：状态（在线/忙碌/离线）、上传头像、改个人信息、
    改账号口令、明暗主题，以及**能改 AI 的 API / 切换各类模型**
    （DeepSeek / GPT / Claude / Qwen / Gemini）。

    ① `users` 表**扩展字段**（不新建表）：
         avatar_path / presence / theme / email / phone / bio
       ★ 为什么加在 users 上而不是建 profile 表：这是一对一到用户、
         生命周期完全一致的字段。单独建表只会让每次读用户都要 join
         （§五 简单能解决绝不复杂化）。

    ② 新建 `ai_providers` 表：各家模型的配置与当前启用项。
       ★ **api_key 加密存储**，复用数据连接那套 crypto（D40 的凭据加密），
         对外只返回"是否已配置"，永不回显（同一个安全纪律）。

═══════════════════════════════════════════════════════════════════════
【presence 是"人工设置的意愿状态"，不是"在线探测"】
═══════════════════════════════════════════════════════════════════════
    ★ 必须说清楚：本系统**没有**websocket 心跳、没有实时在线探测。
      所以「在线/忙碌/离线」是**用户自己选的**状态，不是系统探测出来的。
      界面上要如实这样写 —— 把它显示成"实时在线"就是编造（§二十六）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values

Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class PresenceStatus(str, Enum):
    """用户的**自我声明**状态（不是系统探测的在线状态）。"""

    ONLINE = "ONLINE"
    BUSY = "BUSY"
    OFFLINE = "OFFLINE"


class ThemePreference(str, Enum):
    """界面主题偏好。

    ★ SYSTEM = 跟随操作系统；LIGHT/DARK = 用户强制指定。
    """

    SYSTEM = "SYSTEM"
    LIGHT = "LIGHT"
    DARK = "DARK"


class AiProviderKind(str, Enum):
    """支持的模型供应商。

    ★ 两类协议（决定了请求体怎么拼）：
       · OPENAI_COMPATIBLE：DeepSeek / OpenAI(GPT) / 通义千问(Qwen) / 智谱 等
         都用 OpenAI 的 `/chat/completions` 格式，可共用一套代码
       · ANTHROPIC：Claude 用 `/v1/messages`，请求体与响应结构都不同
       · GEMINI：Google 用 `generateContent`，又是不一样的结构
      所以适配层按 `protocol` 分派，而不是按供应商名硬编码一堆分支。
    """

    DEEPSEEK = "DEEPSEEK"
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"
    QWEN = "QWEN"
    GEMINI = "GEMINI"
    CUSTOM = "CUSTOM"


class AiProtocol(str, Enum):
    """请求协议类型（适配层按这个分派）。"""

    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    ANTHROPIC = "ANTHROPIC"
    GEMINI = "GEMINI"


#: 每种供应商的默认配置与协议。
#: ★ 只写**公开可查的官方地址与常见模型名**；具体可用的模型以各家文档为准，
#:   界面上允许用户自己改，不假装这是完整清单。
PROVIDER_PRESETS: dict[AiProviderKind, dict] = {
    AiProviderKind.DEEPSEEK: {
        "label": "DeepSeek（深度求索）",
        "protocol": AiProtocol.OPENAI_COMPATIBLE,
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "docs": "https://platform.deepseek.com",
    },
    AiProviderKind.OPENAI: {
        "label": "OpenAI（GPT）",
        "protocol": AiProtocol.OPENAI_COMPATIBLE,
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "docs": "https://platform.openai.com",
    },
    AiProviderKind.ANTHROPIC: {
        "label": "Anthropic（Claude）",
        "protocol": AiProtocol.ANTHROPIC,
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-sonnet-latest",
        "docs": "https://docs.anthropic.com",
    },
    AiProviderKind.QWEN: {
        "label": "通义千问（Qwen）",
        "protocol": AiProtocol.OPENAI_COMPATIBLE,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "docs": "https://help.aliyun.com/zh/dashscope",
    },
    AiProviderKind.GEMINI: {
        "label": "Google（Gemini）",
        "protocol": AiProtocol.GEMINI,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-1.5-flash",
        "docs": "https://ai.google.dev",
    },
    AiProviderKind.CUSTOM: {
        "label": "自定义（OpenAI 兼容）",
        "protocol": AiProtocol.OPENAI_COMPATIBLE,
        "base_url": "",
        "model": "",
        "docs": "",
    },
}


class AiProviderConfig(Base):
    """一个模型供应商的配置。

    ★ `api_key_encrypted` 用 core/crypto.py 加密（同数据连接凭据，D40）。
      对外**只返回是否已配置**，永不回显内容。
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    kind: Mapped[AiProviderKind] = mapped_column(
        SAEnum(AiProviderKind, name="ai_provider_kind", values_callable=_enum_values),
        nullable=False,
        unique=True,
    )
    protocol: Mapped[AiProtocol] = mapped_column(
        SAEnum(AiProtocol, name="ai_protocol", values_callable=_enum_values),
        nullable=False,
        default=AiProtocol.OPENAI_COMPATIBLE,
    )

    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    #: ★ 加密后的 API Key；对外只报是否已配置
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: ★ 全局只有一个生效（切供应商 = 切换这个标记）。由服务层保证唯一。
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: 最近一次连通性测试的真实结果（不人工写，只由测试驱动，同 D40）
    last_test_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AiProviderConfig {self.kind} active={self.is_active}>"
