"""api_exports.py · 「直接存到桌面」+「在文件夹中打开」的 HTTP 端点（FR-002B）。

════════════════════════════════════════════════════════════════════════
【端点清单（全部是新增路径，与既有端点零交集）】
════════════════════════════════════════════════════════════════════════
    POST /api/exports/desktop    把一次导出**直接落到本机桌面**（销售表 / 报告两种来源）
    POST /api/exports/reveal     在资源管理器里定位**刚刚生成的那个文件**

════════════════════════════════════════════════════════════════════════
【为什么是"新端点"而不是给老端点加参数】
════════════════════════════════════════════════════════════════════════
① 老端点的**语义一个字不改**：`GET /api/tables/{table}/export` 与
   `GET /api/conversations/{id}/report/export` 照旧把文件**发回浏览器**，
   老下载路径原样可用（评审 ④：不能因为有新方案就把老的删掉）。
   本模块只是**多开一条出口**，落盘到桌面，两边**渲染的是同一份字节**：
   表导出走 `export_module.build_export`，报告走 `api_chat.render_report_file`。
② 请求体里**没有路径字段**：谁能定义"写到哪儿"就只能由服务端说了算。
   `extra="forbid"` 把 `{"path": "C:\\xxx"}` 这种尝试直接顶回 422 —— 不是"忽略未知字段"，
   而是"多一个我不认识的字段就报错"。这条是**结构性**的：请求里根本没有能指定路径的地方。
③ 「在文件夹中打开」只认**刚刚生成的那个文件**：请求只能带 `export_id` 做一致性校验，
   真正传给 `explorer.exe` 的路径由服务端从 `app.desktop.last_saved()` 里取，
   **不接受**任何来自客户端的路径或命令。

════════════════════════════════════════════════════════════════════════
【部署前提（必须写在最显眼处，别被当成通用能力）】
════════════════════════════════════════════════════════════════════════
"服务端写桌面"只在【服务端进程与用户桌面同机、同 Windows 会话】时成立（本机单用户自用：
本进程就是用户自己机器上的进程，127.0.0.1 访问）。响应里的 `deployment` 字段如实写着这句话。
换到服务器/容器/多用户部署时这条能力**不成立**，必须停用或换成用户自选下载位置。

详细机制（桌面目录怎么解析、原子写、防重名）见 `app/desktop.py` 的模块文档。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app import desktop
# 报告渲染**复用下载端点那一条路**（`render_report_file`），不在这里另起一套 ——
# 两条出口拿到的必须是同一份字节、同一个文件名。
from app.api_chat import render_report_file
from app.datasets import export as export_module
# 这两个私有助手复用是为了**同源**（不是图省事）：
#   `_query_params` 决定"HTTP 参数 → 取数参数"的映射，导出条件必须与页面/老导出端点逐字段一致；
#   `_error_from`    决定"取数层错误 → HTTP 状态码"，否则 export_too_large 会变成 500。
# 抄一份到本文件，就会出现"同一个超限条件，一个端点 413、另一个 500"的分叉。
from app.api_datasets import _error_from, _query_params

router = APIRouter(prefix="/api/exports", tags=["exports"])

# 报告那条出口的默认格式（与 `GET /api/conversations/{id}/report/export` 的默认一致）
_REPORT_DEFAULT_FORMAT = "docx"
# 业务表那条出口的默认格式（与页面上的「导出 Excel」一致）
_TABLE_DEFAULT_FORMAT = "xlsx"

_TABLE_ONLY_FIELDS = ("table", "dataset_id", "start", "end", "search",
                      "sort", "order", "dimension", "metric")


class ExportsApiError(HTTPException):
    """带机器可读 `code` 的 HTTPException（与 api_datasets/api_chat 同形，靠鸭子类型被统一处理器接住）。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message


# 领域错误码 → HTTP 状态（服务端环境问题用 5xx，用户输入问题用 4xx，别混）
_DESKTOP_STATUS: dict[str, int] = {
    "desktop_unavailable": 503,        # 这台机器/这个会话拿不到桌面 —— 服务端环境问题
    "desktop_not_a_directory": 503,
    "desktop_write_failed": 500,
    "desktop_name_exhausted": 409,     # 桌面上同名副本排满了 —— 需要人先清理
    "reveal_unsupported": 501,         # 非 Windows：明确说"没实现"，不是"出错"
    "reveal_failed": 500,
    "file_missing": 404,
}


def _desktop_error(exc: desktop.DesktopError) -> ExportsApiError:
    return ExportsApiError(_DESKTOP_STATUS.get(exc.code, 500), exc.code, exc.message)


class DesktopExportRequest(BaseModel):
    """一次"存到桌面"的请求。

    **刻意没有 path / dir 字段**（`extra="forbid"` 会把它们顶回 422）。
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["table", "report"]
    format: str | None = None

    # source=table：与 GET /api/tables/{table}/export 的查询参数**同名同义**
    table: str | None = None
    dataset_id: str | None = None
    start: str | None = None
    end: str | None = None
    search: str | None = None
    sort: str | None = None
    order: str | None = None
    dimension: str | None = None
    metric: str | None = None

    # source=report：哪一次问答的报告
    conversation_id: str | None = None


class RevealRequest(BaseModel):
    """「在文件夹中打开」的请求：只能带一个 `export_id` 做一致性校验，**没有路径字段**。"""

    model_config = ConfigDict(extra="forbid")

    export_id: str | None = None


@router.post("/desktop", status_code=201, summary="把这次导出直接存到本机桌面（不覆盖同名文件）")
def save_export_to_desktop(payload: DesktopExportRequest) -> dict[str, Any]:
    """渲染 → 落桌面 → 返回**如实**的落盘信息（真实文件名、字节数、目录、时间）。

    文件名不覆盖、注入字符会被清洗、写入是原子的 —— 细节见 `app/desktop.py`。
    这条出口与浏览器下载**共用同一个渲染函数**，所以两边内容一致。
    """
    if payload.source == "table":
        if not payload.table:
            raise ExportsApiError(400, "export_table_missing",
                                  "source=table 时必须给出 table（customers / products / sales / raw）。")
        if payload.conversation_id:
            raise ExportsApiError(400, "export_source_mismatch",
                                  "source=table 的请求里不该出现 conversation_id —— 两个来源别混着传。")
        fmt = payload.format or _TABLE_DEFAULT_FORMAT
        try:
            filename, content, _mime = export_module.build_export(
                payload.table, fmt,
                **_query_params(payload.start, payload.end, payload.search, payload.sort,
                                payload.order, payload.dimension, payload.metric,
                                payload.dataset_id),
            )
        except Exception as exc:                        # noqa: BLE001
            raise _error_from(exc) from exc
    else:
        if not payload.conversation_id:
            raise ExportsApiError(400, "export_conversation_missing",
                                  "source=report 时必须给出 conversation_id（哪一次问答的报告）。")
        stray = [name for name in _TABLE_ONLY_FIELDS if getattr(payload, name) is not None]
        if stray:
            raise ExportsApiError(400, "export_source_mismatch",
                                  f"source=report 的请求里不该出现 {stray} —— 两个来源别混着传。")
        content, filename, _mime = _report_file(payload.conversation_id,
                                                payload.format or _REPORT_DEFAULT_FORMAT)

    try:
        record = desktop.save_to_desktop(filename, content)
    except desktop.DesktopError as exc:
        raise _desktop_error(exc) from exc

    return {
        "export_id": record["export_id"],
        "source": payload.source,
        "file_name": record["file_name"],
        "bytes": record["bytes"],
        "desktop_dir": record["desktop_dir"],
        "saved_at": record["saved_at"],
        # 前端下一步要调的就是这个路径（只告诉它"能定位刚刚那个文件"，不给它传路径的权力）
        "reveal_url": "/api/exports/reveal",
        "deployment": record["deployment"],
    }


@router.post("/reveal", summary="在资源管理器里定位刚刚存到桌面的那个文件")
def reveal_last_export(payload: RevealRequest | None = None) -> dict[str, Any]:
    """`explorer.exe /select,"<path>"` —— 打开文件夹并选中文件。

    `export_id` **可省略**（省略 = 定位最近那个）；给了就必须与最近一次一致 ——
    这样"打开别的文件"这件事在接口层面就做不到（只认刚刚生成的那个文件）。
    """
    record = desktop.last_saved()
    if record is None:
        raise ExportsApiError(404, "nothing_to_reveal",
                              "还没有通过本功能存过文件 —— 先「存到桌面」，再来打开所在文件夹。")
    given = (payload.export_id if payload else None) or None
    if given and given != record["export_id"]:
        raise ExportsApiError(
            409, "export_superseded",
            "只能定位**刚刚生成的那个文件**；你给的不是最近一次的编号（可能是之前的那份）。",
        )
    try:
        desktop.reveal_in_file_manager(_path_of(record))
    except desktop.DesktopError as exc:
        raise _desktop_error(exc) from exc
    return {
        "revealed": True,
        "export_id": record["export_id"],
        "file_name": record["file_name"],
        "platform_supported": True,
    }


def _path_of(record: dict[str, Any]) -> Path:
    """落盘记录 → 目标路径（**只从服务端记录里取**，与请求无关）。"""
    return Path(str(record["path"]))


def _report_file(conversation_id: str, fmt: str) -> tuple[bytes, str, str]:
    """报告 → `(内容, 文件名, 内容类型)`；名字保留成函数是为了调用点读起来一致（与表那条对称）。"""
    return render_report_file(conversation_id, fmt)


__all__ = ["router"]
