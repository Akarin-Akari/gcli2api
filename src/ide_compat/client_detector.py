"""
Client Type Detector - 客户端类型检测器

根据 HTTP Headers 检测请求来源的客户端类型,用于:
- 决定是否需要消息净化
- 启用/禁用跨池 fallback
- 提取 Server Conversation ID (SCID)
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass

from log import log


class ClientType(Enum):
    """客户端类型枚举"""
    CLAUDE_CODE = "claude_code"      # Claude Code CLI
    CURSOR = "cursor"                 # Cursor IDE
    AUGMENT = "augment"               # Augment/Bugment
    WINDSURF = "windsurf"             # Windsurf IDE
    CLINE = "cline"                   # Cline VSCode 扩展
    CONTINUE_DEV = "continue_dev"     # Continue.dev
    AIDER = "aider"                   # Aider
    ZED = "zed"                       # Zed 编辑器
    COPILOT = "copilot"               # GitHub Copilot
    OPENAI_API = "openai_api"         # 标准 OpenAI API 调用
    UNKNOWN = "unknown"               # 未知客户端


@dataclass
class ClientInfo:
    """客户端信息"""
    client_type: ClientType
    user_agent: str
    needs_sanitization: bool          # 是否需要消息净化
    enable_cross_pool_fallback: bool  # 是否启用跨池 fallback
    scid: Optional[str] = None        # Server Conversation ID
    version: str = ""                 # 客户端版本
    display_name: str = ""            # 显示名称


class ClientTypeDetector:
    """
    客户端类型检测器

    根据 HTTP Headers 检测请求来源:
    - User-Agent
    - X-AG-Conversation-Id (SCID)
    - X-Client-Type (自定义 header)
    - X-Forwarded-User-Agent (网关转发场景)
    """

    # User-Agent 匹配规则 (按优先级排序)
    # 格式: (client_type, patterns, version_regex, display_name)
    UA_PATTERNS = [
        # 高优先级：专用 AI 编程工具（精确匹配）
        (ClientType.CURSOR, [r"cursor/", r"cursor-"], r"cursor[/-]?(\d+(?:\.\d+)*)", "Cursor IDE"),
        (ClientType.CLINE, [r"cline/", r"cline-", r"claude-dev", r"claudedev"], r"cline[/-]?(\d+(?:\.\d+)*)", "Cline"),
        (ClientType.CLAUDE_CODE, [r"claude-code/", r"claude-code-", r"anthropic-claude"], r"claude-code[/-]?(\d+(?:\.\d+)*)", "Claude Code"),
        (ClientType.WINDSURF, [r"windsurf/", r"windsurf-"], r"windsurf[/-]?(\d+(?:\.\d+)*)", "Windsurf IDE"),
        (ClientType.AIDER, [r"aider/", r"aider-"], r"aider[/-]?(\d+(?:\.\d+)*)", "Aider"),
        (ClientType.CONTINUE_DEV, [r"continue/", r"continue-dev"], r"continue[/-]?(\d+(?:\.\d+)*)", "Continue.dev"),
        (ClientType.ZED, [r"zed/", r"zed-editor"], r"zed[/-]?(\d+(?:\.\d+)*)", "Zed Editor"),
        (ClientType.COPILOT, [r"github-copilot", r"copilot/"], r"copilot[/-]?(\d+(?:\.\d+)*)", "GitHub Copilot"),

        # 中优先级：通用关键词（可能误匹配，放在后面）
        (ClientType.CURSOR, [r"cursor"], None, "Cursor IDE"),
        (ClientType.CLAUDE_CODE, [r"claude", r"anthropic"], None, "Claude Code"),
        (ClientType.AUGMENT, [r"augment", r"bugment", r"vscode"], None, "Augment"),

        # 低优先级：SDK 和通用客户端
        (ClientType.OPENAI_API, [r"openai-python/", r"openai-node/", r"openai/"], r"(?:openai-python|openai-node|openai)[/-](\d+(?:\.\d+)*)", "OpenAI SDK"),
        (ClientType.OPENAI_API, [r"python-requests/", r"httpx/", r"aiohttp/"], r"(?:python-requests|httpx|aiohttp)[/-](\d+(?:\.\d+)*)", "HTTP Client"),
        (ClientType.OPENAI_API, [r"node-fetch/", r"axios/", r"got/"], r"(?:node-fetch|axios|got)[/-](\d+(?:\.\d+)*)", "Node.js Client"),
    ]

    # 需要消息净化的客户端类型 (IDE 客户端可能变形 thinking 文本)
    SANITIZATION_REQUIRED = {
        ClientType.CURSOR,
        ClientType.AUGMENT,
        ClientType.WINDSURF,
        ClientType.CLINE,
        ClientType.CONTINUE_DEV,
        ClientType.AIDER,
        ClientType.ZED,
        ClientType.COPILOT,
        ClientType.UNKNOWN,  # 未知客户端默认需要净化
    }

    # 启用跨池 fallback 的客户端类型
    CROSS_POOL_FALLBACK_ENABLED = {
        ClientType.CLAUDE_CODE,
        ClientType.CLINE,
        ClientType.CONTINUE_DEV,
        ClientType.AIDER,
        ClientType.OPENAI_API,
    }

    @classmethod
    def detect(cls, headers: Dict[str, str]) -> ClientInfo:
        """
        从 HTTP Headers 检测客户端类型

        Args:
            headers: HTTP 请求头 (大小写不敏感)

        Returns:
            ClientInfo 对象
        """
        # 1. 提取 User-Agent (优先使用转发的 UA)
        user_agent = cls._extract_user_agent(headers)

        # 2. 检测客户端类型和版本
        client_type, version, display_name = cls._match_user_agent(user_agent)

        # 3. 提取 SCID
        scid = cls._extract_scid(headers)

        # 4. 判断是否需要净化
        needs_sanitization = cls.needs_sanitization(client_type)

        # 5. 判断是否启用跨池 fallback
        enable_cross_pool_fallback = client_type in cls.CROSS_POOL_FALLBACK_ENABLED

        # 6. 构造 ClientInfo
        client_info = ClientInfo(
            client_type=client_type,
            user_agent=user_agent,
            needs_sanitization=needs_sanitization,
            enable_cross_pool_fallback=enable_cross_pool_fallback,
            scid=scid,
            version=version,
            display_name=display_name,
        )

        # 7. 记录日志
        log.info(
            f"[CLIENT_DETECTOR] Detected client: {display_name} "
            f"(type={client_type.value}, version={version or 'unknown'}, "
            f"sanitize={needs_sanitization}, fallback={enable_cross_pool_fallback}, "
            f"scid={scid[:16] + '...' if scid else 'None'})"
        )

        return client_info

    @classmethod
    def _extract_user_agent(cls, headers: Dict[str, str]) -> str:
        """
        提取 User-Agent (大小写不敏感)

        优先级:
        1. X-Forwarded-User-Agent (网关转发场景)
        2. User-Agent
        """
        # 转换为小写键的字典以支持大小写不敏感查找
        headers_lower = {k.lower(): v for k, v in headers.items()}

        # 优先使用转发的 UA
        forwarded_ua = headers_lower.get("x-forwarded-user-agent", "")
        if forwarded_ua:
            log.debug(f"[CLIENT_DETECTOR] Using forwarded User-Agent: {forwarded_ua}")
            return forwarded_ua

        # 使用标准 UA
        user_agent = headers_lower.get("user-agent", "")
        return user_agent

    @classmethod
    def _match_user_agent(cls, user_agent: str) -> tuple[ClientType, str, str]:
        """
        匹配 User-Agent 并提取版本号

        Args:
            user_agent: HTTP User-Agent 头

        Returns:
            (client_type, version, display_name)
        """
        if not user_agent:
            return ClientType.UNKNOWN, "", "Unknown Client"

        user_agent_lower = user_agent.lower()

        # 按优先级顺序匹配
        for client_type, patterns, version_regex, display_name in cls.UA_PATTERNS:
            for pattern in patterns:
                if re.search(pattern, user_agent_lower, re.IGNORECASE):
                    # 提取版本号
                    version = cls._extract_version(user_agent, version_regex) if version_regex else ""
                    log.debug(
                        f"[CLIENT_DETECTOR] Matched pattern '{pattern}': "
                        f"{display_name} (type={client_type.value}, version={version or 'unknown'})"
                    )
                    return client_type, version, display_name

        return ClientType.UNKNOWN, "", "Unknown Client"

    @classmethod
    def _extract_version(cls, user_agent: str, version_regex: str) -> str:
        """
        从 User-Agent 中提取版本号

        Args:
            user_agent: HTTP User-Agent 头
            version_regex: 版本号正则表达式

        Returns:
            版本号字符串，如果未找到则返回空字符串
        """
        try:
            match = re.search(version_regex, user_agent, re.IGNORECASE)
            if match:
                return match.group(1)
        except Exception as e:
            log.warning(f"[CLIENT_DETECTOR] Failed to extract version: {e}")

        return ""

    @classmethod
    def _extract_scid(cls, headers: Dict[str, str]) -> Optional[str]:
        """
        提取 Server Conversation ID

        优先级:
        1. X-AG-Conversation-Id header
        2. X-Conversation-Id header
        3. None (需要从 request body 中提取,由外部处理)

        Args:
            headers: HTTP 请求头 (大小写不敏感)

        Returns:
            SCID 字符串或 None
        """
        # 转换为小写键的字典
        headers_lower = {k.lower(): v for k, v in headers.items()}

        # 优先级 1: X-AG-Conversation-Id
        scid = headers_lower.get("x-ag-conversation-id", "").strip()
        if scid:
            log.debug(f"[CLIENT_DETECTOR] SCID from X-AG-Conversation-Id: {scid[:16]}...")
            return scid

        # 优先级 2: X-Conversation-Id
        scid = headers_lower.get("x-conversation-id", "").strip()
        if scid:
            log.debug(f"[CLIENT_DETECTOR] SCID from X-Conversation-Id: {scid[:16]}...")
            return scid

        # 未找到 SCID
        return None

    @classmethod
    def needs_sanitization(cls, client_type: ClientType) -> bool:
        """
        判断客户端是否需要消息净化

        Claude Code: 不需要 (原生支持 Anthropic 协议)
        IDE 客户端: 需要 (可能变形 thinking 文本)
        Unknown: 需要 (保守策略)

        Args:
            client_type: 客户端类型

        Returns:
            是否需要净化
        """
        return client_type in cls.SANITIZATION_REQUIRED

    @classmethod
    def is_augment_request(cls, headers: Dict[str, str]) -> bool:
        """
        判断是否为 Augment/Bugment 请求

        通过检测特定的 header 来判断:
        - X-Augment-Client
        - X-Bugment-Client
        - X-Augment-Request
        - X-Bugment-Request
        - X-Signature-* (签名相关 header)

        Args:
            headers: HTTP 请求头

        Returns:
            是否为 Augment 请求
        """
        headers_lower = {k.lower() for k in headers.keys()}

        augment_headers = {
            "x-augment-client",
            "x-bugment-client",
            "x-augment-request",
            "x-bugment-request",
            "x-signature-version",
            "x-signature-vector",
            "x-signature-signature",
        }

        return bool(headers_lower & augment_headers)
