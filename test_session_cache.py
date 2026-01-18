#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Session 缓存功能测试脚本

测试 Layer 3 (Session级别缓存) 的功能实现。

Author: Claude Sonnet 4.5
Date: 2026-01-17
"""

import sys
import os
import io

# 设置标准输出编码为 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加 src 目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from signature_cache import (
    SignatureCache,
    generate_session_fingerprint,
    cache_session_signature,
    get_session_signature,
    get_session_signature_with_text,
    reset_signature_cache
)


def test_session_fingerprint_generation():
    """测试会话指纹生成"""
    print("\n=== 测试 1: 会话指纹生成 ===")

    # 测试用户消息
    messages = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello, how are you?"}
    ]

    fingerprint = generate_session_fingerprint(messages)
    print(f"生成的指纹: {fingerprint}")
    assert len(fingerprint) == 16, "指纹长度应为 16"

    # 测试相同消息生成相同指纹
    fingerprint2 = generate_session_fingerprint(messages)
    assert fingerprint == fingerprint2, "相同消息应生成相同指纹"
    print("✓ 相同消息生成相同指纹")

    # 测试不同消息生成不同指纹
    messages2 = [
        {"role": "user", "content": "Different message"}
    ]
    fingerprint3 = generate_session_fingerprint(messages2)
    assert fingerprint != fingerprint3, "不同消息应生成不同指纹"
    print("✓ 不同消息生成不同指纹")

    # 测试空消息
    fingerprint4 = generate_session_fingerprint([])
    assert fingerprint4 == "", "空消息应返回空字符串"
    print("✓ 空消息处理正确")

    # 测试多模态内容
    messages3 = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this image"},
                {"type": "image", "source": "..."}
            ]
        }
    ]
    fingerprint5 = generate_session_fingerprint(messages3)
    assert len(fingerprint5) == 16, "多模态消息应正常生成指纹"
    print("✓ 多模态内容处理正确")

    print("✓ 会话指纹生成测试通过")


def test_session_cache_basic():
    """测试 Session 缓存基本功能"""
    print("\n=== 测试 2: Session 缓存基本功能 ===")

    # 重置缓存
    reset_signature_cache()

    session_id = "test_session_001"
    signature = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
    thinking_text = "Let me think about this problem..."

    # 测试缓存写入
    result = cache_session_signature(session_id, signature, thinking_text)
    assert result is True, "缓存写入应成功"
    print("✓ Session 签名缓存写入成功")

    # 测试缓存读取
    cached_sig = get_session_signature(session_id)
    assert cached_sig == signature, "缓存读取应返回相同的签名"
    print("✓ Session 签名缓存读取成功")

    # 测试带文本的缓存读取
    result = get_session_signature_with_text(session_id)
    assert result is not None, "应返回元组"
    cached_sig2, cached_text = result
    assert cached_sig2 == signature, "签名应匹配"
    assert cached_text == thinking_text, "thinking 文本应匹配"
    print("✓ Session 签名缓存（含文本）读取成功")

    # 测试不存在的 session
    cached_sig3 = get_session_signature("non_existent_session")
    assert cached_sig3 is None, "不存在的 session 应返回 None"
    print("✓ 不存在的 session 处理正确")

    print("✓ Session 缓存基本功能测试通过")


def test_session_cache_class_methods():
    """测试 SignatureCache 类的 Session 方法"""
    print("\n=== 测试 3: SignatureCache 类方法 ===")

    cache = SignatureCache(max_size=100, ttl_seconds=3600)

    session_id = "test_session_002"
    signature = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
    thinking_text = "Another thinking block..."

    # 测试类方法
    result = cache.cache_session_signature(session_id, signature, thinking_text)
    assert result is True, "类方法缓存写入应成功"
    print("✓ 类方法缓存写入成功")

    cached_sig = cache.get_session_signature(session_id)
    assert cached_sig == signature, "类方法缓存读取应成功"
    print("✓ 类方法缓存读取成功")

    result = cache.get_session_signature_with_text(session_id)
    assert result is not None, "类方法应返回元组"
    print("✓ 类方法缓存（含文本）读取成功")

    print("✓ SignatureCache 类方法测试通过")


def test_session_cache_validation():
    """测试 Session 缓存验证逻辑"""
    print("\n=== 测试 4: Session 缓存验证 ===")

    reset_signature_cache()

    # 测试空 session_id
    result = cache_session_signature("", "valid_signature")
    assert result is False, "空 session_id 应拒绝缓存"
    print("✓ 空 session_id 验证正确")

    # 测试空 signature
    result = cache_session_signature("valid_session", "")
    assert result is False, "空 signature 应拒绝缓存"
    print("✓ 空 signature 验证正确")

    # 测试无效的 signature 格式（太短）
    result = cache_session_signature("valid_session", "short")
    assert result is False, "过短的 signature 应拒绝缓存"
    print("✓ 无效 signature 格式验证正确")

    # 测试有效的 signature
    valid_sig = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
    result = cache_session_signature("valid_session", valid_sig)
    assert result is True, "有效的 signature 应成功缓存"
    print("✓ 有效 signature 验证正确")

    print("✓ Session 缓存验证测试通过")


def test_integration_with_existing_layers():
    """测试与现有缓存层的集成"""
    print("\n=== 测试 5: 与现有缓存层集成 ===")

    reset_signature_cache()
    cache = SignatureCache()

    # Layer 1: 工具ID缓存
    tool_id = "tool_call_001"
    signature1 = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
    cache.cache_tool_signature(tool_id, signature1)

    # Layer 2: thinking text hash 缓存
    thinking_text = "Let me analyze this..."
    signature2 = "FqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
    cache.set(thinking_text, signature2)

    # Layer 3: Session 缓存
    session_id = "session_003"
    signature3 = "GqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
    cache.cache_session_signature(session_id, signature3, thinking_text)

    # 验证所有层都能独立工作
    assert cache.get_tool_signature(tool_id) == signature1, "Layer 1 应正常工作"
    print("✓ Layer 1 (工具ID缓存) 正常工作")

    assert cache.get(thinking_text) == signature2, "Layer 2 应正常工作"
    print("✓ Layer 2 (thinking hash 缓存) 正常工作")

    assert cache.get_session_signature(session_id) == signature3, "Layer 3 应正常工作"
    print("✓ Layer 3 (Session 缓存) 正常工作")

    print("✓ 三层缓存集成测试通过")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Session 缓存功能测试")
    print("=" * 60)

    try:
        test_session_fingerprint_generation()
        test_session_cache_basic()
        test_session_cache_class_methods()
        test_session_cache_validation()
        test_integration_with_existing_layers()

        print("\n" + "=" * 60)
        print("✓ 所有测试通过！")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n✗ 测试出错: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
