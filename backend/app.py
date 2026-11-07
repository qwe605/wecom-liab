"""客服宝 FastAPI 应用入口。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.config import get_settings
from backend.controllers import admin_chat_records, admin_users, ai, auth, wecom
from backend.database.session import get_engine
from backend.errors import ConcurrencyConflictError


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。

    Returns:
        已注册 Controller、跨域和统一异常处理的应用实例。
    """

    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="2.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.include_router(auth.router)
    application.include_router(wecom.router)
    application.include_router(ai.router)
    application.include_router(admin_chat_records.router)
    application.include_router(admin_users.router)

    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if (frontend_dist / "assets").is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=frontend_dist / "assets"),
            name="frontend-assets",
        )

        @application.get("/", include_in_schema=False)
        @application.get("/login", include_in_schema=False)
        @application.get("/login/", include_in_schema=False)
        @application.get("/sidebar/", include_in_schema=False)
        @application.get("/sidebar-demo/", include_in_schema=False)
        @application.get("/sidebar-v2/", include_in_schema=False)
        @application.get("/review/", include_in_schema=False)
        @application.get("/records/", include_in_schema=False)
        @application.get("/users/", include_in_schema=False)
        @application.get("/change-password/", include_in_schema=False)
        def frontend_entry() -> FileResponse:
            """由 FastAPI 返回 Vue 单页应用入口，前端路由决定具体管理页面。"""

            return FileResponse(frontend_dist / "index.html")

    @application.get("/api/health", tags=["system"])
    def health() -> dict:
        """检查应用进程和 MySQL 连接。"""

        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "ok": True,
            "database": "mysql",
            "time": datetime.now(timezone.utc).isoformat(),
        }

    @application.exception_handler(PermissionError)
    async def permission_error(_: Request, exc: PermissionError) -> JSONResponse:
        """把领域权限异常映射为 HTTP 403。"""

        return JSONResponse(status_code=403, content={"error": str(exc)})

    @application.exception_handler(ValueError)
    async def validation_error(_: Request, exc: ValueError) -> JSONResponse:
        """把业务参数错误映射为 HTTP 400。"""

        return JSONResponse(status_code=400, content={"error": str(exc)})

    @application.exception_handler(LookupError)
    async def not_found_error(_: Request, exc: LookupError) -> JSONResponse:
        """把领域资源不存在异常映射为 HTTP 404。"""

        return JSONResponse(status_code=404, content={"error": str(exc)})

    @application.exception_handler(RuntimeError)
    async def integration_error(_: Request, exc: RuntimeError) -> JSONResponse:
        """把第三方不可用映射为 HTTP 503。"""

        return JSONResponse(status_code=503, content={"error": str(exc)})

    @application.exception_handler(ConcurrencyConflictError)
    async def concurrency_error(_: Request, exc: ConcurrencyConflictError) -> JSONResponse:
        """把审核乐观锁冲突映射为 HTTP 409，要求页面重新加载。"""

        return JSONResponse(status_code=409, content={"error": str(exc)})

    @application.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        """统一 FastAPI 主动异常的响应结构。"""

        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    return application


app = create_app()
