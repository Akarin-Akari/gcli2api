"""
Main Web Integration - Integrates all routers and modules
集合router并开启主服务
"""

# 加载 .env 文件中的环境变量（必须在其他导入之前）
from dotenv import load_dotenv
load_dotenv()  # 加载 .env 文件

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import get_server_host, get_server_port
from log import log

# Import IDE compatibility middleware
from src.ide_compat import IDECompatMiddleware

# Import managers and utilities
from src.credential_manager import CredentialManager
from src.gemini_router import router as gemini_router

# Import all routers
from src.antigravity_router import router as antigravity_router
from src.antigravity_anthropic_router import router as antigravity_anthropic_router
from src.openai_router import router as openai_router
from src.task_manager import shutdown_all_tasks
from src.web_routes import router as web_router
from src.unified_gateway_router import router as gateway_router, augment_router

# 全局凭证管理器
global_credential_manager = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global global_credential_manager

    log.info("启动 GCLI2API 主服务")

    # 初始化配置缓存（优先执行）
    try:
        import config
        await config.init_config()
        log.info("配置缓存初始化成功")
    except Exception as e:
        log.error(f"配置缓存初始化失败: {e}")

    # 初始化全局凭证管理器
    try:
        global_credential_manager = CredentialManager()
        await global_credential_manager.initialize()
        log.info("凭证管理器初始化成功")
    except Exception as e:
        log.error(f"凭证管理器初始化失败: {e}")
        global_credential_manager = None

    # ================================================================
    # [PHASE 2 DUAL_WRITE] 启用缓存双写模式 - 生产测试
    # 作者: Claude Opus 4 (浮浮酱)
    # 日期: 2026-01-10
    # 说明: 启用双写策略，同时写入旧缓存和新的分层缓存系统
    #       用于验证新缓存系统的正确性，不影响现有功能
    # 
    # 如需禁用，请注释以下代码块
    # ================================================================
    try:
        from src.signature_cache import (
            enable_migration_mode,
            set_migration_phase,
            get_migration_status
        )
        # 启用迁移模式
        enable_migration_mode()
        # 设置为 DUAL_WRITE 阶段（双写模式）
        set_migration_phase("DUAL_WRITE")
        status = get_migration_status()
        log.info(f"[PHASE 2] 缓存双写模式已启用: {status}")
    except Exception as e:
        log.warning(f"[PHASE 2] 缓存双写模式启用失败（非致命）: {e}")
    # ================================================================
    # [END PHASE 2 DUAL_WRITE]
    # ================================================================

    # OAuth回调服务器将在需要时按需启动

    yield

    # 清理资源
    log.info("开始关闭 GCLI2API 主服务")

    # 首先关闭所有异步任务
    try:
        await shutdown_all_tasks(timeout=10.0)
        log.info("所有异步任务已关闭")
    except Exception as e:
        log.error(f"关闭异步任务时出错: {e}")

    # 然后关闭凭证管理器
    if global_credential_manager:
        try:
            await global_credential_manager.close()
            log.info("凭证管理器已关闭")
        except Exception as e:
            log.error(f"关闭凭证管理器时出错: {e}")

    log.info("GCLI2API 主服务已停止")


# 创建FastAPI应用
app = FastAPI(
    title="GCLI2API",
    description="Gemini API proxy with OpenAI compatibility",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================================================================
# [IDE COMPAT] IDE 兼容中间件 - 处理 IDE 客户端的特殊行为
# 作者: Claude Opus 4.5 (浮浮酱)
# 日期: 2026-01-17
# 说明: 在 CORS 之后、路由之前添加 IDE 兼容中间件
#       - 检测客户端类型 (Claude Code / Cursor / Augment 等)
#       - 对 IDE 客户端的消息进行净化 (处理无效签名等)
#       - 维护会话状态 (SCID 状态机)
# ================================================================
try:
    app.add_middleware(IDECompatMiddleware)
    log.info("[STARTUP] IDE 兼容中间件已启用")
except Exception as e:
    log.warning(f"[STARTUP] IDE 兼容中间件启用失败（非致命）: {e}")
# ================================================================
# [END IDE COMPAT]
# ================================================================

# 挂载路由器
# OpenAI兼容路由 - 处理OpenAI格式请求
app.include_router(openai_router, prefix="", tags=["OpenAI Compatible API"])

# Gemini原生路由 - 处理Gemini格式请求
app.include_router(gemini_router, prefix="", tags=["Gemini Native API"])

# Antigravity路由 - 处理OpenAI格式请求并转换为Antigravity API
app.include_router(antigravity_router, prefix="", tags=["Antigravity API"])

# Antigravity Anthropic Messages 路由 - Anthropic Messages 格式兼容
app.include_router(antigravity_anthropic_router, prefix="", tags=["Antigravity Anthropic Messages"])

# Web路由 - 包含认证、凭证管理和控制面板功能
app.include_router(web_router, prefix="", tags=["Web Interface"])

# 统一网关路由 - 整合多后端服务，支持优先级路由和故障转移
app.include_router(gateway_router, prefix="", tags=["Unified Gateway"])

# Augment Code 兼容路由 - 处理不带 /gateway 前缀的请求
app.include_router(augment_router, prefix="", tags=["Augment Code Compatibility"])

# 静态文件路由 - 服务docs目录下的文件（如捐赠图片）
app.mount("/docs", StaticFiles(directory="docs"), name="docs")

# 静态文件路由 - 服务front目录下的文件（HTML、JS、CSS等）
app.mount("/front", StaticFiles(directory="front"), name="front")


# 保活接口（仅响应 HEAD）
@app.head("/keepalive")
async def keepalive() -> Response:
    return Response(status_code=200)


def get_credential_manager():
    """获取全局凭证管理器实例"""
    return global_credential_manager


# 导出给其他模块使用
__all__ = ["app", "get_credential_manager"]


async def main():
    """异步主启动函数"""
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    # 日志系统现在直接使用环境变量，无需初始化
    # 从环境变量或配置获取端口和主机
    port = await get_server_port()
    host = await get_server_host()

    log.info("=" * 60)
    log.info("启动 GCLI2API")
    log.info("=" * 60)
    log.info(f"控制面板: http://127.0.0.1:{port}")
    log.info("=" * 60)
    log.info("API端点:")
    log.info(f"   OpenAI兼容: http://127.0.0.1:{port}/v1")
    log.info(f"   Gemini原生: http://127.0.0.1:{port}")
    log.info(f"   Antigravity (OpenAI格式): http://127.0.0.1:{port}/antigravity/v1")
    log.info(f"   Antigravity (claude格式): http://127.0.0.1:{port}/antigravity/v1")
    log.info(f"   Antigravity (Gemini格式): http://127.0.0.1:{port}/antigravity")
    log.info(f"   Antigravity (SD-WebUI格式): http://127.0.0.1:{port}/antigravity")
    log.info("=" * 60)
    log.info("统一网关 (自动故障转移):")
    log.info(f"   Gateway API: http://127.0.0.1:{port}/gateway/v1")
    log.info(f"   优先级: Antigravity > Copilot > Kiro Gateway")
    # 检查 Kiro Gateway 配置
    from src.unified_gateway_router import KIRO_GATEWAY_MODELS
    if KIRO_GATEWAY_MODELS:
        log.info(f"   Kiro Gateway 路由模型: {', '.join(KIRO_GATEWAY_MODELS)}")
    log.info("=" * 60)

    # 配置hypercorn
    config = Config()
    config.bind = [f"{host}:{port}"]
    config.accesslog = "-"
    config.errorlog = "-"
    config.loglevel = "INFO"
    config.use_colors = True

    # 设置请求体大小限制为100MB
    config.max_request_body_size = 100 * 1024 * 1024

    # 设置连接超时
    config.keep_alive_timeout = 300  # 5分钟
    config.read_timeout = 300  # 5分钟读取超时
    config.write_timeout = 300  # 5分钟写入超时

    # 增加启动超时时间以支持大量凭证的场景
    config.startup_timeout = 120  # 2分钟启动超时

    await serve(app, config)


if __name__ == "__main__":
    asyncio.run(main())
