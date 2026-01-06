# -*- coding: utf-8 -*-
"""
补丁脚本 Step 2：在 router 中添加 User-Agent 检测逻辑
同时打印 User-Agent 用于调试
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROUTER_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_router.py"

# 1. 在 chat_completions 函数开头添加 User-Agent 检测
ROUTER_UA_DETECTION = {
    "name": "添加 User-Agent 检测和来源判断",
    "old": '''@router.post("/antigravity/v1/chat/completions")
async def chat_completions(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """
    处理 OpenAI 格式的聊天完成请求，转换为 Antigravity API
    """
    # 获取原始请求数据
    try:
        raw_data = await request.json()''',
    "new": '''@router.post("/antigravity/v1/chat/completions")
async def chat_completions(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """
    处理 OpenAI 格式的聊天完成请求，转换为 Antigravity API
    """
    # ✅ 检测请求来源（User-Agent）以决定降级策略
    user_agent = request.headers.get("user-agent", "").lower()
    log.info(f"[ANTIGRAVITY] Request User-Agent: {user_agent}")

    # 判断是否是 Claude Code 请求（需要跨池降级）
    # Claude Code 的 User-Agent 通常包含 "claude" 或 "anthropic"
    # Cursor 的 User-Agent 通常包含 "cursor"
    # 如果无法判断，默认不启用跨池降级（更安全）
    is_claude_code = any(keyword in user_agent for keyword in ["claude", "anthropic"])
    is_cursor = "cursor" in user_agent

    # Claude Code 启用跨池降级，Cursor 不启用
    enable_cross_pool_fallback = is_claude_code and not is_cursor

    if enable_cross_pool_fallback:
        log.info("[ANTIGRAVITY] Detected Claude Code request - cross-pool fallback ENABLED")
    else:
        log.debug(f"[ANTIGRAVITY] Detected {'Cursor' if is_cursor else 'unknown'} request - cross-pool fallback DISABLED")

    # 获取原始请求数据
    try:
        raw_data = await request.json()'''
}

# 2. 修改流式请求调用（抗截断版）
ROUTER_STREAM_ANTI_TRUNCATION = {
    "name": "流式请求（抗截断）传递 enable_cross_pool_fallback",
    "old": '''                    # 包装请求函数以适配抗截断处理器
                    async def antigravity_request_func(payload):
                        resources, cred_name, cred_data = await send_antigravity_request_stream(
                            payload, cred_mgr
                        )''',
    "new": '''                    # 包装请求函数以适配抗截断处理器
                    async def antigravity_request_func(payload):
                        resources, cred_name, cred_data = await send_antigravity_request_stream(
                            payload, cred_mgr, enable_cross_pool_fallback=enable_cross_pool_fallback
                        )'''
}

# 3. 修改流式请求调用（普通版）
ROUTER_STREAM_NORMAL = {
    "name": "流式请求（普通）传递 enable_cross_pool_fallback",
    "old": '''                # 流式请求（无抗截断）
                resources, cred_name, cred_data = await send_antigravity_request_stream(
                    request_body, cred_mgr
                )''',
    "new": '''                # 流式请求（无抗截断）
                resources, cred_name, cred_data = await send_antigravity_request_stream(
                    request_body, cred_mgr, enable_cross_pool_fallback=enable_cross_pool_fallback
                )'''
}

def main():
    print(f"[READ] 读取文件: {ROUTER_FILE}")

    with open(ROUTER_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 备份
    backup_file = ROUTER_FILE + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy(ROUTER_FILE, backup_file)
    print(f"[BACKUP] 备份到: {backup_file}")

    applied = 0
    patches = [
        ROUTER_UA_DETECTION,
        ROUTER_STREAM_ANTI_TRUNCATION,
        ROUTER_STREAM_NORMAL,
    ]

    for patch in patches:
        if patch["old"] in content:
            content = content.replace(patch["old"], patch["new"], 1)
            print(f"   [OK] {patch['name']}")
            applied += 1
        else:
            print(f"   [SKIP] {patch['name']} - 目标未找到")

    # 写入
    with open(ROUTER_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"\n[SUCCESS] 共应用 {applied} 个补丁!")
    print(f"\n[INFO] 现在请重启服务，然后用 Cursor 和 Claude Code 分别发送请求")
    print(f"       查看日志中的 User-Agent 来确认区分逻辑是否正确")
    print(f"\n[NEXT] 如果 User-Agent 检测不准确，可以根据实际情况调整关键词")

if __name__ == "__main__":
    main()
