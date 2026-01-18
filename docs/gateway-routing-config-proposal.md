# 网关路由配置方案 - Web 端手动切换

## 需求分析

用户希望在 web 控制面板中能够：
1. **查看所有可用的后端服务**（Antigravity、Copilot、Kiro Gateway）
2. **为每个模型手动指定后端**（覆盖自动路由逻辑）
3. **设置后端优先级**（影响故障转移顺序）
4. **启用/禁用特定后端**
5. **实时生效**（无需重启服务）

## 实现方案

### 1. 数据结构设计

#### 配置存储格式（JSON）

```json
{
  "gateway_routing": {
    "model_backend_map": {
      "claude-3-opus": "kiro-gateway",
      "claude-3.5-sonnet": "antigravity",
      "gpt-4": "copilot"
    },
    "backend_priority": {
      "antigravity": 1,
      "copilot": 2,
      "kiro-gateway": 3
    },
    "backend_enabled": {
      "antigravity": true,
      "copilot": true,
      "kiro-gateway": true
    }
  }
}
```

### 2. 后端实现

#### 2.1 添加配置读取函数（`config.py`）

```python
async def get_gateway_routing_config() -> Dict[str, Any]:
    """获取网关路由配置"""
    storage_adapter = await get_storage_adapter()
    routing_config = await storage_adapter.get_config("gateway_routing", {})
    
    # 默认配置
    default_config = {
        "model_backend_map": {},
        "backend_priority": {
            "antigravity": 1,
            "copilot": 2,
            "kiro-gateway": 3
        },
        "backend_enabled": {
            "antigravity": True,
            "copilot": True,
            "kiro-gateway": True
        }
    }
    
    # 合并默认配置
    if not routing_config:
        return default_config
    
    # 确保所有字段都存在
    for key in default_config:
        if key not in routing_config:
            routing_config[key] = default_config[key]
    
    return routing_config

async def set_gateway_routing_config(config: Dict[str, Any]) -> bool:
    """保存网关路由配置"""
    storage_adapter = await get_storage_adapter()
    return await storage_adapter.set_config("gateway_routing", config)
```

#### 2.2 修改路由逻辑（`unified_gateway_router.py`）

```python
async def get_backend_for_model(model: str) -> Optional[str]:
    """
    根据模型名称获取指定后端（支持用户配置覆盖）
    
    优先级：
    1. 用户配置的模型-后端映射（最高优先级）
    2. Kiro Gateway 环境变量配置
    3. Antigravity 支持检查
    4. 默认 Copilot
    """
    if not model:
        model = ""
    
    model_lower = model.lower()
    
    # 1. 优先检查用户配置的模型-后端映射
    try:
        from config import get_gateway_routing_config
        routing_config = await get_gateway_routing_config()
        model_backend_map = routing_config.get("model_backend_map", {})
        
        # 精确匹配
        if model_lower in model_backend_map:
            backend = model_backend_map[model_lower]
            log.route(f"Model {model} -> {backend} (user configured)", tag="GATEWAY")
            return backend
        
        # 模糊匹配（规范化模型名）
        normalized_model = normalize_model_name(model)
        for configured_model, backend in model_backend_map.items():
            if normalized_model == normalize_model_name(configured_model):
                log.route(f"Model {model} -> {backend} (user configured, normalized)", tag="GATEWAY")
                return backend
    except Exception as e:
        log.warning(f"Failed to load gateway routing config: {e}", tag="GATEWAY")
    
    # 2. 检查 Kiro Gateway 环境变量配置（保持向后兼容）
    if KIRO_GATEWAY_MODELS:
        if model_lower in KIRO_GATEWAY_MODELS:
            log.route(f"Model {model} -> Kiro Gateway (env configured)", tag="GATEWAY")
            return "kiro-gateway"
        
        normalized_model = normalize_model_name(model)
        for kiro_model in KIRO_GATEWAY_MODELS:
            if normalized_model == kiro_model.lower() or normalized_model.startswith(kiro_model.lower()):
                log.route(f"Model {model} -> Kiro Gateway (env pattern match)", tag="GATEWAY")
                return "kiro-gateway"
    
    # 3. 检查 Antigravity 支持
    if is_antigravity_supported(model):
        log.route(f"Model {model} -> Antigravity", tag="GATEWAY")
        return "antigravity"
    else:
        log.route(f"Model {model} -> Copilot (default)", tag="GATEWAY")
        return "copilot"
```

#### 2.3 动态更新后端配置

```python
def get_sorted_backends() -> List[Tuple[str, Dict]]:
    """获取按优先级排序的后端列表（支持动态配置）"""
    # 从配置读取优先级和启用状态
    routing_config = asyncio.run(get_gateway_routing_config()) if asyncio.iscoroutinefunction(get_gateway_routing_config) else {}
    
    backend_priority = routing_config.get("backend_priority", {})
    backend_enabled = routing_config.get("backend_enabled", {})
    
    # 应用配置到 BACKENDS
    enabled_backends = []
    for key, backend in BACKENDS.items():
        # 更新优先级
        if key in backend_priority:
            backend["priority"] = backend_priority[key]
        
        # 检查启用状态
        if key in backend_enabled:
            backend["enabled"] = backend_enabled[key]
        
        if backend.get("enabled", True):
            enabled_backends.append((key, backend))
    
    return sorted(enabled_backends, key=lambda x: x[1]["priority"])
```

#### 2.4 添加 Web API 端点（`web_routes.py`）

```python
@router.get("/gateway/routing/get")
async def get_gateway_routing_config(token: str = Depends(verify_panel_token)):
    """获取网关路由配置"""
    try:
        from config import get_gateway_routing_config
        from src.unified_gateway_router import BACKENDS
        
        routing_config = await get_gateway_routing_config()
        
        # 获取所有可用后端信息
        backends_info = {}
        for key, backend in BACKENDS.items():
            backends_info[key] = {
                "name": backend["name"],
                "base_url": backend["base_url"],
                "priority": routing_config.get("backend_priority", {}).get(key, backend["priority"]),
                "enabled": routing_config.get("backend_enabled", {}).get(key, backend.get("enabled", True)),
            }
        
        return JSONResponse(content={
            "routing_config": routing_config,
            "available_backends": backends_info,
            "current_backends": BACKENDS
        })
    except Exception as e:
        log.error(f"获取网关路由配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/gateway/routing/save")
async def save_gateway_routing_config(
    request: Request,
    token: str = Depends(verify_panel_token)
):
    """保存网关路由配置"""
    try:
        from config import set_gateway_routing_config
        from src.unified_gateway_router import BACKENDS
        
        data = await request.json()
        routing_config = data.get("routing_config", {})
        
        # 验证配置格式
        if "model_backend_map" in routing_config:
            model_backend_map = routing_config["model_backend_map"]
            if not isinstance(model_backend_map, dict):
                raise HTTPException(status_code=400, detail="model_backend_map 必须是字典")
            
            # 验证后端名称
            valid_backends = set(BACKENDS.keys())
            for model, backend in model_backend_map.items():
                if backend not in valid_backends:
                    raise HTTPException(
                        status_code=400,
                        detail=f"无效的后端名称: {backend}，可用后端: {', '.join(valid_backends)}"
                    )
        
        if "backend_priority" in routing_config:
            backend_priority = routing_config["backend_priority"]
            if not isinstance(backend_priority, dict):
                raise HTTPException(status_code=400, detail="backend_priority 必须是字典")
            
            # 验证优先级值
            for backend, priority in backend_priority.items():
                if backend not in BACKENDS:
                    raise HTTPException(status_code=400, detail=f"无效的后端: {backend}")
                if not isinstance(priority, int) or priority < 1:
                    raise HTTPException(status_code=400, detail=f"优先级必须是大于0的整数: {backend}")
        
        if "backend_enabled" in routing_config:
            backend_enabled = routing_config["backend_enabled"]
            if not isinstance(backend_enabled, dict):
                raise HTTPException(status_code=400, detail="backend_enabled 必须是字典")
        
        # 保存配置
        success = await set_gateway_routing_config(routing_config)
        
        if success:
            # 重新加载配置（使配置立即生效）
            from config import reload_config
            await reload_config()
            
            return JSONResponse(content={
                "message": "网关路由配置保存成功",
                "routing_config": routing_config
            })
        else:
            raise HTTPException(status_code=500, detail="保存配置失败")
    
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"保存网关路由配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

### 3. 前端实现

#### 3.1 在控制面板添加新标签页

在 `control_panel.html` 中添加：

```html
<button class="tab" onclick="switchTab('gateway-routing')">网关路由</button>
```

#### 3.2 添加网关路由配置界面

```html
<div id="gatewayRoutingSection" class="tab-content hidden">
    <h3>网关路由配置</h3>
    <p>配置模型到后端的映射关系，以及后端优先级和启用状态</p>
    
    <!-- 后端管理 -->
    <div class="config-group">
        <h4>后端服务管理</h4>
        <div id="backendsList"></div>
    </div>
    
    <!-- 模型-后端映射 -->
    <div class="config-group">
        <h4>模型路由配置</h4>
        <div class="form-group">
            <label>添加模型路由规则：</label>
            <div style="display: flex; gap: 10px; margin-bottom: 10px;">
                <input type="text" id="newModelName" placeholder="模型名称（如: claude-3-opus）" style="flex: 1;" />
                <select id="newBackend" style="flex: 1;">
                    <option value="">选择后端</option>
                </select>
                <button onclick="addModelRoute()">添加</button>
            </div>
        </div>
        <div id="modelRoutesList"></div>
    </div>
    
    <div class="form-actions">
        <button onclick="saveGatewayRouting()" class="btn-primary">保存配置</button>
        <button onclick="resetGatewayRouting()" class="btn-secondary">重置为默认</button>
    </div>
</div>
```

#### 3.3 JavaScript 实现（`common.js`）

```javascript
let gatewayRoutingConfig = {
    model_backend_map: {},
    backend_priority: {},
    backend_enabled: {}
};

let availableBackends = {};

// 加载网关路由配置
async function loadGatewayRoutingConfig() {
    try {
        const response = await fetch('./gateway/routing/get', {
            headers: getAuthHeaders()
        });
        const data = await response.json();
        
        if (response.ok) {
            gatewayRoutingConfig = data.routing_config || {
                model_backend_map: {},
                backend_priority: {},
                backend_enabled: {}
            };
            availableBackends = data.available_backends || {};
            
            renderBackendsList();
            renderModelRoutesList();
            populateBackendSelect();
        } else {
            showStatus(`加载配置失败: ${data.detail || data.error}`, 'error');
        }
    } catch (error) {
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

// 渲染后端列表
function renderBackendsList() {
    const container = document.getElementById('backendsList');
    if (!container) return;
    
    container.innerHTML = '';
    
    for (const [key, backend] of Object.entries(availableBackends)) {
        const div = document.createElement('div');
        div.className = 'form-group';
        div.style.display = 'flex';
        div.style.alignItems = 'center';
        div.style.gap = '10px';
        div.style.marginBottom = '10px';
        
        div.innerHTML = `
            <label style="min-width: 150px;">${backend.name}:</label>
            <input type="number" 
                   id="priority_${key}" 
                   value="${backend.priority}" 
                   min="1" 
                   style="width: 80px;"
                   onchange="updateBackendPriority('${key}', this.value)" />
            <label style="min-width: 60px;">优先级</label>
            <input type="checkbox" 
                   id="enabled_${key}" 
                   ${backend.enabled ? 'checked' : ''}
                   onchange="updateBackendEnabled('${key}', this.checked)" />
            <label>启用</label>
            <span style="color: #666; font-size: 12px;">${backend.base_url}</span>
        `;
        
        container.appendChild(div);
    }
}

// 渲染模型路由列表
function renderModelRoutesList() {
    const container = document.getElementById('modelRoutesList');
    if (!container) return;
    
    container.innerHTML = '';
    
    if (Object.keys(gatewayRoutingConfig.model_backend_map || {}).length === 0) {
        container.innerHTML = '<p style="color: #666;">暂无自定义路由规则，将使用默认路由逻辑</p>';
        return;
    }
    
    const table = document.createElement('table');
    table.className = 'config-table';
    table.innerHTML = `
        <thead>
            <tr>
                <th>模型名称</th>
                <th>后端服务</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody id="modelRoutesTableBody"></tbody>
    `;
    
    const tbody = table.querySelector('#modelRoutesTableBody');
    for (const [model, backend] of Object.entries(gatewayRoutingConfig.model_backend_map || {})) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${model}</td>
            <td>${availableBackends[backend]?.name || backend}</td>
            <td>
                <button onclick="removeModelRoute('${model}')" class="btn-danger">删除</button>
            </td>
        `;
        tbody.appendChild(tr);
    }
    
    container.appendChild(table);
}

// 填充后端选择下拉框
function populateBackendSelect() {
    const select = document.getElementById('newBackend');
    if (!select) return;
    
    select.innerHTML = '<option value="">选择后端</option>';
    for (const [key, backend] of Object.entries(availableBackends)) {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = `${backend.name} (${key})`;
        select.appendChild(option);
    }
}

// 添加模型路由
function addModelRoute() {
    const modelInput = document.getElementById('newModelName');
    const backendSelect = document.getElementById('newBackend');
    
    if (!modelInput || !backendSelect) return;
    
    const model = modelInput.value.trim().toLowerCase();
    const backend = backendSelect.value;
    
    if (!model) {
        showStatus('请输入模型名称', 'error');
        return;
    }
    
    if (!backend) {
        showStatus('请选择后端服务', 'error');
        return;
    }
    
    if (!gatewayRoutingConfig.model_backend_map) {
        gatewayRoutingConfig.model_backend_map = {};
    }
    
    gatewayRoutingConfig.model_backend_map[model] = backend;
    
    modelInput.value = '';
    backendSelect.value = '';
    
    renderModelRoutesList();
    showStatus('路由规则已添加（请点击保存生效）', 'success');
}

// 删除模型路由
function removeModelRoute(model) {
    if (gatewayRoutingConfig.model_backend_map) {
        delete gatewayRoutingConfig.model_backend_map[model];
        renderModelRoutesList();
        showStatus('路由规则已删除（请点击保存生效）', 'success');
    }
}

// 更新后端优先级
function updateBackendPriority(backend, priority) {
    if (!gatewayRoutingConfig.backend_priority) {
        gatewayRoutingConfig.backend_priority = {};
    }
    gatewayRoutingConfig.backend_priority[backend] = parseInt(priority);
}

// 更新后端启用状态
function updateBackendEnabled(backend, enabled) {
    if (!gatewayRoutingConfig.backend_enabled) {
        gatewayRoutingConfig.backend_enabled = {};
    }
    gatewayRoutingConfig.backend_enabled[backend] = enabled;
}

// 保存网关路由配置
async function saveGatewayRouting() {
    try {
        const response = await fetch('./gateway/routing/save', {
            method: 'POST',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                routing_config: gatewayRoutingConfig
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showStatus('网关路由配置保存成功，已立即生效', 'success');
        } else {
            showStatus(`保存失败: ${data.detail || data.error}`, 'error');
        }
    } catch (error) {
        showStatus(`网络错误: ${error.message}`, 'error');
    }
}

// 重置为默认配置
function resetGatewayRouting() {
    if (confirm('确定要重置为默认配置吗？')) {
        gatewayRoutingConfig = {
            model_backend_map: {},
            backend_priority: {
                antigravity: 1,
                copilot: 2,
                'kiro-gateway': 3
            },
            backend_enabled: {
                antigravity: true,
                copilot: true,
                'kiro-gateway': true
            }
        };
        loadGatewayRoutingConfig();
        showStatus('已重置为默认配置', 'success');
    }
}

// 在切换标签页时加载配置
function switchTab(tabName) {
    // ... 现有的切换逻辑 ...
    
    if (tabName === 'gateway-routing') {
        loadGatewayRoutingConfig();
    }
}
```

## 实现步骤

1. **后端实现**（优先级：高）
   - [ ] 在 `config.py` 添加 `get_gateway_routing_config` 和 `set_gateway_routing_config`
   - [ ] 修改 `unified_gateway_router.py` 的 `get_backend_for_model` 函数
   - [ ] 修改 `get_sorted_backends` 函数支持动态配置
   - [ ] 在 `web_routes.py` 添加 `/gateway/routing/get` 和 `/gateway/routing/save` 端点

2. **前端实现**（优先级：中）
   - [ ] 在 `control_panel.html` 添加"网关路由"标签页
   - [ ] 在 `common.js` 添加网关路由配置相关函数
   - [ ] 添加样式优化界面

3. **测试验证**（优先级：高）
   - [ ] 测试配置保存和读取
   - [ ] 测试路由逻辑是否正确应用
   - [ ] 测试实时生效（无需重启）
   - [ ] 测试故障转移逻辑

## 优势

1. **灵活性**：用户可以完全自定义模型到后端的映射
2. **实时生效**：配置保存后立即生效，无需重启
3. **向后兼容**：保留环境变量配置方式
4. **健壮性**：配置验证和错误处理
5. **易用性**：Web 界面直观易用

## 注意事项

1. **配置优先级**：用户配置 > 环境变量 > 默认逻辑
2. **配置验证**：确保后端名称有效，优先级为正整数
3. **性能考虑**：配置读取使用内存缓存，避免频繁数据库查询
4. **并发安全**：配置更新时需要考虑并发访问
