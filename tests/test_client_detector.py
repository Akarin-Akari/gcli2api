"""
测试 ClientTypeDetector

验证客户端类型检测逻辑的正确性
"""

import unittest
from src.ide_compat import ClientType, ClientTypeDetector


class TestClientTypeDetector(unittest.TestCase):
    """测试 ClientTypeDetector"""

    def test_detect_claude_code(self):
        """测试 Claude Code 检测"""
        print("\n--- Testing Claude Code detection ---")

        test_cases = [
            "claude-code/1.0",
            "anthropic-claude/2.0",
            "Claude Desktop/1.5",
            "Anthropic API Client",
        ]

        for ua in test_cases:
            headers = {"user-agent": ua}
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.client_type, ClientType.CLAUDE_CODE, f"Failed for: {ua}")
            self.assertFalse(client_info.needs_sanitization, "Claude Code should not need sanitization")
            self.assertTrue(client_info.enable_cross_pool_fallback, "Claude Code should enable cross-pool fallback")
            print(f"  '{ua}' -> {client_info.client_type.value}")

    def test_detect_cursor(self):
        """测试 Cursor IDE 检测"""
        print("\n--- Testing Cursor IDE detection ---")

        test_cases = [
            "cursor/1.0",
            "Cursor-IDE/2.5.3",
            "Mozilla/5.0 Cursor",
        ]

        for ua in test_cases:
            headers = {"user-agent": ua}
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.client_type, ClientType.CURSOR, f"Failed for: {ua}")
            self.assertTrue(client_info.needs_sanitization, "Cursor should need sanitization")
            self.assertFalse(client_info.enable_cross_pool_fallback, "Cursor should not enable cross-pool fallback")
            print(f"  '{ua}' -> {client_info.client_type.value}")

    def test_detect_augment(self):
        """测试 Augment 检测"""
        print("\n--- Testing Augment detection ---")

        test_cases = [
            "augment/1.0",
            "bugment/2.0",
            "vscode-augment/1.5",
        ]

        for ua in test_cases:
            headers = {"user-agent": ua}
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.client_type, ClientType.AUGMENT, f"Failed for: {ua}")
            self.assertTrue(client_info.needs_sanitization, "Augment should need sanitization")
            print(f"  '{ua}' -> {client_info.client_type.value}")

    def test_detect_windsurf(self):
        """测试 Windsurf IDE 检测"""
        print("\n--- Testing Windsurf IDE detection ---")

        headers = {"user-agent": "windsurf/1.0"}
        client_info = ClientTypeDetector.detect(headers)
        self.assertEqual(client_info.client_type, ClientType.WINDSURF)
        self.assertTrue(client_info.needs_sanitization)
        print(f"  'windsurf/1.0' -> {client_info.client_type.value}")

    def test_detect_cline(self):
        """测试 Cline 检测"""
        print("\n--- Testing Cline detection ---")

        test_cases = [
            "cline/1.0",
            "claude-dev/2.0",
            "claudedev/1.5",
        ]

        for ua in test_cases:
            headers = {"user-agent": ua}
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.client_type, ClientType.CLINE, f"Failed for: {ua}")
            self.assertTrue(client_info.needs_sanitization)
            self.assertTrue(client_info.enable_cross_pool_fallback)
            print(f"  '{ua}' -> {client_info.client_type.value}")

    def test_detect_openai_api(self):
        """测试 OpenAI API 客户端检测"""
        print("\n--- Testing OpenAI API client detection ---")

        test_cases = [
            "openai-python/1.0",
            "python-requests/2.28",
            "node-fetch/3.0",
            "axios/1.0",
        ]

        for ua in test_cases:
            headers = {"user-agent": ua}
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.client_type, ClientType.OPENAI_API, f"Failed for: {ua}")
            # OpenAI API 客户端不需要净化 (标准 API 调用)
            self.assertFalse(client_info.needs_sanitization, f"OpenAI API should not need sanitization for: {ua}")
            self.assertTrue(client_info.enable_cross_pool_fallback)
            print(f"  '{ua}' -> {client_info.client_type.value}")

    def test_detect_unknown(self):
        """测试未知客户端检测"""
        print("\n--- Testing unknown client detection ---")

        test_cases = [
            "Mozilla/5.0",
            "Some Random Client",
            "",
        ]

        for ua in test_cases:
            headers = {"user-agent": ua}
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.client_type, ClientType.UNKNOWN, f"Failed for: {ua}")
            self.assertTrue(client_info.needs_sanitization, "Unknown client should need sanitization")
            self.assertFalse(client_info.enable_cross_pool_fallback, "Unknown client should not enable fallback")
            print(f"  '{ua}' -> {client_info.client_type.value}")

    def test_extract_scid(self):
        """测试 SCID 提取"""
        print("\n--- Testing SCID extraction ---")

        # 测试 X-AG-Conversation-Id
        headers = {
            "user-agent": "claude-code/1.0",
            "x-ag-conversation-id": "scid_1737100800_a1b2c3d4e5f6"
        }
        client_info = ClientTypeDetector.detect(headers)
        self.assertEqual(client_info.scid, "scid_1737100800_a1b2c3d4e5f6")
        print(f"  X-AG-Conversation-Id: {client_info.scid}")

        # 测试 X-Conversation-Id (fallback)
        headers = {
            "user-agent": "cursor/1.0",
            "x-conversation-id": "conv_123456"
        }
        client_info = ClientTypeDetector.detect(headers)
        self.assertEqual(client_info.scid, "conv_123456")
        print(f"  X-Conversation-Id: {client_info.scid}")

        # 测试无 SCID
        headers = {"user-agent": "augment/1.0"}
        client_info = ClientTypeDetector.detect(headers)
        self.assertIsNone(client_info.scid)
        print("  No SCID: None")

    def test_forwarded_user_agent(self):
        """测试转发的 User-Agent"""
        print("\n--- Testing forwarded User-Agent ---")

        headers = {
            "user-agent": "nginx/1.0",  # 网关的 UA
            "x-forwarded-user-agent": "cursor/1.0"  # 真实客户端的 UA
        }
        client_info = ClientTypeDetector.detect(headers)
        self.assertEqual(client_info.client_type, ClientType.CURSOR)
        self.assertEqual(client_info.user_agent, "cursor/1.0")
        print(f"  Forwarded UA: {client_info.user_agent} -> {client_info.client_type.value}")

    def test_version_extraction(self):
        """测试版本号提取"""
        print("\n--- Testing version extraction ---")

        test_cases = [
            ("claude-code/1.2.3", "1.2.3"),
            ("cursor-1.5.0", "1.5.0"),
            ("openai-python/2.0", "2.0"),
        ]

        for ua, expected_version in test_cases:
            headers = {"user-agent": ua}
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.version, expected_version, f"Failed for: {ua}")
            print(f"  '{ua}' -> version={client_info.version}")

    def test_is_augment_request(self):
        """测试 Augment 请求判断"""
        print("\n--- Testing Augment request detection ---")

        # 测试 Augment headers
        augment_headers = [
            {"x-augment-client": "true"},
            {"x-bugment-client": "true"},
            {"x-signature-version": "1.0"},
        ]

        for headers in augment_headers:
            headers["user-agent"] = "test"
            is_augment = ClientTypeDetector.is_augment_request(headers)
            self.assertTrue(is_augment, f"Failed for headers: {headers}")
            print(f"  {list(headers.keys())} -> is_augment={is_augment}")

        # 测试非 Augment headers
        normal_headers = {"user-agent": "cursor/1.0"}
        is_augment = ClientTypeDetector.is_augment_request(normal_headers)
        self.assertFalse(is_augment)
        print(f"  {list(normal_headers.keys())} -> is_augment={is_augment}")

    def test_case_insensitive_headers(self):
        """测试大小写不敏感的 header 处理"""
        print("\n--- Testing case-insensitive headers ---")

        # 测试不同大小写的 User-Agent
        test_cases = [
            {"User-Agent": "cursor/1.0"},
            {"user-agent": "cursor/1.0"},
            {"USER-AGENT": "cursor/1.0"},
        ]

        for headers in test_cases:
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.client_type, ClientType.CURSOR)
            print(f"  {list(headers.keys())[0]}: cursor/1.0 -> {client_info.client_type.value}")

        # 测试不同大小写的 SCID header
        test_cases = [
            {"X-AG-Conversation-Id": "scid_123"},
            {"x-ag-conversation-id": "scid_123"},
            {"X-Ag-Conversation-Id": "scid_123"},
        ]

        for headers in test_cases:
            headers["user-agent"] = "test"
            client_info = ClientTypeDetector.detect(headers)
            self.assertEqual(client_info.scid, "scid_123")
            print(f"  {list(headers.keys())[0]}: scid_123 -> {client_info.scid}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
