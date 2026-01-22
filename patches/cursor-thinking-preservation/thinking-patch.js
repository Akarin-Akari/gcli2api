/**
 * Cursor Thinking Preservation Patch
 *
 * 修复 Cursor 的 fullConversationHeadersOnly 不包含 thinking 数据的问题
 *
 * 问题分析：
 * - Cursor 在发送请求时同时发送 `conversation` 和 `fullConversationHeadersOnly`
 * - `conversation` 包含完整的消息内容（包括 thinking + signature）
 * - `fullConversationHeadersOnly` 只包含 bubbleId, type, serverBubbleId
 * - 如果服务器使用 fullConversationHeadersOnly 恢复历史，thinking 会丢失
 *
 * 解决方案：
 * 1. 拦截 Cursor 构建 fullConversationHeadersOnly 的逻辑
 * 2. 在 fullConversationHeadersOnly 中注入 thinking 数据
 * 3. 或者确保服务器使用 conversation 而不是 fullConversationHeadersOnly
 *
 * @version 1.0.0
 * @author 浮浮酱 (Claude Opus 4.5)
 * @date 2026-01-20
 */

(function() {
  'use strict';

  // ==================== 配置 ====================

  const CONFIG = {
    // 是否启用补丁
    enabled: true,

    // 是否启用调试日志
    debug: false,

    // 版本号
    version: '1.0.0',

    // 补丁模式
    // 'inject': 在 fullConversationHeadersOnly 中注入 thinking 数据
    // 'remove': 移除 fullConversationHeadersOnly，强制服务器使用 conversation
    // 'monitor': 仅监控，不修改数据
    mode: 'inject'
  };

  // ==================== 工具函数 ====================

  function log(...args) {
    if (CONFIG.debug || window.__CURSOR_THINKING_PATCH_DEBUG) {
      console.log('[Cursor Thinking Patch]', ...args);
    }
  }

  function warn(...args) {
    console.warn('[Cursor Thinking Patch]', ...args);
  }

  function info(...args) {
    console.info('[Cursor Thinking Patch]', ...args);
  }

  // ==================== 核心补丁逻辑 ====================

  /**
   * 从 conversationMap 中提取 thinking 数据
   * 用于注入到 fullConversationHeadersOnly
   */
  function extractThinkingFromConversationMap(conversationMap, bubbleId) {
    if (!conversationMap || !bubbleId) {
      return null;
    }

    const message = conversationMap[bubbleId];
    if (!message || !message.thinking) {
      return null;
    }

    return {
      text: message.thinking.text || '',
      signature: message.thinking.signature || ''
    };
  }

  /**
   * 增强 fullConversationHeadersOnly 条目
   * 注入 thinking 数据
   */
  function enhanceHeadersOnlyEntry(entry, conversationMap) {
    if (!entry || !entry.bubbleId) {
      return entry;
    }

    const thinking = extractThinkingFromConversationMap(conversationMap, entry.bubbleId);
    if (!thinking) {
      return entry;
    }

    // 创建增强后的条目
    const enhanced = {
      ...entry,
      // 注入 thinking 数据
      thinking: thinking
    };

    log(`增强 headersOnly 条目: bubbleId=${entry.bubbleId}, thinking_len=${thinking.text.length}, has_signature=${!!thinking.signature}`);

    return enhanced;
  }

  /**
   * 处理请求数据
   * 根据配置模式修改 fullConversationHeadersOnly
   */
  function processRequestData(data, conversationMap) {
    if (!data) {
      return data;
    }

    // 检查是否有 fullConversationHeadersOnly
    if (!data.fullConversationHeadersOnly || !Array.isArray(data.fullConversationHeadersOnly)) {
      return data;
    }

    const headersOnly = data.fullConversationHeadersOnly;
    log(`处理请求: fullConversationHeadersOnly 条目数=${headersOnly.length}`);

    switch (CONFIG.mode) {
      case 'inject':
        // 模式 1: 在 fullConversationHeadersOnly 中注入 thinking 数据
        data.fullConversationHeadersOnly = headersOnly.map(entry =>
          enhanceHeadersOnlyEntry(entry, conversationMap)
        );
        info(`已注入 thinking 数据到 ${headersOnly.length} 个 headersOnly 条目`);
        break;

      case 'remove':
        // 模式 2: 移除 fullConversationHeadersOnly，强制使用 conversation
        delete data.fullConversationHeadersOnly;
        info('已移除 fullConversationHeadersOnly，服务器将使用 conversation');
        break;

      case 'monitor':
        // 模式 3: 仅监控，记录日志
        let thinkingCount = 0;
        let signatureCount = 0;

        if (conversationMap) {
          headersOnly.forEach(entry => {
            const thinking = extractThinkingFromConversationMap(conversationMap, entry.bubbleId);
            if (thinking) {
              thinkingCount++;
              if (thinking.signature) {
                signatureCount++;
              }
            }
          });
        }

        info(`监控: headersOnly=${headersOnly.length}, 有thinking=${thinkingCount}, 有signature=${signatureCount}`);
        break;

      default:
        warn(`未知模式: ${CONFIG.mode}`);
    }

    return data;
  }

  // ==================== Protobuf 拦截 ====================

  /**
   * 拦截 protobuf 序列化
   *
   * Cursor 使用 protobuf-es 进行消息序列化
   * 我们需要在序列化之前修改数据
   */
  function interceptProtobufSerialization() {
    // 查找 protobuf 相关的全局对象
    // Cursor 可能使用 @bufbuild/protobuf 或类似库

    // 方法 1: 拦截 JSON.stringify（用于调试和某些场景）
    const originalStringify = JSON.stringify;
    JSON.stringify = function(value, replacer, space) {
      // 检查是否是 Cursor 的请求数据
      if (value && typeof value === 'object') {
        if (value.fullConversationHeadersOnly && value.conversation) {
          log('检测到 Cursor 请求数据（JSON）');
          // 尝试从 conversation 中提取 conversationMap
          const conversationMap = buildConversationMap(value.conversation);
          value = processRequestData(value, conversationMap);
        }
      }
      return originalStringify.call(this, value, replacer, space);
    };

    log('已拦截 JSON.stringify');
  }

  /**
   * 从 conversation 数组构建 conversationMap
   */
  function buildConversationMap(conversation) {
    if (!Array.isArray(conversation)) {
      return {};
    }

    const map = {};
    conversation.forEach(msg => {
      if (msg && msg.bubbleId) {
        map[msg.bubbleId] = msg;
      }
    });

    return map;
  }

  // ==================== Fetch 拦截 ====================

  /**
   * 拦截 fetch 请求
   * 在请求发送前修改 body
   */
  function interceptFetch() {
    const originalFetch = window.fetch;

    window.fetch = async function(url, options = {}) {
      // 检查是否是 Cursor 的 AI 请求
      const urlString = typeof url === 'string' ? url : url.toString();

      if (isCursorAIRequest(urlString) && options.body) {
        try {
          let body = options.body;

          // 处理不同类型的 body
          if (typeof body === 'string') {
            const data = JSON.parse(body);
            if (data.fullConversationHeadersOnly) {
              const conversationMap = buildConversationMap(data.conversation);
              const processedData = processRequestData(data, conversationMap);
              options.body = JSON.stringify(processedData);
              log('已处理 fetch 请求 body');
            }
          }
        } catch (e) {
          // 解析失败，可能是二进制数据（protobuf）
          log('fetch body 不是 JSON，可能是 protobuf');
        }
      }

      return originalFetch.call(this, url, options);
    };

    log('已拦截 fetch');
  }

  /**
   * 检查是否是 Cursor 的 AI 请求
   */
  function isCursorAIRequest(url) {
    // Cursor 的 AI 请求通常发往这些端点
    const patterns = [
      /\/aiserver\./,
      /\/api\/chat/,
      /\/v1\/messages/,
      /StreamUnifiedChatRequest/,
      /cursor\.sh/,
      /cursorapi/
    ];

    return patterns.some(pattern => pattern.test(url));
  }

  // ==================== 全局存储拦截 ====================

  /**
   * 拦截 Cursor 的状态存储
   * 监控 conversationMap 的变化
   */
  function interceptStateStore() {
    // Cursor 使用 zustand 或类似的状态管理
    // 我们需要监控 conversationMap 的变化

    // 保存对 conversationMap 的引用
    let cachedConversationMap = null;

    // 定期检查全局状态
    setInterval(() => {
      try {
        // 尝试从 Cursor 的全局状态中获取 conversationMap
        // 这需要根据 Cursor 的实际实现来调整
        const stores = findCursorStores();
        if (stores && stores.conversationMap) {
          cachedConversationMap = stores.conversationMap;
          log('已缓存 conversationMap');
        }
      } catch (e) {
        // 忽略错误
      }
    }, 5000);

    // 暴露获取 conversationMap 的方法
    window.__CURSOR_THINKING_PATCH_GET_CONVERSATION_MAP = () => cachedConversationMap;
  }

  /**
   * 查找 Cursor 的状态存储
   */
  function findCursorStores() {
    // 这是一个启发式方法，需要根据 Cursor 的实际实现来调整
    // Cursor 可能将状态存储在不同的位置

    // 方法 1: 检查 window 上的全局变量
    if (window.__CURSOR_STORES__) {
      return window.__CURSOR_STORES__;
    }

    // 方法 2: 检查 React DevTools 暴露的状态
    if (window.__REACT_DEVTOOLS_GLOBAL_HOOK__) {
      // 尝试从 React 组件树中提取状态
      // 这比较复杂，暂时跳过
    }

    return null;
  }

  // ==================== 初始化 ====================

  function initialize() {
    if (!CONFIG.enabled) {
      log('补丁已禁用');
      return;
    }

    log('初始化补丁...');

    // 拦截各种可能的数据传输方式
    interceptProtobufSerialization();
    interceptFetch();
    interceptStateStore();

    info(`Cursor Thinking Preservation Patch v${CONFIG.version} 已加载`);
    info(`模式: ${CONFIG.mode}`);
  }

  // ==================== 全局 API ====================

  // 暴露版本号
  window.__CURSOR_THINKING_PATCH_VERSION = CONFIG.version;

  // 暴露状态查询函数
  window.__CURSOR_THINKING_PATCH_STATUS = function() {
    return {
      version: CONFIG.version,
      enabled: CONFIG.enabled,
      debug: CONFIG.debug || window.__CURSOR_THINKING_PATCH_DEBUG,
      mode: CONFIG.mode
    };
  };

  // 暴露模式切换函数
  window.__CURSOR_THINKING_PATCH_SET_MODE = function(mode) {
    if (['inject', 'remove', 'monitor'].includes(mode)) {
      CONFIG.mode = mode;
      info(`模式已切换为: ${mode}`);
    } else {
      warn(`无效模式: ${mode}，可选值: inject, remove, monitor`);
    }
  };

  // 暴露禁用函数
  window.__CURSOR_THINKING_PATCH_DISABLE = function() {
    CONFIG.enabled = false;
    info('补丁已禁用');
  };

  // 暴露启用函数
  window.__CURSOR_THINKING_PATCH_ENABLE = function() {
    CONFIG.enabled = true;
    info('补丁已启用');
  };

  // ==================== 启动 ====================

  // 等待 DOM 加载完成后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }

})();
