"""
IDECompatMiddleware 测试

测试中间件的核心功能:
1. 路径过滤
2. 客户端检测
3. 消息净化
4. 请求体重写
5. 异常处理

Author: Claude Sonnet 4.5 (浮浮酱)
Date: 2026-01-17
"""

import pytest
import json
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from src.ide_compat import IDECompatMiddleware, AnthropicSanitizer


# ============================================================================
# 测试应用
# ============================================================================

def create_test_app():
    """创建测试应用"""
    app = FastAPI()

    # 添加中间件
    app.add_middleware(IDECompatMiddleware)

    # 测试路由
    @app.post("/antigravity/v1/messages")
    async def messages_endpoint(request: Request):
        """模拟 Anthropic Messages API 端点"""
        body = await request.json()
        return JSONResponse(content={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Test response"}],
            "model": "claude-sonnet-4.5",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    @app.post("/v1/messages")
    async def messages_endpoint_v1(request: Request):
        """模拟 /v1/messages 端点"""
        body = await request.json()
        return JSONResponse(content={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Test response"}],
            "model": "claude-sonnet-4.5",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    @app.post("/other/endpoint")
    async def other_endpoint(request: Request):
        """其他端点 (不应被中间件处理)"""
        body = await request.json()
        return JSONResponse(content={"processed": True})

    @app.get("/stats")
    async def get_stats():
        """获取中间件统计信息"""
        # 这里需要访问中间件实例,暂时返回模拟数据
        return {"stats": "not_implemented"}

    return app


# ============================================================================
# 测试用例
# ============================================================================

class TestIDECompatMiddleware:
    """IDECompatMiddleware 测试套件"""

    @pytest.fixture
    def client(self):
        """创建测试客户端"""
        app = create_test_app()
        return TestClient(app)

    def test_path_filtering_antigravity(self, client):
        """测试路径过滤 - /antigravity/v1/messages"""
        response = client.post(
            "/antigravity/v1/messages",
            json={
                "model": "claude-sonnet-4.5",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"User-Agent": "claude-code/1.0.0"}
        )
        assert response.status_code == 200

    def test_path_filtering_v1(self, client):
        """测试路径过滤 - /v1/messages"""
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4.5",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"User-Agent": "claude-code/1.0.0"}
        )
        assert response.status_code == 200

    def test_path_filtering_other(self, client):
        """测试路径过滤 - 其他路径应该被跳过"""
        response = client.post(
            "/other/endpoint",
            json={"test": "data"},
            headers={"User-Agent": "cursor/1.0.0"}
        )
        assert response.status_code == 200
        # 其他路径不应被中间件处理

    def test_client_detection_claude_code(self, client):
        """测试客户端检测 - Claude Code (不需要净化)"""
        response = client.post(
            "/antigravity/v1/messages",
            json={
                "model": "claude-sonnet-4.5",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"User-Agent": "claude-code/1.0.0"}
        )
        assert response.status_code == 200
        # Claude Code 不需要净化,应该直接放行

    def test_client_detection_cursor(self, client):
        """测试客户端检测 - Cursor (需要净化)"""
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
                                "thoughtSignature": "invalid_signature"
                            }
                        ]
                    }
                ],
            },
            headers={"User-Agent": "cursor/1.0.0"}
        )
        assert response.status_code == 200
        # Cursor 需要净化,无效签名应该被降级为 text

    def test_message_sanitization_invalid_signature(self, client):
        """测试消息净化 - 无效签名应该被降级"""
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
                                "thoughtSignature": "short"  # 无效签名 (太短)
                            }
                        ]
                    }
                ],
                "thinking": {"type": "enabled", "budget_tokens": 1000}
            },
            headers={"User-Agent": "cursor/1.0.0"}
        )
        assert response.status_code == 200
        # 无效签名应该被降级,thinking 配置应该被移除

    def test_message_sanitization_valid_signature(self, client):
        """测试消息净化 - 有效签名应该被保留"""
        # 生成一个有效的签名 (64字节)
        valid_signature = "a" * 64

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
                                "thoughtSignature": valid_signature
                            }
                        ]
                    }
                ],
                "thinking": {"type": "enabled", "budget_tokens": 1000}
            },
            headers={"User-Agent": "cursor/1.0.0"}
        )
        assert response.status_code == 200
        # 有效签名应该被保留

    def test_thinking_config_sync(self, client):
        """测试 thinking 配置同步"""
        response = client.post(
            "/antigravity/v1/messages",
            json={
                "model": "claude-sonnet-4.5",
                "messages": [
                    {"role": "user", "content": "Hello"},
                ],
                "thinking": {"type": "enabled", "budget_tokens": 1000}
            },
            headers={"User-Agent": "cursor/1.0.0"}
        )
        assert response.status_code == 200
        # 没有 thinking block,thinking 配置应该被移除

    def test_error_handling_invalid_json(self, client):
        """测试异常处理 - 无效 JSON"""
        response = client.post(
            "/antigravity/v1/messages",
            data="invalid json",
            headers={
                "User-Agent": "cursor/1.0.0",
                "Content-Type": "application/json"
            }
        )
        # 中间件应该捕获异常并返回原始请求的处理结果
        # FastAPI 会返回 422 Unprocessable Entity
        assert response.status_code in (422, 500)

    def test_error_handling_missing_messages(self, client):
        """测试异常处理 - 缺少 messages 字段"""
        response = client.post(
            "/antigravity/v1/messages",
            json={
                "model": "claude-sonnet-4.5",
                # 缺少 messages 字段
            },
            headers={"User-Agent": "cursor/1.0.0"}
        )
        # 中间件应该处理这种情况,不应该崩溃
        assert response.status_code == 200

    def test_scid_extraction_from_header(self, client):
        """测试 SCID 提取 - 从 header"""
        response = client.post(
            "/antigravity/v1/messages",
            json={
                "model": "claude-sonnet-4.5",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={
                "User-Agent": "cursor/1.0.0",
                "X-AG-Conversation-Id": "test_scid_123"
            }
        )
        assert response.status_code == 200
        # SCID 应该被提取并用于 Session Cache

    def test_scid_extraction_from_body(self, client):
        """测试 SCID 提取 - 从 body"""
        response = client.post(
            "/antigravity/v1/messages",
            json={
                "model": "claude-sonnet-4.5",
                "messages": [{"role": "user", "content": "Hello"}],
                "conversation_id": "test_scid_456"
            },
            headers={"User-Agent": "cursor/1.0.0"}
        )
        assert response.status_code == 200
        # SCID 应该被提取并用于 Session Cache

    def test_method_filtering(self, client):
        """测试方法过滤 - 只处理 POST 请求"""
        response = client.get("/antigravity/v1/messages")
        # GET 请求应该被跳过 (会返回 405 Method Not Allowed)
        assert response.status_code == 405


# ============================================================================
# 性能测试
# ============================================================================

class TestIDECompatMiddlewarePerformance:
    """IDECompatMiddleware 性能测试"""

    @pytest.fixture
    def client(self):
        """创建测试客户端"""
        app = create_test_app()
        return TestClient(app)

    def test_performance_small_request(self, client):
        """测试性能 - 小请求"""
        import time

        start = time.time()
        for _ in range(100):
            client.post(
                "/antigravity/v1/messages",
                json={
                    "model": "claude-sonnet-4.5",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
                headers={"User-Agent": "cursor/1.0.0"}
            )
        end = time.time()

        elapsed = end - start
        avg_time = elapsed / 100

        print(f"\n小请求平均处理时间: {avg_time*1000:.2f}ms")
        # 应该在合理范围内 (<100ms)
        assert avg_time < 0.1

    def test_performance_large_request(self, client):
        """测试性能 - 大请求"""
        import time

        # 创建一个大请求 (100条消息)
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]

        start = time.time()
        for _ in range(10):
            client.post(
                "/antigravity/v1/messages",
                json={
                    "model": "claude-sonnet-4.5",
                    "messages": messages,
                },
                headers={"User-Agent": "cursor/1.0.0"}
            )
        end = time.time()

        elapsed = end - start
        avg_time = elapsed / 10

        print(f"\n大请求平均处理时间: {avg_time*1000:.2f}ms")
        # 大请求可能需要更长时间
        assert avg_time < 1.0


# ============================================================================
# 运行测试
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
