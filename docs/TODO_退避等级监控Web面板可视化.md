# TODO: 退避等级监控（Web 面板可视化）

**创建日期**: 2026-01-17
**优先级**: P2（低优先级）
**预估工作量**: 4-6 小时
**负责人**: 待分配

---

## 📋 需求概述

### 目标

在 Web 管理面板中添加**退避等级监控**功能，帮助管理员：
1. 实时查看每个凭证的退避等级
2. 识别频繁失败的凭证
3. 监控限流状态和恢复进度
4. 辅助故障排查和性能优化

### 用户故事

**作为**管理员，
**我希望**在 Web 面板中查看所有凭证的退避等级，
**以便**快速识别哪些凭证遇到了限流问题，并采取相应措施。

---

## 🎯 功能需求

### 1. 退避等级状态页面

#### 页面路径

```
GET /api/credentials/backoff-status?password=<panel_password>
```

#### 响应格式

```json
{
  "backoff_status": [
    {
      "name": "cred_001.json",
      "type": "antigravity",
      "models": {
        "gemini-3-flash": {
          "backoff_level": 2,
          "cooldown_until": 1705478400.0,
          "next_retry_after": "2026-01-17 10:30:00",
          "status": "cooling_down",
          "last_updated": 1705478350.0
        },
        "claude-sonnet-4-5": {
          "backoff_level": 0,
          "cooldown_until": 0.0,
          "next_retry_after": null,
          "status": "active",
          "last_updated": 1705478450.0
        }
      },
      "max_backoff_level": 2,
      "overall_status": "cooling_down"
    },
    {
      "name": "cred_002.json",
      "type": "geminicli",
      "models": {
        "gemini-2.0-flash-exp": {
          "backoff_level": 0,
          "cooldown_until": 0.0,
          "next_retry_after": null,
          "status": "active",
          "last_updated": 1705478500.0
        }
      },
      "max_backoff_level": 0,
      "overall_status": "active"
    }
  ],
  "summary": {
    "total_credentials": 2,
    "active": 1,
    "cooling_down": 1,
    "disabled": 0,
    "avg_backoff_level": 1.0,
    "max_backoff_level": 2
  }
}
```

#### 状态定义

| 状态 | 说明 | 条件 |
|------|------|------|
| `active` | 活跃状态 | `backoff_level == 0` 且无冷却时间 |
| `cooling_down` | 冷却中 | `cooldown_until > now` 或 `backoff_level > 0` |
| `disabled` | 已禁用 | `disabled == 1` |

### 2. Web 前端页面

#### 页面布局

```
┌─────────────────────────────────────────────────────────────┐
│  退避等级监控                                    🔄 刷新    │
├─────────────────────────────────────────────────────────────┤
│  总览                                                        │
│  ┌─────────┬─────────┬─────────┬─────────┬─────────┐      │
│  │ 总凭证  │ 活跃    │ 冷却中  │ 已禁用  │ 平均等级│      │
│  │   10    │   7     │   2     │   1     │  0.5    │      │
│  └─────────┴─────────┴─────────┴─────────┴─────────┘      │
├─────────────────────────────────────────────────────────────┤
│  凭证列表                                                    │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ cred_001.json [Antigravity] 🔴 冷却中                 │ │
│  │   ├─ gemini-3-flash: 退避等级 2, 冷却至 10:30        │ │
│  │   └─ claude-sonnet-4-5: 退避等级 0, 活跃             │ │
│  ├───────────────────────────────────────────────────────┤ │
│  │ cred_002.json [GeminiCLI] 🟢 活跃                     │ │
│  │   └─ gemini-2.0-flash-exp: 退避等级 0, 活跃          │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

#### 颜色编码

| 状态 | 颜色 | 图标 |
|------|------|------|
| 活跃 | 🟢 绿色 | ✅ |
| 冷却中 | 🟡 黄色 | ⏳ |
| 退避等级 ≥ 3 | 🔴 红色 | ⚠️ |
| 已禁用 | ⚫ 灰色 | 🚫 |

### 3. 实时刷新

- 支持手动刷新按钮
- 可选自动刷新（每 30 秒）
- WebSocket 实时推送（可选，高级功能）

---

## 🛠️ 技术实现

### 后端实现

#### 文件：`src/web_routes.py`

```python
from fastapi import APIRouter, Query, HTTPException
from typing import Dict, List, Any
import time
from datetime import datetime, timezone

router = APIRouter()

@router.get("/api/credentials/backoff-status")
async def get_backoff_status(password: str = Query(...)):
    """获取所有凭证的退避等级状态"""
    from config import get_panel_password
    from src.credential_manager import get_credential_manager

    # 验证密码
    if password != await get_panel_password():
        raise HTTPException(status_code=401, detail="Unauthorized")

    credential_manager = await get_credential_manager()
    storage = credential_manager.storage

    # 获取所有凭证
    backoff_status = []

    # 处理 Antigravity 凭证
    async with storage.get_connection() as conn:
        result = await conn.execute(
            "SELECT filename, model_cooldowns, disabled FROM antigravity_credentials"
        )
        rows = await result.fetchall()

    for row in rows:
        filename, model_cooldowns_json, disabled = row
        status_entry = await _build_credential_status(
            filename,
            model_cooldowns_json,
            disabled,
            "antigravity"
        )
        backoff_status.append(status_entry)

    # 处理 GeminiCLI 凭证
    async with storage.get_connection() as conn:
        result = await conn.execute(
            "SELECT filename, model_cooldowns, disabled FROM credentials"
        )
        rows = await result.fetchall()

    for row in rows:
        filename, model_cooldowns_json, disabled = row
        status_entry = await _build_credential_status(
            filename,
            model_cooldowns_json,
            disabled,
            "geminicli"
        )
        backoff_status.append(status_entry)

    # 计算总览统计
    summary = _calculate_summary(backoff_status)

    return {
        "backoff_status": backoff_status,
        "summary": summary,
        "timestamp": time.time(),
    }


async def _build_credential_status(
    filename: str,
    model_cooldowns_json: str,
    disabled: int,
    cred_type: str,
) -> Dict[str, Any]:
    """构建单个凭证的状态信息"""
    import json

    try:
        model_cooldowns = json.loads(model_cooldowns_json or "{}")
    except Exception:
        model_cooldowns = {}

    models = {}
    max_backoff_level = 0

    for model_key, value in model_cooldowns.items():
        # 解析值（兼容旧格式）
        if isinstance(value, dict):
            cooldown_until = float(value.get("cooldown_until", 0.0))
            backoff_level = int(value.get("backoff_level", 0))
            last_updated = float(value.get("last_updated", time.time()))
        elif isinstance(value, (int, float)):
            cooldown_until = float(value)
            backoff_level = 0
            last_updated = time.time()
        else:
            cooldown_until = 0.0
            backoff_level = 0
            last_updated = time.time()

        # 计算状态
        now = time.time()
        if cooldown_until > now:
            status = "cooling_down"
            next_retry_after = datetime.fromtimestamp(cooldown_until, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        elif backoff_level > 0:
            status = "cooling_down"
            next_retry_after = None
        else:
            status = "active"
            next_retry_after = None

        models[model_key] = {
            "backoff_level": backoff_level,
            "cooldown_until": cooldown_until,
            "next_retry_after": next_retry_after,
            "status": status,
            "last_updated": last_updated,
        }

        max_backoff_level = max(max_backoff_level, backoff_level)

    # 计算整体状态
    if disabled:
        overall_status = "disabled"
    elif any(m["status"] == "cooling_down" for m in models.values()):
        overall_status = "cooling_down"
    else:
        overall_status = "active"

    return {
        "name": filename,
        "type": cred_type,
        "models": models,
        "max_backoff_level": max_backoff_level,
        "overall_status": overall_status,
    }


def _calculate_summary(backoff_status: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算总览统计"""
    total = len(backoff_status)
    active = sum(1 for s in backoff_status if s["overall_status"] == "active")
    cooling_down = sum(1 for s in backoff_status if s["overall_status"] == "cooling_down")
    disabled = sum(1 for s in backoff_status if s["overall_status"] == "disabled")

    total_backoff_level = sum(s["max_backoff_level"] for s in backoff_status)
    avg_backoff_level = total_backoff_level / total if total > 0 else 0.0
    max_backoff_level = max((s["max_backoff_level"] for s in backoff_status), default=0)

    return {
        "total_credentials": total,
        "active": active,
        "cooling_down": cooling_down,
        "disabled": disabled,
        "avg_backoff_level": round(avg_backoff_level, 2),
        "max_backoff_level": max_backoff_level,
    }
```

### 前端实现

#### 文件：`templates/backoff_status.html`

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>退避等级监控 - gcli2api</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
            overflow: hidden;
        }

        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .header h1 {
            font-size: 28px;
            font-weight: 600;
        }

        .refresh-btn {
            background: rgba(255, 255, 255, 0.2);
            border: 2px solid white;
            color: white;
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }

        .refresh-btn:hover {
            background: white;
            color: #667eea;
        }

        .summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
        }

        .summary-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            text-align: center;
        }

        .summary-card h3 {
            font-size: 14px;
            color: #6c757d;
            margin-bottom: 10px;
        }

        .summary-card .value {
            font-size: 32px;
            font-weight: 700;
            color: #667eea;
        }

        .credentials-list {
            padding: 30px;
        }

        .credential-item {
            background: white;
            border: 1px solid #e9ecef;
            border-radius: 8px;
            margin-bottom: 15px;
            overflow: hidden;
            transition: all 0.3s;
        }

        .credential-item:hover {
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        }

        .credential-header {
            padding: 15px 20px;
            background: #f8f9fa;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
        }

        .credential-name {
            font-weight: 600;
            font-size: 16px;
        }

        .status-badge {
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }

        .status-active {
            background: #d4edda;
            color: #155724;
        }

        .status-cooling {
            background: #fff3cd;
            color: #856404;
        }

        .status-disabled {
            background: #f8d7da;
            color: #721c24;
        }

        .models-list {
            padding: 15px 20px;
            display: none;
        }

        .models-list.show {
            display: block;
        }

        .model-item {
            padding: 10px;
            border-left: 3px solid #667eea;
            margin-bottom: 10px;
            background: #f8f9fa;
            border-radius: 4px;
        }

        .model-name {
            font-weight: 600;
            margin-bottom: 5px;
        }

        .model-details {
            font-size: 14px;
            color: #6c757d;
        }

        .backoff-level {
            display: inline-block;
            padding: 2px 8px;
            background: #667eea;
            color: white;
            border-radius: 12px;
            font-size: 12px;
            margin-left: 10px;
        }

        .backoff-level.high {
            background: #dc3545;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 退避等级监控</h1>
            <button class="refresh-btn" onclick="refreshData()">🔄 刷新</button>
        </div>

        <div class="summary" id="summary">
            <!-- 动态生成 -->
        </div>

        <div class="credentials-list" id="credentials-list">
            <!-- 动态生成 -->
        </div>
    </div>

    <script>
        async function loadData() {
            const password = prompt('请输入面板密码：');
            if (!password) return;

            try {
                const response = await fetch(`/api/credentials/backoff-status?password=${password}`);
                if (!response.ok) {
                    alert('密码错误或请求失败');
                    return;
                }

                const data = await response.json();
                renderSummary(data.summary);
                renderCredentials(data.backoff_status);
            } catch (error) {
                console.error('加载数据失败:', error);
                alert('加载数据失败');
            }
        }

        function renderSummary(summary) {
            const summaryDiv = document.getElementById('summary');
            summaryDiv.innerHTML = `
                <div class="summary-card">
                    <h3>总凭证数</h3>
                    <div class="value">${summary.total_credentials}</div>
                </div>
                <div class="summary-card">
                    <h3>活跃</h3>
                    <div class="value" style="color: #28a745;">${summary.active}</div>
                </div>
                <div class="summary-card">
                    <h3>冷却中</h3>
                    <div class="value" style="color: #ffc107;">${summary.cooling_down}</div>
                </div>
                <div class="summary-card">
                    <h3>已禁用</h3>
                    <div class="value" style="color: #dc3545;">${summary.disabled}</div>
                </div>
                <div class="summary-card">
                    <h3>平均退避等级</h3>
                    <div class="value">${summary.avg_backoff_level}</div>
                </div>
            `;
        }

        function renderCredentials(credentials) {
            const listDiv = document.getElementById('credentials-list');
            listDiv.innerHTML = credentials.map((cred, index) => `
                <div class="credential-item">
                    <div class="credential-header" onclick="toggleModels(${index})">
                        <div>
                            <span class="credential-name">${cred.name}</span>
                            <span style="color: #6c757d; font-size: 14px;"> [${cred.type}]</span>
                            ${cred.max_backoff_level > 0 ? `<span class="backoff-level ${cred.max_backoff_level >= 3 ? 'high' : ''}">退避等级 ${cred.max_backoff_level}</span>` : ''}
                        </div>
                        <span class="status-badge status-${cred.overall_status === 'active' ? 'active' : cred.overall_status === 'disabled' ? 'disabled' : 'cooling'}">
                            ${cred.overall_status === 'active' ? '🟢 活跃' : cred.overall_status === 'disabled' ? '🚫 已禁用' : '⏳ 冷却中'}
                        </span>
                    </div>
                    <div class="models-list" id="models-${index}">
                        ${Object.entries(cred.models).map(([modelKey, modelData]) => `
                            <div class="model-item">
                                <div class="model-name">${modelKey}</div>
                                <div class="model-details">
                                    退避等级: ${modelData.backoff_level} |
                                    状态: ${modelData.status === 'active' ? '✅ 活跃' : '⏳ 冷却中'}
                                    ${modelData.next_retry_after ? ` | 冷却至: ${modelData.next_retry_after}` : ''}
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `).join('');
        }

        function toggleModels(index) {
            const modelsDiv = document.getElementById(`models-${index}`);
            modelsDiv.classList.toggle('show');
        }

        function refreshData() {
            loadData();
        }

        // 页面加载时自动加载数据
        loadData();

        // 可选：每 30 秒自动刷新
        // setInterval(loadData, 30000);
    </script>
</body>
</html>
```

---

## 📝 实施步骤

### Step 1: 后端 API 开发（2 小时）

1. 在 `src/web_routes.py` 中添加 `/api/credentials/backoff-status` 路由
2. 实现 `_build_credential_status()` 和 `_calculate_summary()` 辅助函数
3. 添加单元测试验证 API 响应格式

### Step 2: 前端页面开发（2 小时）

1. 创建 `templates/backoff_status.html` 页面
2. 实现数据加载和渲染逻辑
3. 添加交互功能（展开/折叠、刷新）

### Step 3: 集成测试（1 小时）

1. 在测试环境验证功能
2. 测试不同状态下的显示效果
3. 验证密码保护功能

### Step 4: 文档更新（0.5 小时）

1. 更新用户手册，添加退避等级监控使用说明
2. 更新 API 文档

### Step 5: 部署（0.5 小时）

1. 合并代码到主分支
2. 部署到生产环境
3. 监控功能运行状态

---

## 🧪 测试计划

### 功能测试

| 测试项 | 测试步骤 | 预期结果 |
|--------|---------|---------|
| API 响应 | 调用 `/api/credentials/backoff-status` | 返回正确的 JSON 格式 |
| 密码验证 | 使用错误密码访问 | 返回 401 错误 |
| 状态计算 | 验证不同状态的凭证 | 状态标识正确 |
| 前端渲染 | 打开 Web 页面 | 正确显示所有凭证 |
| 刷新功能 | 点击刷新按钮 | 数据更新 |

### 性能测试

| 测试项 | 测试条件 | 性能指标 |
|--------|---------|---------|
| API 响应时间 | 100 个凭证 | < 500ms |
| 页面加载时间 | 100 个凭证 | < 1s |
| 内存占用 | 持续运行 1 小时 | 无内存泄漏 |

---

## 📈 预期收益

### 运维收益

| 收益项 | 说明 |
|--------|------|
| **故障排查效率** | 快速定位限流问题，缩短故障排查时间 50% |
| **主动监控** | 提前发现频繁失败的凭证，避免服务中断 |
| **数据可视化** | 直观展示退避等级，辅助决策 |

### 用户体验

| 收益项 | 说明 |
|--------|------|
| **透明度** | 用户可以了解限流状态，减少疑惑 |
| **信任度** | 专业的监控界面提升用户信任 |

---

## ⚠️ 注意事项

### 安全性

- ✅ 必须使用密码保护，防止未授权访问
- ✅ 不要在前端暴露敏感信息（如凭证内容）
- ✅ 使用 HTTPS 传输数据

### 性能

- ⚠️ 避免频繁刷新导致数据库压力
- ⚠️ 考虑添加缓存机制（如 Redis）
- ⚠️ 大量凭证时考虑分页加载

### 兼容性

- ✅ 确保与现有 Web 面板风格一致
- ✅ 支持移动端响应式布局
- ✅ 兼容主流浏览器（Chrome、Firefox、Safari、Edge）

---

## 📚 参考资料

| 资料 | 链接 |
|------|------|
| FastAPI 文档 | https://fastapi.tiangolo.com/ |
| Chart.js（可选图表库） | https://www.chartjs.org/ |
| Bootstrap（可选 UI 框架） | https://getbootstrap.com/ |

---

## 🎯 验收标准

### 功能验收

- ✅ API 返回正确的退避等级数据
- ✅ Web 页面正确显示所有凭证状态
- ✅ 刷新功能正常工作
- ✅ 密码保护功能有效

### 性能验收

- ✅ API 响应时间 < 500ms（100 个凭证）
- ✅ 页面加载时间 < 1s
- ✅ 无内存泄漏

### 用户体验验收

- ✅ 界面美观、易用
- ✅ 状态标识清晰
- ✅ 支持移动端访问

---

**创建者**: 浮浮酱 (Claude Opus 4.5) ฅ'ω'ฅ
**创建时间**: 2026-01-17
**优先级**: P2（低优先级）
**状态**: 📝 待实施

喵～退避等级监控的 TODO 文档已生成！(๑ˉ∀ˉ๑)
后续开发同学可以根据这个文档进行实施喵～
