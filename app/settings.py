"""运行期设置。

★★ 为什么要单独做一层：
    原来模型配置只能读环境变量（.env），改一次要重启服务 ——
    用户的原话是「控制台模型也弄不了」。
    对一个要给客户用的产品，**模型必须能在界面上配**：
    客户可能先用我们的 key 试，之后换成自己的；
    也可能从 DeepSeek 换到别家。

★ 优先级：**数据库 > 环境变量 > 默认值**
    · 数据库：界面上改的，立即生效
    · 环境变量：部署时配的（.env），作为出厂默认
    · 默认：代码里的兜底

★ 密钥的显示规则：**界面只回显遮罩后的**（sk-eee…01），
  永远不回传完整 key —— 否则界面上一次查看就等于泄露一次。
"""

from __future__ import annotations

from typing import Any

from . import config, store

# 可配的模型项：(键, 环境变量名, 说明)
MODEL_FIELDS: list[tuple[str, str, str]] = [
    ("base_url", "LLM_BASE_URL", "接口地址（DeepSeek 兼容 OpenAI 协议，换别家改这里）"),
    ("api_key", "LLM_API_KEY", "API Key"),
    ("model", "LLM_MODEL", "主模型"),
    ("model_cheap", "LLM_MODEL_CHEAP", "降级用的便宜模型"),
]


def _env_defaults() -> dict[str, str]:
    return {
        "base_url": config.LLM_BASE_URL,
        "api_key": config.LLM_API_KEY,
        "model": config.LLM_MODEL,
        "model_cheap": config.LLM_MODEL_CHEAP,
    }


def mask_key(k: str) -> str:
    """遮罩密钥。★ 前 6 位 + 后 4 位，中间打点。

    前 6 位是为了让用户能认出"这是我哪个 key"（DeepSeek 的 key 前缀都差不多，
    但不同账号的会不同），后 4 位帮助比对。
    ★ 中间部分**任何情况下都不回传**。
    """
    if not k:
        return ""
    if len(k) <= 12:
        return "*" * len(k)
    return f"{k[:6]}{'*' * 8}{k[-4:]}"


def get_model_config(*, mask: bool = True) -> dict[str, Any]:
    """读当前模型配置。默认遮罩密钥。"""
    env = _env_defaults()
    out: dict[str, Any] = {}
    for key, _envname, desc in MODEL_FIELDS:
        row = store.one("SELECT value FROM setting WHERE key=?", (f"llm.{key}",))
        val = (row or {}).get("value")
        source = "database"
        if val is None:
            val = env.get(key, "")
            source = "env" if val else "default"
        if key == "api_key":
            out[key] = mask_key(val) if mask else val
            out[f"{key}_set"] = bool(val)
        else:
            out[key] = val
        out[f"{key}_source"] = source
    out["ready"] = bool(
        (store.one("SELECT value FROM setting WHERE key='llm.api_key'") or {}).get("value")
        or env.get("api_key")
    )
    return out


def set_model_config(patch: dict[str, str]) -> dict[str, Any]:
    """改模型配置。★ 只改传进来的字段，没传的保持原样。

    ★ 密钥传空字符串表示"不改"，传特定值才改 ——
      因为界面上显示的是遮罩后的值，用户没动它就会原样提交，
      我们**不能把遮罩串当成新 key 存进去**（那服务立刻就不能用了）。
    """
    changed = []
    for key, _envname, _desc in MODEL_FIELDS:
        if key not in patch:
            continue
        val = (patch.get(key) or "").strip()
        if key == "api_key":
            # ★ 等于遮罩串 / 空 → 视为"不改"
            cur = get_model_config(mask=False).get("api_key", "")
            if not val or "*" in val or val == cur:
                continue
        store.run(
            """INSERT INTO setting (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (f"llm.{key}", val, store.now()),
        )
        changed.append(key)
    return {"changed": changed}


def test_model() -> dict[str, Any]:
    """真调一次模型，验证配置对不对。

    ★ 必须**真调**。只检查"字段填了没有"没有意义 ——
      最常见的问题是 key 错、地址错、模型名错，只有真调才能发现。
    """
    from . import llm

    cfg = get_model_config(mask=False)
    if not cfg.get("api_key"):
        return {"ok": False, "error": "还没填 API Key"}
    llm.reset_client()
    try:
        r = llm._call(cfg["model"], "你是一个测试助手。", "只回复两个字：正常")
        if r.text:
            return {
                "ok": True,
                "model": cfg["model"],
                "reply": r.text[:40],
                "tokens": r.tokens_in + r.tokens_out,
            }
        return {"ok": False, "error": "模型返回了空内容", "notes": r.notes}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


# ══════════════════════════════════════════════════════════════════════
# 通用设置（非模型的）
# ══════════════════════════════════════════════════════════════════════

def get(key: str, default: str = "") -> str:
    row = store.one("SELECT value FROM setting WHERE key=?", (key,))
    return (row or {}).get("value") or default


def put(key: str, value: str) -> None:
    store.run(
        """INSERT INTO setting (key, value, updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, store.now()),
    )
