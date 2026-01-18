# 阿卡林网关 - 前端开发与 Tauri 打包指南

> **项目名称**: Akari's Gateway (阿卡林网关)  
> **文档类型**: 前端开发与打包指南  
> **版本**: 1.0.0  
> **最后更新**: 2026-01-17

---

## 📋 目录

1. [前端架构设计](#前端架构设计)
2. [UI 界面设计](#ui-界面设计)
3. [后端管理界面](#后端管理界面)
4. [工具管理界面](#工具管理界面)
5. [Tauri 集成方案](#tauri-集成方案)
6. [Nuitka 打包 FastAPI 内核](#nuitka-打包-fastapi-内核)
7. [打包配置](#打包配置)
8. [分发与发布](#分发与发布)
9. [开发工作流](#开发工作流)

---

## 前端架构设计

### 核心架构理念

**双层架构设计**:
- **内核层**: FastAPI 服务（Nuitka 打包为 `server.exe`）
- **UI 层**: Tauri 应用（React + TypeScript）

**架构类比**: 类似 Clash 的架构
- `server.exe` ≈ `mihomo` (核心代理引擎)
- Tauri 应用 ≈ Clash GUI (用户界面)

### 技术栈选择

**内核层**:
- **框架**: FastAPI (Python)
- **打包工具**: Nuitka (打包为独立 exe)
- **输出**: `server.exe` (单文件可执行程序)

**UI 层**:
- **前端框架**: React 18+ (TypeScript)
- **UI 组件库**: 自定义组件 + Tailwind CSS
- **状态管理**: Zustand 或 React Context
- **HTTP 客户端**: Axios 或 Fetch API
- **桌面框架**: Tauri 2.0+
- **构建工具**: Vite
- **包管理**: pnpm 或 npm

### 项目结构

```
akari-gateway-ui/
├── src/
│   ├── components/          # 可复用组件
│   │   ├── BackendCard.tsx  # 后端卡片组件
│   │   ├── ToolCard.tsx     # 工具卡片组件
│   │   ├── StatusBadge.tsx  # 状态徽章
│   │   └── ...
│   ├── pages/               # 页面组件
│   │   ├── Dashboard.tsx    # 仪表板
│   │   ├── BackendManager.tsx # 后端管理
│   │   ├── ToolManager.tsx  # 工具管理
│   │   └── Settings.tsx     # 设置页面
│   ├── stores/              # 状态管理
│   │   ├── backendStore.ts  # 后端状态
│   │   ├── toolStore.ts     # 工具状态
│   │   └── configStore.ts   # 配置状态
│   ├── services/            # API 服务
│   │   ├── api.ts           # API 客户端
│   │   ├── backendService.ts
│   │   └── toolService.ts
│   ├── hooks/               # 自定义 Hooks
│   │   ├── useBackends.ts
│   │   └── useTools.ts
│   ├── types/               # TypeScript 类型
│   │   ├── backend.ts
│   │   └── tool.ts
│   └── utils/               # 工具函数
├── src-tauri/               # Tauri 后端
│   ├── src/
│   │   ├── main.rs          # Rust 主入口
│   │   ├── commands.rs      # Tauri 命令
│   │   └── ...
│   ├── tauri.conf.json      # Tauri 配置
│   └── Cargo.toml
├── public/                  # 静态资源
├── package.json
├── vite.config.ts
└── tsconfig.json
```

### 数据流设计

```
用户操作 → React 组件 → Zustand Store → API Service → FastAPI 后端
                ↓
        状态更新 → UI 重新渲染
```

---

## UI 界面设计

### 设计原则

- **简洁直观**: 界面清晰，操作简单
- **响应式设计**: 支持不同屏幕尺寸
- **深色模式**: 默认深色主题，支持切换
- **实时反馈**: 操作结果即时显示
- **错误处理**: 友好的错误提示

### 主界面布局

```
┌─────────────────────────────────────────────────────┐
│  [Logo] 阿卡林网关              [设置] [关于] [退出] │
├─────────────────────────────────────────────────────┤
│  [仪表板] [后端管理] [工具管理] [路由配置] [日志]   │
├─────────────────────────────────────────────────────┤
│                                                     │
│  主内容区域                                         │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 核心页面

#### 1. 仪表板 (Dashboard)

**功能**:
- 显示网关运行状态
- 后端服务健康状态概览
- 请求统计（总数、成功率、平均延迟）
- 最近错误日志
- 快速操作按钮

**组件**:
- 状态卡片（运行中/已停止）
- 后端状态列表
- 统计图表
- 错误日志列表

#### 2. 后端管理 (Backend Manager)

**功能**:
- 后端列表展示
- 添加/编辑/删除后端
- 启用/禁用后端
- 测试后端连接
- 后端优先级调整

**界面元素**:
- 后端卡片（名称、状态、优先级、端点）
- 添加后端按钮
- 编辑/删除操作按钮
- 拖拽排序（优先级）

#### 3. 工具管理 (Tool Manager)

**功能**:
- 工具列表展示
- 添加/编辑/删除工具
- 工具启用/禁用
- 工具格式转换规则配置
- 工具测试

**界面元素**:
- 工具卡片（名称、类型、状态、描述）
- 工具编辑器（JSON Schema）
- 格式转换规则配置
- 测试工具按钮

#### 4. 路由配置 (Routing Config)

**功能**:
- 模型路由规则配置
- 路由策略选择
- 故障转移规则
- 优先级调整

**界面元素**:
- 路由规则表格
- 模型名称输入
- 后端选择下拉
- 优先级滑块

#### 5. 设置 (Settings)

**功能**:
- 网关端口配置
- 超时时间配置
- 日志级别设置
- 主题切换
- 数据导入/导出

---

## 后端管理界面

### 后端列表展示

**数据结构**:
```typescript
interface Backend {
  key: string;           // 唯一标识
  name: string;          // 显示名称
  base_url: string;      // 基础 URL
  priority: number;      // 优先级
  timeout: number;       // 超时时间（秒）
  stream_timeout: number; // 流式超时（秒）
  max_retries: number;   // 最大重试次数
  enabled: boolean;      // 启用状态
  status: 'healthy' | 'unhealthy' | 'unknown'; // 健康状态
  last_check: string;    // 最后检查时间
}
```

**界面组件**:
- 后端卡片组件
- 状态指示器（绿色/红色/灰色）
- 操作按钮（编辑/删除/测试/启用切换）
- 优先级拖拽手柄

### 添加/编辑后端

**表单字段**:
- 后端名称（必填）
- 基础 URL（必填，格式验证）
- 优先级（数字，1-10）
- 超时时间（秒）
- 流式超时（秒）
- 最大重试次数
- 启用状态（开关）

**验证规则**:
- URL 格式验证
- 端口范围验证
- 优先级唯一性检查
- 超时时间合理性检查

### 后端测试功能

**测试项目**:
- 连接测试（ping）
- 健康检查（/health 或 /models）
- API 兼容性测试（发送测试请求）
- 响应时间测试

**测试结果展示**:
- 成功/失败状态
- 响应时间
- 错误信息（如有）

---

## 工具管理界面

### 工具列表展示

**数据结构**:
```typescript
interface Tool {
  id: string;            // 工具 ID
  name: string;          // 工具名称
  description: string;  // 工具描述
  type: 'function' | 'custom'; // 工具类型
  enabled: boolean;      // 启用状态
  schema: object;        // JSON Schema
  conversion_rules?: {   // 格式转换规则
    from: string;        // 源格式
    to: string;          // 目标格式
    mapping: object;     // 字段映射
  };
}
```

**界面组件**:
- 工具卡片
- 工具类型标签
- 启用/禁用开关
- 编辑/删除按钮
- 测试按钮

### 工具编辑器

**功能**:
- JSON Schema 编辑器（代码高亮）
- 实时验证
- 格式转换规则配置
- 预览功能

**编辑器特性**:
- 语法高亮
- 自动补全
- 错误提示
- 格式化

### 工具测试

**测试功能**:
- 发送测试请求
- 查看工具调用结果
- 验证格式转换
- 性能测试

---

## Tauri 集成方案

### 架构设计

**核心思想**: 将 FastAPI 服务打包为独立可执行文件 `server.exe`，作为 Tauri 应用的内核。

```
┌─────────────────────────────────────┐
│      React 前端 (WebView)           │
│  - UI 组件                           │
│  - 状态管理                          │
│  - API 调用 (HTTP)                   │
└──────────────┬──────────────────────┘
               │ HTTP (localhost)
               │
┌──────────────▼──────────────────────┐
│      Tauri Rust 后端                │
│  - 进程管理 (启动/停止 server.exe)   │
│  - 文件操作 (配置文件)               │
│  - 系统调用                          │
└──────────────┬──────────────────────┘
               │ 子进程管理
               │
┌──────────────▼──────────────────────┐
│      server.exe (Nuitka 打包)      │
│  - FastAPI 服务 (独立进程)          │
│  - 网关路由逻辑                      │
│  - API 端点 (localhost:PORT)        │
│  - 后端管理                          │
└──────────────┬──────────────────────┘
               │ HTTP
               │
┌──────────────▼──────────────────────┐
│      后端服务 (gcli2api 等)        │
└─────────────────────────────────────┘
```

### 内核层设计 (server.exe)

**职责**:
- 提供 FastAPI 服务
- 处理网关路由逻辑
- 管理后端服务
- 提供 REST API 接口

**特点**:
- 独立可执行文件（Nuitka 打包）
- 可以独立运行（不依赖 Tauri）
- 通过 HTTP 接口与 UI 通信
- 配置文件存储在应用目录

**优势**:
- 内核和 UI 解耦
- 便于单独更新内核
- 便于调试和测试
- 可以命令行运行（开发/调试）

### Tauri 命令设计

#### 1. 内核服务管理命令

```rust
// 启动 server.exe
#[tauri::command]
async fn start_server(port: u16, config_path: String) -> Result<u32, String> {
    // 获取 server.exe 路径（打包在资源目录）
    let server_exe = get_resource_path("server.exe")?;
    
    // 启动子进程
    let mut cmd = Command::new(server_exe)
        .arg("--port")
        .arg(port.to_string())
        .arg("--config")
        .arg(config_path)
        .spawn()?;
    
    Ok(cmd.id())
}

// 停止 server.exe
#[tauri::command]
async fn stop_server(pid: u32) -> Result<(), String> {
    // 终止进程
    kill_process(pid)?;
    Ok(())
}

// 检查服务状态
#[tauri::command]
async fn get_server_status(port: u16) -> Result<ServerStatus, String> {
    // 检查端口是否监听
    // 发送健康检查请求
    let status = check_server_health(port).await?;
    Ok(status)
}

// 获取 server.exe 版本
#[tauri::command]
async fn get_server_version() -> Result<String, String> {
    let server_exe = get_resource_path("server.exe")?;
    let output = Command::new(server_exe)
        .arg("--version")
        .output()?;
    Ok(String::from_utf8(output.stdout)?)
}
```

#### 2. 配置管理命令

```rust
// 读取配置
#[tauri::command]
async fn read_config() -> Result<Config, String>

// 保存配置
#[tauri::command]
async fn save_config(config: Config) -> Result<(), String>

// 导出配置
#[tauri::command]
async fn export_config(path: String) -> Result<(), String>

// 导入配置
#[tauri::command]
async fn import_config(path: String) -> Result<Config, String>
```

#### 3. 文件操作命令

```rust
// 选择文件
#[tauri::command]
async fn select_file() -> Result<Option<String>, String>

// 读取文件
#[tauri::command]
async fn read_file(path: String) -> Result<String, String>

// 写入文件
#[tauri::command]
async fn write_file(path: String, content: String) -> Result<(), String>
```

#### 4. 系统信息命令

```rust
// 获取系统信息
#[tauri::command]
async fn get_system_info() -> Result<SystemInfo, String>

// 检查端口占用
#[tauri::command]
async fn check_port(port: u16) -> Result<bool, String>
```

### Tauri 配置

**tauri.conf.json 关键配置**:

```json
{
  "build": {
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build",
    "devPath": "http://localhost:1420",
    "distDir": "../dist"
  },
  "package": {
    "productName": "阿卡林网关",
    "version": "1.0.0"
  },
  "tauri": {
    "allowlist": {
      "all": false,
      "shell": {
        "all": false,
        "execute": true,
        "sidecar": true,
        "open": true
      },
      "fs": {
        "all": false,
        "readFile": true,
        "writeFile": true,
        "scope": ["$APPDATA/**", "$RESOURCE/**"]
      },
      "path": {
        "all": true
      },
      "process": {
        "all": false,
        "relaunch": true
      }
    },
    "windows": [
      {
        "title": "阿卡林网关",
        "width": 1200,
        "height": 800,
        "minWidth": 800,
        "minHeight": 600,
        "resizable": true,
        "fullscreen": false
      }
    ]
  }
}
```

---

## Nuitka 打包 FastAPI 内核

### Nuitka 简介

Nuitka 是一个 Python 编译器，可以将 Python 代码编译为独立的可执行文件。

**优势**:
- 生成单文件可执行程序
- 无需 Python 运行时
- 启动速度快
- 文件体积相对较小

### 打包配置

#### 1. 安装 Nuitka

```bash
pip install nuitka
```

#### 2. 打包脚本 (nuitka_build.py)

```python
#!/usr/bin/env python3
"""
Nuitka 打包脚本 - 将 FastAPI 应用打包为 server.exe
"""

import os
import subprocess
import sys

def build_server():
    """打包 FastAPI 服务为 server.exe"""
    
    # 项目根目录
    project_root = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(project_root, "src")
    main_file = os.path.join(src_dir, "main.py")
    
    # Nuitka 命令参数
    nuitka_args = [
        "python", "-m", "nuitka",
        "--standalone",                    # 独立模式
        "--onefile",                       # 单文件模式
        "--enable-plugin=anti-bloat",      # 启用反膨胀插件
        "--enable-plugin=multiprocessing",  # 启用多进程支持
        "--include-module=fastapi",         # 包含 FastAPI
        "--include-module=uvicorn",         # 包含 Uvicorn
        "--include-module=httpx",           # 包含 httpx
        "--include-module=pydantic",        # 包含 Pydantic
        "--windows-icon-from-ico=icon.ico", # 图标（可选）
        "--output-dir=dist",                # 输出目录
        "--output-filename=server.exe",     # 输出文件名
        "--assume-yes-for-downloads",       # 自动下载依赖
        main_file
    ]
    
    # 执行打包
    print("开始打包 server.exe...")
    result = subprocess.run(nuitka_args, cwd=project_root)
    
    if result.returncode == 0:
        print("✅ 打包成功！")
        print(f"输出文件: {project_root}/dist/server.exe")
    else:
        print("❌ 打包失败！")
        sys.exit(1)

if __name__ == "__main__":
    build_server()
```

#### 3. 打包命令

```bash
# 开发环境打包
python nuitka_build.py

# 或直接使用 Nuitka
python -m nuitka --standalone --onefile src/main.py
```

#### 4. 打包优化

**减小体积**:
```python
# 排除不需要的模块
--nofollow-import-to=matplotlib
--nofollow-import-to=numpy
--nofollow-import-to=pandas

# 使用 UPX 压缩（可选）
--upx-binary=upx.exe
```

**性能优化**:
```python
# 启用优化
--lto=yes  # 链接时优化

# 禁用调试信息
--no-debug
```

### 内核集成到 Tauri

#### 1. 资源文件配置

**tauri.conf.json**:
```json
{
  "tauri": {
    "bundle": {
      "resources": [
        "server.exe"
      ]
    }
  }
}
```

#### 2. Rust 代码获取资源路径

```rust
use tauri::api::path::resource_dir;
use tauri::Manager;

fn get_server_exe_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let resource_dir = resource_dir(app.config(), app.package_info())
        .ok_or("无法获取资源目录")?;
    
    let server_exe = resource_dir.join("server.exe");
    
    if !server_exe.exists() {
        return Err("server.exe 不存在".to_string());
    }
    
    Ok(server_exe)
}
```

#### 3. 启动内核服务

```rust
use std::process::{Command, Stdio};
use std::path::PathBuf;

async fn start_server_internal(
    app: tauri::AppHandle,
    port: u16,
    config_path: PathBuf,
) -> Result<u32, String> {
    let server_exe = get_server_exe_path(&app)?;
    
    // 启动 server.exe
    let mut child = Command::new(&server_exe)
        .arg("--port")
        .arg(port.to_string())
        .arg("--config")
        .arg(config_path)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动 server.exe 失败: {}", e))?;
    
    // 保存进程 ID
    let pid = child.id();
    app.state::<ServerState>().set_pid(pid);
    
    Ok(pid)
}
```

### 内核更新机制

#### 1. 检查更新

```rust
#[tauri::command]
async fn check_kernel_update() -> Result<UpdateInfo, String> {
    // 从 GitHub Releases 检查 server.exe 更新
    let latest_version = fetch_latest_version().await?;
    let current_version = get_current_version()?;
    
    Ok(UpdateInfo {
        current: current_version,
        latest: latest_version,
        available: latest_version > current_version,
    })
}
```

#### 2. 下载更新

```rust
#[tauri::command]
async fn download_kernel_update() -> Result<PathBuf, String> {
    // 下载新的 server.exe
    let download_url = get_download_url().await?;
    let temp_path = download_file(download_url).await?;
    Ok(temp_path)
}
```

#### 3. 应用更新

```rust
#[tauri::command]
async fn apply_kernel_update(new_exe_path: PathBuf) -> Result<(), String> {
    // 停止当前服务
    stop_server().await?;
    
    // 替换 server.exe
    let resource_dir = get_resource_dir()?;
    let server_exe = resource_dir.join("server.exe");
    std::fs::copy(new_exe_path, server_exe)?;
    
    // 重启服务
    start_server().await?;
    
    Ok(())
}
```

---

## 打包配置

### 开发环境配置

**package.json 脚本**:
```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "tauri": "tauri",
    "tauri:dev": "tauri dev",
    "tauri:build": "tauri build"
  }
}
```

### 构建配置

**Vite 配置 (vite.config.ts)**:
```typescript
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    target: ['es2021', 'chrome100', 'safari13'],
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
});
```

### 完整打包流程

#### 阶段 1: 打包内核 (server.exe)

```bash
cd server/
python nuitka_build.py
```

**输出**: `server/dist/server.exe`

#### 阶段 2: 复制内核到 Tauri 资源目录

```bash
# 将 server.exe 复制到 Tauri 资源目录
cp server/dist/server.exe ui/src-tauri/resources/server.exe
```

#### 阶段 3: 构建前端

```bash
cd ui/
npm run build
```

**输出**: `ui/dist/` (前端静态文件)

#### 阶段 4: Tauri 打包

```bash
cd ui/
npm run tauri:build
```

**输出**:
- Windows: `ui/src-tauri/target/release/bundle/msi/` (MSI 安装包)
- Windows: `ui/src-tauri/target/release/bundle/nsis/` (NSIS 安装包)
- macOS: `ui/src-tauri/target/release/bundle/dmg/` (DMG 镜像)
- Linux: `ui/src-tauri/target/release/bundle/appimage/` (AppImage)

### 自动化打包脚本

**build.sh** (Linux/macOS):
```bash
#!/bin/bash
set -e

echo "🔨 开始打包阿卡林网关..."

# 1. 打包内核
echo "📦 打包 server.exe..."
cd server/
python nuitka_build.py
cd ..

# 2. 复制内核
echo "📋 复制 server.exe 到 Tauri 资源目录..."
mkdir -p ui/src-tauri/resources
cp server/dist/server.exe ui/src-tauri/resources/server.exe

# 3. 构建前端
echo "🎨 构建前端..."
cd ui/
npm run build
cd ..

# 4. Tauri 打包
echo "🚀 Tauri 打包..."
cd ui/
npm run tauri:build
cd ..

echo "✅ 打包完成！"
```

**build.ps1** (Windows):
```powershell
# 类似的 PowerShell 脚本
```

### 打包产物结构

```
最终安装包包含:
├── akari-gateway.exe        # Tauri 主程序
├── server.exe               # FastAPI 内核（资源文件）
├── WebView2Loader.dll       # WebView2 运行时
└── 其他依赖文件
```

### 打包优化

#### 内核优化 (server.exe)

**减小体积**:
- 使用 `--onefile` 单文件模式
- 排除不需要的模块
- 使用 UPX 压缩（可选）
- 移除调试信息

**性能优化**:
- 启用 `--lto=yes` 链接时优化
- 使用 `--no-debug` 禁用调试
- 优化导入模块

#### UI 层优化

**减小体积**:
- 启用代码压缩
- 移除未使用的依赖
- 优化图片资源
- 使用 Tree Shaking

**性能优化**:
- 启用 Rust 优化编译
- 使用 Release 模式
- 优化前端打包
- 启用代码分割

### 最终体积估算

- **server.exe**: ~30-50 MB (Nuitka 打包)
- **Tauri 应用**: ~20-30 MB (不含 server.exe)
- **总计**: ~50-80 MB (单文件或安装包)

---

## 分发与发布

### GitHub Releases

**发布流程**:
1. 创建 Git Tag
   ```bash
   git tag -a v1.0.0 -m "Release version 1.0.0"
   git push origin v1.0.0
   ```

2. 构建多平台版本
   - Windows (MSI + NSIS)
   - macOS (DMG)
   - Linux (AppImage)

3. 上传到 GitHub Releases
   - 使用 GitHub Actions 自动化
   - 或手动上传构建产物

### GitHub Actions 自动化

**.github/workflows/release.yml**:
```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  build:
    strategy:
      fail-fast: false
      matrix:
        include:
          - platform: 'windows-latest'
            args: '--target x86_64-pc-windows-msvc'
          - platform: 'macos-latest'
            args: '--target aarch64-apple-darwin'
          - platform: 'ubuntu-latest'
            args: '--target x86_64-unknown-linux-gnu'

    runs-on: ${{ matrix.platform }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - uses: dtolnay/rust-toolchain@stable
      - name: Install dependencies
        run: npm install
      - name: Build
        run: npm run tauri:build -- ${{ matrix.args }}
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: ${{ matrix.platform }}
          path: src-tauri/target/release/bundle/
```

### 安装包说明

**Windows**:
- MSI 安装包：标准 Windows 安装程序
- NSIS 安装包：更小的单文件安装程序

**macOS**:
- DMG 镜像：拖拽安装
- 需要代码签名（可选）

**Linux**:
- AppImage：单文件，无需安装
- 需要设置执行权限

### 版本管理

**版本号规则**:
- 主版本号.次版本号.修订号 (Semantic Versioning)
- 示例: 1.0.0, 1.1.0, 2.0.0

**更新机制**:
- 检查更新 API
- 自动更新功能（可选）
- 手动下载更新

---

## 开发工作流

### 本地开发

1. **启动开发服务器**
   ```bash
   npm run tauri:dev
   ```
   - 启动 Vite 开发服务器
   - 启动 Tauri 应用
   - 热重载支持

2. **开发流程**
   - 修改前端代码 → 自动重载
   - 修改 Rust 代码 → 需要重启
   - 修改 Tauri 配置 → 需要重启

### 调试

**前端调试**:
- 浏览器开发者工具
- React DevTools
- 控制台日志

**Rust 调试**:
- `println!` 宏
- 日志系统
- 断点调试（需要配置）

### 测试

**前端测试**:
- 单元测试（Vitest）
- 组件测试（React Testing Library）
- E2E 测试（Playwright）

**集成测试**:
- API 测试
- Tauri 命令测试
- 端到端流程测试

### 代码规范

**TypeScript**:
- 使用 ESLint
- 使用 Prettier
- 类型严格检查

**Rust**:
- 使用 rustfmt
- 使用 clippy
- 遵循 Rust 编码规范

---

## 快速开始

### 项目初始化

#### 1. 创建项目结构

```bash
# 创建根目录
mkdir akari-gateway
cd akari-gateway

# 创建内核项目
mkdir server
cd server
# 初始化 Python 项目（FastAPI）

# 创建 UI 项目
cd ..
npm create tauri-app@latest ui
# 选择模板: React + TypeScript + Vite + pnpm
```

#### 2. 安装依赖

```bash
# 内核依赖
cd server/
pip install -r requirements.txt
pip install nuitka

# UI 依赖
cd ../ui/
pnpm install
```

#### 3. 配置项目

- 配置 `server/nuitka_build.py`
- 配置 `ui/src-tauri/tauri.conf.json`
- 配置资源文件路径

### 开发命令

#### 内核开发

```bash
cd server/

# 开发模式（直接运行 Python）
python src/main.py

# 打包内核
python nuitka_build.py
```

#### UI 开发

```bash
cd ui/

# 开发模式
pnpm tauri:dev

# 构建前端
pnpm build

# 打包应用（需要先打包内核）
pnpm tauri:build
```

#### 完整打包

```bash
# 从项目根目录
./build.sh  # Linux/macOS
# 或
./build.ps1  # Windows
```

### 项目结构初始化

1. **创建目录结构**
   - `server/` - FastAPI 内核项目
   - `ui/` - Tauri UI 项目

2. **配置内核项目**
   - 编写 `nuitka_build.py`
   - 配置 `requirements.txt`
   - 设置入口文件 `main.py`

3. **配置 UI 项目**
   - 配置 TypeScript
   - 配置 Vite
   - 配置 Tauri
   - 安装 UI 组件库

4. **集成内核**
   - 配置资源文件路径
   - 实现服务管理命令
   - 实现进程管理逻辑

---

## 注意事项

### 架构优势

1. **解耦设计**
   - 内核和 UI 完全分离
   - 可以独立更新内核
   - 便于单独测试和调试

2. **灵活性**
   - server.exe 可以命令行运行
   - 便于 CI/CD 集成
   - 支持无头模式（无 UI）

3. **可维护性**
   - 内核更新不影响 UI
   - UI 更新不影响内核
   - 便于版本管理

### 安全考虑

- **文件系统访问**: 限制在应用目录内
- **网络访问**: 仅允许访问本地 FastAPI 服务
- **进程管理**: 仅管理 server.exe 子进程
- **配置验证**: 验证所有用户输入
- **资源文件**: 验证 server.exe 完整性

### 开发建议

1. **内核开发**
   - 先独立开发和测试 FastAPI 服务
   - 确保可以命令行运行
   - 再集成到 Tauri

2. **UI 开发**
   - 开发时可以直接连接本地 FastAPI（开发模式）
   - 打包时使用嵌入的 server.exe

3. **调试**
   - 内核可以单独调试（Python 调试器）
   - UI 可以单独调试（浏览器 DevTools）
   - 集成调试需要同时运行两个进程

### 性能优化

- **启动速度**: 优化应用启动时间
- **内存使用**: 监控内存占用
- **CPU 使用**: 避免阻塞主线程
- **网络请求**: 使用连接池

### 兼容性

- **Windows**: Windows 10+
- **macOS**: macOS 10.15+
- **Linux**: Ubuntu 20.04+ / 其他主流发行版

### 已知问题

- Tauri 2.0 可能与某些防病毒软件冲突
- 首次启动可能需要较长时间
- 某些系统可能需要管理员权限

---

## 参考资源

- [Tauri 官方文档](https://tauri.app/)
- [React 官方文档](https://react.dev/)
- [TypeScript 官方文档](https://www.typescriptlang.org/)
- [Vite 官方文档](https://vitejs.dev/)

---

**文档最后更新**: 2026-01-17  
**文档版本**: 1.0.0
