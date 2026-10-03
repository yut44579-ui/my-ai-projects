"""FastAPI 应用入口。

启动（在 D:\\biz-assistant 下）：
    .venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --reload
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.router import api_router
from app.core.config import settings
from app.services.errors import ApiFailure


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI 商业项目助理 API",
        description=(
            "V1 单租户（D3）。所有业务数字以 EvidenceValue 返回（D4）；"
            "AI 安全拦截在代码层（D7）。"
        ),
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 统一失败出口：{"error": <错误码>, "message": ...}（TASK-001 §五 起，TASK-003 沿用）
    # 文件级导入失败 = 零入库，不会留下任何半截数据；ImportFailure 是 ApiFailure 的子类，
    # 因此这一个处理器同时覆盖导入类与沟通/AI 类错误码。
    @app.exception_handler(ApiFailure)
    async def _api_failure_handler(_request: Request, exc: ApiFailure) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_body())

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )
