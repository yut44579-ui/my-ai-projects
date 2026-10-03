"""D4 数据契约：EvidenceValue。

本文件是「决策已冻结、代码先行落地」的契约定义（骨架 TASK 只放类型，不做业务）。
所有对外的业务数字都必须包成 EvidenceValue 返回，前端不得自行计算业务数字。

state 三态互斥，绝不允许混淆：
  VALID    —— 有可信数据，value 可以是 0（0 是有效值，不是"没有数据"）
  NO_DATA  —— 确实没有数据，value 必须为 None
  ERROR    —— 取数失败，value 必须为 None，且必须说明原因
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ValueState(str, Enum):
    VALID = "VALID"
    NO_DATA = "NO_DATA"
    ERROR = "ERROR"


class SourceType(str, Enum):
    """数字来源类型。IMPORT=导入数据；MANUAL=人工录入；SYSTEM=系统计算。"""

    IMPORT = "IMPORT"
    MANUAL = "MANUAL"
    SYSTEM = "SYSTEM"


class EvidenceValue(BaseModel):
    """带状态与证据引用业务数字。"""

    value: float | int | None = Field(default=None, description="业务数值")
    state: ValueState = Field(description="三态：VALID / NO_DATA / ERROR")
    source_type: SourceType = Field(description="数字来源类型")
    evidence_ref: str | None = Field(
        default=None, description="证据锚点（D5：风险证据用 message_id）"
    )
    reason: str | None = Field(default=None, description="NO_DATA / ERROR 时的说明")

    @model_validator(mode="after")
    def _enforce_state_semantics(self) -> "EvidenceValue":
        if self.state is ValueState.VALID:
            if self.value is None:
                raise ValueError("state=VALID 时 value 不能为 None（0 是合法值）")
        else:
            if self.value is not None:
                raise ValueError(
                    f"state={self.state.value} 时 value 必须为 None，"
                    "禁止用 0 冒充『没有数据』/『取数失败』"
                )
            if self.state is ValueState.ERROR and not self.reason:
                raise ValueError("state=ERROR 必须给出 reason")
        return self


def build_evidence(
    value: Any,
    *,
    source_type: SourceType,
    evidence_ref: str | None = None,
) -> EvidenceValue:
    """由原始取数结果构造 EvidenceValue：None -> NO_DATA，其余 -> VALID。"""
    if value is None:
        return EvidenceValue(
            value=None,
            state=ValueState.NO_DATA,
            source_type=source_type,
            evidence_ref=evidence_ref,
            reason="无数据",
        )
    return EvidenceValue(
        value=value,
        state=ValueState.VALID,
        source_type=source_type,
        evidence_ref=evidence_ref,
    )


def no_data_evidence(
    reason: str,
    *,
    source_type: SourceType,
    evidence_ref: str | None = None,
) -> EvidenceValue:
    """显式空态：这块确实没有数据（功能未接入 / 该口径下一条都没有）。

    ★ 与 build_evidence(None) 的区别只在 reason：这里必须写清"为什么没有"。
      无论哪种情况 value 都是 None —— 绝不许用 0 冒充「没有数据」。
    """
    return EvidenceValue(
        value=None,
        state=ValueState.NO_DATA,
        source_type=source_type,
        evidence_ref=evidence_ref,
        reason=reason,
    )
