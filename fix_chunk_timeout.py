#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 unified_gateway_router.py 中的 chunk_timeout 问题

问题描述：
- 原代码在收到 chunk 后检查时间差，如果超过 120 秒就中断连接
- 这是错误的逻辑：当模型需要长时间思考时，两个 chunk 之间可能超过 120 秒
- 但只要最终收到了数据，就不应该超时

解决方案：
- 移除错误的 chunk_timeout 检查
- httpx 的 read=timeout 配置已经处理了真正的读取超时

使用方法：
1. 先停止 gcli2api 服务
2. 运行此脚本: python fix_chunk_timeout.py
3. 重新启动 gcli2api 服务
"""

import os
import shutil
import sys
from datetime import datetime


def fix_chunk_timeout():
    # 获取脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, 'src', 'unified_gateway_router.py')

    print(f"目标文件: {file_path}")

    if not os.path.exists(file_path):
        print(f"错误：文件不存在 {file_path}")
        return False

    # 读取文件内容（使用 utf-8-sig 处理 BOM）
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
    except PermissionError:
        print("错误：无法读取文件，文件被占用")
        print("请先停止 gcli2api 服务后再运行此脚本")
        return False
    except Exception as e:
        print(f"错误：读取文件失败 - {e}")
        return False

    # 创建备份目录
    backup_dir = os.path.join(script_dir, '_archive', 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    # 备份原文件
    backup_filename = f'unified_gateway_router.py.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    backup_path = os.path.join(backup_dir, backup_filename)
    try:
        shutil.copy2(file_path, backup_path)
        print(f"已备份原文件到: {backup_path}")
    except Exception as e:
        print(f"警告：备份失败 - {e}")

    # 检查是否已经修复
    if 'chunk_timeout = 120.0' not in content:
        print("未找到 chunk_timeout = 120.0，可能已经修复过了")
        return True

    # 逐行处理
    lines = content.split('\n')
    new_lines = []

    # 状态机变量
    in_proxy_streaming_func = False
    in_stream_generator = False
    skip_mode = False
    skip_count = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # 检测进入 proxy_streaming_request_with_timeout 函数
        if 'async def proxy_streaming_request_with_timeout(' in line:
            in_proxy_streaming_func = True
            new_lines.append(line)
            i += 1
            continue

        # 检测进入 stream_generator 函数（在 proxy_streaming_request_with_timeout 内部）
        if in_proxy_streaming_func and 'async def stream_generator():' in line:
            in_stream_generator = True
            new_lines.append(line)
            # 添加注释说明为什么移除了 chunk_timeout
            new_lines.append('            # 注意：chunk_timeout 检查已移除')
            new_lines.append('            # 原因：之前的逻辑是在收到 chunk 后才检查时间差，这是错误的。')
            new_lines.append('            # 当模型需要长时间思考（如 Claude 写长文档）时，两个 chunk 之间可能超过 120 秒，')
            new_lines.append('            # 但只要最终收到了数据，就不应该超时。')
            new_lines.append('            # httpx 的 read=timeout 配置已经处理了真正的读取超时。')
            new_lines.append('')
            i += 1
            continue

        # 在 stream_generator 内部，跳过需要删除的行
        if in_stream_generator:
            # 跳过 last_data_time = time.time()
            if 'last_data_time = time.time()' in line:
                i += 1
                continue

            # 跳过 chunk_timeout = 120.0
            if 'chunk_timeout = 120.0' in line:
                i += 1
                continue

            # 跳过 current_time = time.time()
            if 'current_time = time.time()' in line and 'chunk' not in lines[i-1] if i > 0 else True:
                i += 1
                continue

            # 跳过 # 检查是否超过chunk超时
            if '# 检查是否超过chunk超时' in line:
                i += 1
                continue

            # 跳过 if current_time - last_data_time > chunk_timeout: 及其整个块
            if 'if current_time - last_data_time > chunk_timeout:' in line:
                # 跳过整个 if 块（包括 log.warning, error_msg, yield, break）
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    # 检测 if 块结束（遇到 break 或缩进减少）
                    if 'break' in next_line:
                        i += 1
                        break
                    # 如果遇到空行后面跟着非缩进行，说明 if 块结束
                    if next_line.strip() == '':
                        # 检查下一行是否还在 if 块内
                        if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith('                                '):
                            break
                    i += 1
                continue

            # 跳过 last_data_time = current_time
            if 'last_data_time = current_time' in line:
                i += 1
                continue

            # 检测退出 stream_generator 函数
            if 'log.success(f"Streaming completed"' in line:
                in_stream_generator = False

        # 检测退出 proxy_streaming_request_with_timeout 函数
        if in_proxy_streaming_func and line.strip() and not line.startswith(' ') and not line.startswith('\t'):
            if 'async def ' in line or 'def ' in line or 'class ' in line:
                in_proxy_streaming_func = False
                in_stream_generator = False

        new_lines.append(line)
        i += 1

    new_content = '\n'.join(new_lines)

    # 验证修改
    if 'chunk_timeout = 120.0' in new_content:
        print("警告：替换可能不完整，chunk_timeout 仍然存在")
        print("请手动检查并修改文件")
        return False

    # 写入文件（使用 utf-8 编码）
    try:
        with open(file_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_content)
    except PermissionError:
        print("错误：无法写入文件，文件被占用")
        print("请先停止 gcli2api 服务后再运行此脚本")
        return False
    except Exception as e:
        print(f"错误：写入文件失败 - {e}")
        # 尝试恢复备份
        if os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, file_path)
                print("已从备份恢复原文件")
            except:
                pass
        return False

    print(f"\n✅ 成功修复 {file_path}")
    print("修改内容：移除了错误的 chunk_timeout 检查逻辑")
    print("\n请重启 gcli2api 服务以使更改生效")
    return True


if __name__ == '__main__':
    print("=" * 60)
    print("gcli2api chunk_timeout 修复脚本")
    print("=" * 60)
    print()

    # 检查是否需要先停止服务
    print("⚠️  重要提示：请确保 gcli2api 服务已停止！")
    print()

    if len(sys.argv) > 1 and sys.argv[1] == '--force':
        pass
    else:
        response = input("是否继续？(y/N): ").strip().lower()
        if response != 'y':
            print("已取消")
            sys.exit(0)

    print()
    success = fix_chunk_timeout()
    sys.exit(0 if success else 1)
