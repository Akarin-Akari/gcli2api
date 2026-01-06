#!/usr/bin/env python3
"""
修复模型名称映射 - 添加映射调用到 proxy_request_to_backend
"""

import re

def main():
    file_path = "F:/antigravity2api/gcli2api/src/unified_gateway_router.py"

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 检查是否已经添加了映射调用
    if "Mapping model for Copilot" in content:
        print("[SKIP] Model mapping call already exists")
        return

    # 使用正则表达式精确匹配并替换
    pattern = r'''(    backend = BACKENDS\.get\(backend_key\)\n    if not backend:\n        return False, f"Backend \{backend_key\} not found"\n\n)(    url = f"\{backend\['base_url'\]\}\{endpoint\}")'''

    replacement = r'''\1    # 对 Copilot 后端应用模型名称映射
    if backend_key == "copilot" and body and isinstance(body, dict) and "model" in body:
        original_model = body.get("model", "")
        mapped_model = map_model_for_copilot(original_model)
        if mapped_model != original_model:
            log.info(f"[Gateway] Mapping model for Copilot: {original_model} -> {mapped_model}")
            body = {**body, "model": mapped_model}

\2'''

    new_content, count = re.subn(pattern, replacement, content, count=1)

    if count == 0:
        print("[ERROR] Could not find pattern to replace")
        # 尝试另一种方式
        # 找到 proxy_request_to_backend 函数并在 url = 之前插入
        lines = content.split('\n')
        new_lines = []
        in_proxy_function = False
        found_backend_get = False
        inserted = False

        for i, line in enumerate(lines):
            if 'async def proxy_request_to_backend(' in line:
                in_proxy_function = True

            if in_proxy_function and 'backend = BACKENDS.get(backend_key)' in line:
                found_backend_get = True

            # 在 url = 之前插入映射代码
            if in_proxy_function and found_backend_get and not inserted:
                if line.strip().startswith("url = f\"{backend['base_url']}{endpoint}\""):
                    # 插入模型映射代码
                    new_lines.append("    # 对 Copilot 后端应用模型名称映射")
                    new_lines.append('    if backend_key == "copilot" and body and isinstance(body, dict) and "model" in body:')
                    new_lines.append('        original_model = body.get("model", "")')
                    new_lines.append('        mapped_model = map_model_for_copilot(original_model)')
                    new_lines.append('        if mapped_model != original_model:')
                    new_lines.append('            log.info(f"[Gateway] Mapping model for Copilot: {original_model} -> {mapped_model}")')
                    new_lines.append('            body = {**body, "model": mapped_model}')
                    new_lines.append('')
                    inserted = True
                    print("[OK] Inserted model mapping code")

            new_lines.append(line)

        if inserted:
            new_content = '\n'.join(new_lines)
        else:
            print("[ERROR] Could not find insertion point using line-by-line method")
            return
    else:
        print("[OK] Inserted model mapping code via regex")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print("\n[SUCCESS] Model mapping call added to proxy_request_to_backend!")

if __name__ == "__main__":
    main()
