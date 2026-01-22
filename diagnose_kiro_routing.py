#!/usr/bin/env python3
"""
诊断 Kiro Gateway 路由问题

检查：
1. 配置是否正确加载
2. 路由规则是否正确
3. 后端是否启用
4. 模型支持检查
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

print("=" * 70)
print("Kiro Gateway 路由诊断工具")
print("=" * 70)

# 1. 检查配置加载
print("\n[1] 检查配置加载...")
try:
    from src.gateway.config_loader import get_model_routing_rule, load_model_routing_config
    from src.gateway.config import BACKENDS
    from src.unified_gateway_router import is_kiro_gateway_supported
    
    rules = load_model_routing_config()
    print(f"  [OK] 成功加载 {len(rules)} 个路由规则")
    
    sonnet_rule = rules.get("claude-sonnet-4.5")
    if sonnet_rule:
        print(f"  [OK] 找到 claude-sonnet-4.5 规则")
        print(f"       - enabled: {sonnet_rule.enabled}")
        print(f"       - backend_chain 长度: {len(sonnet_rule.backend_chain)}")
        if sonnet_rule.backend_chain:
            first = sonnet_rule.backend_chain[0]
            print(f"       - 第一个后端: {first.backend}, 目标模型: {first.model}")
    else:
        print(f"  [ERROR] 未找到 claude-sonnet-4.5 规则")
        sys.exit(1)
        
except Exception as e:
    print(f"  [ERROR] 配置加载失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 2. 检查后端配置
print("\n[2] 检查后端配置...")
kiro_config = BACKENDS.get("kiro-gateway", {})
print(f"  kiro-gateway 配置:")
print(f"       - enabled: {kiro_config.get('enabled', False)}")
print(f"       - base_url: {kiro_config.get('base_url', 'N/A')}")
print(f"       - priority: {kiro_config.get('priority', 'N/A')}")

if not kiro_config.get('enabled', False):
    print(f"  [WARNING] kiro-gateway 未启用！")

# 3. 检查模型支持
print("\n[3] 检查模型支持...")
test_models = [
    "claude-sonnet-4.5",
    "claude-sonnet-4.5-thinking",
    "claude-sonnet-4-5",
]

for model in test_models:
    supported = is_kiro_gateway_supported(model)
    print(f"  {model}: {'[OK] 支持' if supported else '[ERROR] 不支持'}")

# 4. 检查路由规则中的后端支持
print("\n[4] 检查路由规则中的后端支持...")
if sonnet_rule and sonnet_rule.backend_chain:
    from src.unified_gateway_router import is_antigravity_supported, is_anyrouter_supported
    
    for i, entry in enumerate(sonnet_rule.backend_chain):
        backend_key = entry.backend
        target_model = entry.model
        backend_config = BACKENDS.get(backend_key, {})
        enabled = backend_config.get("enabled", True)
        
        print(f"\n  后端 {i+1}: {backend_key}")
        print(f"       - enabled: {enabled}")
        print(f"       - target_model: {target_model}")
        
        if not enabled:
            print(f"       [WARNING] 后端未启用")
            continue
        
        # 检查支持
        if backend_key == "antigravity":
            supported = is_antigravity_supported(target_model)
            print(f"       - 支持检查: {'[OK] 支持' if supported else '[ERROR] 不支持'}")
        elif backend_key == "kiro-gateway":
            supported = is_kiro_gateway_supported(target_model)
            print(f"       - 支持检查: {'[OK] 支持' if supported else '[ERROR] 不支持'}")
            if not supported:
                print(f"       [ERROR] Kiro Gateway 不支持 {target_model}！")
        elif backend_key == "anyrouter":
            supported = is_anyrouter_supported(target_model)
            print(f"       - 支持检查: {'[OK] 支持' if supported else '[ERROR] 不支持'}")

# 5. 模拟路由决策
print("\n[5] 模拟路由决策...")
try:
    from src.unified_gateway_router import get_backend_and_model_for_routing
    
    for model in ["claude-sonnet-4.5", "claude-sonnet-4.5-thinking"]:
        backend, target_model = get_backend_and_model_for_routing(model)
        print(f"  {model}:")
        print(f"       - 后端: {backend}")
        print(f"       - 目标模型: {target_model}")
        if backend == "kiro-gateway":
            print(f"       [OK] 正确路由到 kiro-gateway")
        else:
            print(f"       [WARNING] 未路由到 kiro-gateway，而是: {backend}")
            
except Exception as e:
    print(f"  [ERROR] 路由决策失败: {e}")
    import traceback
    traceback.print_exc()

# 6. 检查环境变量
print("\n[6] 检查环境变量...")
env_vars = [
    "KIRO_GATEWAY_ENABLED",
    "KIRO_GATEWAY_BASE_URL",
    "SONNET45_KIRO_FIRST",
]

for var in env_vars:
    value = os.getenv(var, "未设置")
    print(f"  {var}: {value}")

print("\n" + "=" * 70)
print("诊断完成")
print("=" * 70)
print("\n建议:")
print("1. 检查日志级别设置，确保 INFO 级别的日志被输出")
print("2. 检查 kiro-gateway 服务是否正在运行")
print("3. 检查实际请求是否使用了 claude-sonnet-4.5 模型")
print("4. 查看应用启动日志，确认 model_routing 配置已加载")
