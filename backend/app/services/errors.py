"""导入失败语义（TASK-001 §五）。

★ 文件级失败（解析不了、加密、非 CSV/XLSX、表头整行空、超限）→ status=FAILED、零入库、
  返回 400 + 明确错误码；本实现里文件级失败不落 import_batches 行（"零入库"），
  因此也不会阻塞同一份文件修好后重传。
★ 行级失败 → status=PARTIAL，好行入库、坏行进 skipped_reasons（不在这里抛）。
"""

from __future__ import annotations

from enum import Enum


class ApiErrorCode(str, Enum):
    """非导入类接口的错误码（TASK-003 起）。

    与导入类错误码同一套响应格式：{"error": <错误码>, "message": <说明>}。
    """

    # —— TASK-003 沟通记录 ——
    CUSTOMER_SENDER_FORBIDDEN = "customer_sender_forbidden"  # D9：站内没有真实客户渠道
    INVALID_SENDER_TYPE = "invalid_sender_type"
    INVALID_MESSAGE_TYPE = "invalid_message_type"
    EMPTY_CONTENT = "empty_content"
    MESSAGE_NOT_FOUND = "message_not_found"

    # —— TASK-004 AI 回复 ——
    LLM_UNAVAILABLE = "llm_unavailable"  # LLM 调用失败：如实告知，禁止编造回复（D10）


class ApiFailure(Exception):
    """带错误码的接口失败。路由层/异常处理器据此返回 {"error": code, "message": ...}。"""

    def __init__(
        self,
        code: ApiErrorCode | "ImportErrorCode",
        message: str,
        *,
        http_status: int = 400,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = detail or {}

    def to_body(self) -> dict:
        body = {"error": self.code.value, "message": self.message}
        body.update(self.detail)
        return body


class ImportErrorCode(str, Enum):
    """对外错误码。★ 前 5 个是规格冻结的，后 4 个是本 TASK 补齐的接口级错误。"""

    # —— 规格冻结的文件级错误码（返回 400 + {"error": code}）——
    EMPTY_FILE = "empty_file"
    UNSUPPORTED_FORMAT = "unsupported_format"
    TOO_LARGE = "too_large"
    TOO_MANY_ROWS = "too_many_rows"
    PARSE_ERROR = "parse_error"

    # —— 映射类错误（规格要求"明确报错，不许任选一个"）——
    AMBIGUOUS_MAPPING = "ambiguous_mapping"
    INVALID_MAPPING = "invalid_mapping"

    # —— 防换文件 / 防重复导入 ——
    SHA256_MISMATCH = "sha256_mismatch"
    DUPLICATE_IMPORT = "duplicate_import"


class ImportFailure(ApiFailure):
    """带错误码的导入失败。路由层据此返回 {"error": code, ...}。

    ★ 复用 ApiFailure 的响应格式（TASK-003 起两者统一），本类只把错误码收窄成 ImportErrorCode。
    """

    def __init__(
        self,
        code: ImportErrorCode,
        message: str,
        *,
        http_status: int = 400,
        detail: dict | None = None,
    ) -> None:
        super().__init__(code, message, http_status=http_status, detail=detail)
