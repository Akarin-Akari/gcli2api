"""
IDECompatMiddleware 集成示例

展示如何在 web.py 中集成 IDECompatMiddleware

Author: Claude Sonnet 4.5 (浮浮酱)
Date: 2026-01-17
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 导入 IDE 兼容中间件
from src.ide_compat import IDECompatMiddleware

# 导入路由
from src.antigravity_router import router as antigravity_router
from src.antigravity_anthropic_router import router as antigravity_anthropic_router


# ============================================================================
# 方法 1: 基本集成 (推荐)
# ============================================================================

def basic_integration():
    """基本集成 - 使用默认配置"""
    app = FastAPI(
        title="GCLI2API",
        description="Gemini API proxy with OpenAI compatibility",
        version="2.0.0",
    )

    # 1. CORS 中间件 (最外层)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. IDE 兼容中间件 (在 CORS 之后,路由之前)
    app.add_middleware(IDECompatMiddleware)

    # 3. 挂载路由
    app.include_router(antigravity_router, prefix="", tags=["Antigravity API"])
    app.include_router(antigravity_anthropic_router, prefix="", tags=["Antigravity Anthropic Messages"])

    return app


# ============================================================================
# 方法 2: 自定义配置
# ============================================================================

def custom_integration():
    """自定义配置 - 使用自定义 sanitizer"""
    from src.ide_compat import AnthropicSanitizer

    app = FastAPI(
        title="GCLI2API",
        description="Gemini API proxy with OpenAI compatibility",
        version="2.0.0",
    )

    # 1. CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. 创建自定义 sanitizer
    sanitizer = AnthropicSanitizer()

    # 3. IDE 兼容中间件 (使用自定义 sanitizer)
    app.add_middleware(
        IDECompatMiddleware,
        sanitizer=sanitizer,
    )

    # 4. 挂载路由
    app.include_router(antigravity_router, prefix="", tags=["Antigravity API"])
    app.include_router(antigravity_anthropic_router, prefix="", tags=["Antigravity Anthropic Messages"])

    return app


# ============================================================================
# 方法 3: 带统计信息的集成
# ============================================================================

def integration_with_stats():
    """带统计信息的集成 - 添加统计端点"""
    app = FastAPI(
        title="GCLI2API",
        description="Gemini API proxy with OpenAI compatibility",
        version="2.0.0",
    )

    # 1. CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. 创建中间件实例 (保存到全局变量以便访问统计信息)
    global ide_compat_middleware
    ide_compat_middleware = IDECompatMiddleware(app)

    # 3. 添加中间件
    app.add_middleware(IDECompatMiddleware)

    # 4. 添加统计端点
    @app.get("/ide_compat/stats")
    async def get_ide_compat_stats():
        """获取 IDE 兼容中间件统计信息"""
        return ide_compat_middleware.get_stats()

    @app.post("/ide_compat/stats/reset")
    async def reset_ide_compat_stats():
        """重置 IDE 兼容中间件统计信息"""
        ide_compat_middleware.reset_stats()
        return {"status": "ok", "message": "Stats reset successfully"}

    # 5. 挂载路由
    app.include_router(antigravity_router, prefix="", tags=["Antigravity API"])
    app.include_router(antigravity_anthropic_router, prefix="", tags=["Antigravity Anthropic Messages"])

    return app


# ============================================================================
# 方法 4: 条件启用 (通过环境变量)
# ============================================================================

def conditional_integration():
    """条件启用 - 通过环境变量控制是否启用中间件"""
    import os

    app = FastAPI(
        title="GCLI2API",
        description="Gemini API proxy with OpenAI compatibility",
        version="2.0.0",
    )

    # 1. CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. 条件启用 IDE 兼容中间件
    enable_ide_compat = os.getenv("ENABLE_IDE_COMPAT", "true").lower() == "true"

    if enable_ide_compat:
        app.add_middleware(IDECompatMiddleware)
        print("[STARTUP] IDE 兼容中间件已启用")
    else:
        print("[STARTUP] IDE 兼容中间件已禁用")

    # 3. 挂载路由
    app.include_router(antigravity_router, prefix="", tags=["Antigravity API"])
    app.include_router(antigravity_anthropic_router, prefix="", tags=["Antigravity Anthropic Messages"])

    return app


# ============================================================================
# 实际使用示例 (web.py)
# ============================================================================

def create_production_app():
    """
    生产环境应用 - 完整配置

    这是推荐的生产环境配置,包含:
    1. CORS 中间件
    2. IDE 兼容中间件
    3. 所有路由
    4. 统计端点
    """
    from contextlib import asynccontextmanager
    from log import log

    # 全局中间件实例
    global ide_compat_middleware

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期管理"""
        log.info("启动 GCLI2API 主服务")

        # 初始化配置...
        # (省略其他初始化逻辑)

        yield

        log.info("GCLI2API 主服务已停止")

    # 创建 FastAPI 应用
    app = FastAPI(
        title="GCLI2API",
        description="Gemini API proxy with OpenAI compatibility",
        version="2.0.0",
        lifespan=lifespan,
    )

    # ==================== 中间件配置 ====================

    # 1. CORS 中间件 (最外层)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. IDE 兼容中间件 (在 CORS 之后,路由之前)
    ide_compat_middleware = IDECompatMiddleware(app)
    app.add_middleware(IDECompatMiddleware)

    log.info("[STARTUP] IDE 兼容中间件已启用")

    # ==================== 路由配置 ====================

    # Antigravity 路由
    app.include_router(antigravity_router, prefix="", tags=["Antigravity API"])

    # Antigravity Anthropic Messages 路由
    app.include_router(antigravity_anthropic_router, prefix="", tags=["Antigravity Anthropic Messages"])

    # ==================== 统计端点 ====================

    @app.get("/ide_compat/stats")
    async def get_ide_compat_stats():
        """获取 IDE 兼容中间件统计信息"""
        return {
            "middleware": "IDECompatMiddleware",
            "stats": ide_compat_middleware.get_stats(),
        }

    @app.post("/ide_compat/stats/reset")
    async def reset_ide_compat_stats():
        """重置 IDE 兼容中间件统计信息"""
        ide_compat_middleware.reset_stats()
        return {
            "status": "ok",
            "message": "IDE compat middleware stats reset successfully"
        }

    # ==================== 健康检查 ====================

    @app.get("/health")
    async def health_check():
        """健康检查端点"""
        return {
            "status": "ok",
            "service": "GCLI2API",
            "version": "2.0.0",
            "middleware": {
                "ide_compat": "enabled",
            }
        }

    return app


# ============================================================================
# 测试客户端
# ============================================================================

def test_middleware():
    """测试中间件功能"""
    from fastapi.testclient import TestClient

    # 创建应用
    app = basic_integration()

    # 创建测试客户端
    client = TestClient(app)

    # 测试 1: Claude Code (不应被净化)
    print("\n测试 1: Claude Code 客户端")
    response = client.post(
        "/antigravity/v1/messages",
        json={
            "model": "claude-sonnet-4.5",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        headers={"User-Agent": "claude-code/1.0.0"}
    )
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.json()}")

    # 测试 2: Cursor (应被净化)
    print("\n测试 2: Cursor 客户端 (带无效签名)")
    response = client.post(
        "/antigravity/v1/messages",
        json={
            "model": "claude-sonnet-4.5",
            "messages": [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Test thinking",
                            "thoughtSignature": "invalid"  # 无效签名
                        }
                    ]
                }
            ],
            "thinking": {"type": "enabled", "budget_tokens": 1000}
        },
        headers={"User-Agent": "cursor/1.0.0"}
    )
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.json()}")


if __name__ == "__main__":
    # 运行测试
    test_middleware()
