/**
 * Cursor API Hijack Patch
 *
 * 劫持 Cursor IDE 的第三方 API 请求，重定向到 gcli2api 网关服务器
 *
 * @version 1.0.0
 * @author 浮浮酱 (Claude Opus 4.5)
 * @date 2026-01-20
 */

(function() {
  'use strict';

  // ==================== 配置 ====================

  const CONFIG = {
    // 网关服务器地址
    gatewayUrl: 'http://127.0.0.1:8181',

    // 是否启用劫持
    enabled: true,

    // 是否启用调试日志
    debug: false,

    // 劫持的 URL 模式
    hijackPatterns: [
      /^https:\/\/api\.anthropic\.com\//,
      /^https:\/\/api\.openai\.com\//,
      /^https:\/\/.+\.anthropic\.com\/v1\/messages/,
    ],

    // URL 重写规则
    rewriteRules: [
      {
        pattern: /^https:\/\/api\.anthropic\.com\/v1\/messages/,
        replacement: '/v1/messages'
      },
      {
        pattern: /^https:\/\/api\.anthropic\.com\/(.*)/,
        replacement: '/$1'
      },
      {
        pattern: /^https:\/\/api\.openai\.com\/v1\/chat\/completions/,
        replacement: '/openai/v1/chat/completions'
      },
      {
        pattern: /^https:\/\/api\.openai\.com\/(.*)/,
        replacement: '/openai/$1'
      },
      {
        pattern: /^https:\/\/.+\.anthropic\.com\/v1\/messages/,
        replacement: '/v1/messages'
      }
    ],

    // 版本号
    version: '1.0.0'
  };

  // ==================== 工具函数 ====================

  function log(...args) {
    if (CONFIG.debug || window.__GCLI2API_DEBUG) {
      console.log('[GCLI2API Hijack]', ...args);
    }
  }

  function warn(...args) {
    console.warn('[GCLI2API Hijack]', ...args);
  }

  function error(...args) {
    console.error('[GCLI2API Hijack]', ...args);
  }

  // ==================== 第三方 API 检测 ====================

  /**
   * 检测 Cursor 是否启用了第三方 API 模式
   *
   * Cursor 的 "Use own API key" 设置会影响请求的目标地址
   * 当启用时，请求会发往用户配置的 API 端点
   */
  function isThirdPartyAPIEnabled() {
    // 方法 1: 检查 Cursor 全局配置
    if (typeof window !== 'undefined') {
      // Cursor 可能将设置存储在不同的位置
      const cursorSettings = window.__CURSOR_SETTINGS__ ||
                             window.cursorSettings ||
                             window._cursorConfig;

      if (cursorSettings?.useOwnAPIKey === true) {
        return true;
      }

      // 检查 localStorage
      try {
        const storedSettings = localStorage.getItem('cursor.settings');
        if (storedSettings) {
          const parsed = JSON.parse(storedSettings);
          if (parsed.useOwnAPIKey === true) {
            return true;
          }
        }
      } catch (e) {
        // 忽略解析错误
      }
    }

    // 方法 2: 检查请求头中是否包含用户自己的 API key
    // 这是一个后备检测方法，在实际请求时使用
    return false;
  }

  /**
   * 检查请求是否使用了用户自己的 API key
   */
  function hasUserAPIKey(options) {
    if (!options || !options.headers) {
      return false;
    }

    const headers = options.headers;

    // 检查 Anthropic API key
    const anthropicKey = headers['x-api-key'] || headers['X-Api-Key'];
    if (anthropicKey && !anthropicKey.startsWith('cursor-')) {
      return true;
    }

    // 检查 OpenAI API key
    const authHeader = headers['Authorization'] || headers['authorization'];
    if (authHeader && authHeader.startsWith('Bearer ') &&
        !authHeader.includes('cursor-')) {
      return true;
    }

    return false;
  }

  // ==================== URL 处理 ====================

  /**
   * 检查 URL 是否应该被劫持
   */
  function shouldHijack(url) {
    if (!CONFIG.enabled) {
      return false;
    }

    const urlString = typeof url === 'string' ? url : url.toString();

    for (const pattern of CONFIG.hijackPatterns) {
      if (pattern.test(urlString)) {
        return true;
      }
    }

    return false;
  }

  /**
   * 重写 URL 到网关地址
   */
  function rewriteUrl(url) {
    const urlString = typeof url === 'string' ? url : url.toString();

    for (const rule of CONFIG.rewriteRules) {
      if (rule.pattern.test(urlString)) {
        const newPath = urlString.replace(rule.pattern, rule.replacement);
        const newUrl = CONFIG.gatewayUrl + newPath;
        log(`URL 重写: ${urlString} -> ${newUrl}`);
        return newUrl;
      }
    }

    // 默认：直接替换域名
    const newUrl = urlString.replace(/^https?:\/\/[^\/]+/, CONFIG.gatewayUrl);
    log(`URL 重写 (默认): ${urlString} -> ${newUrl}`);
    return newUrl;
  }

  /**
   * 修改请求选项
   */
  function modifyOptions(options) {
    if (!options) {
      options = {};
    }

    // 复制 options 以避免修改原对象
    const newOptions = { ...options };

    // 确保 headers 存在
    if (!newOptions.headers) {
      newOptions.headers = {};
    } else if (newOptions.headers instanceof Headers) {
      // 转换 Headers 对象为普通对象
      const headersObj = {};
      newOptions.headers.forEach((value, key) => {
        headersObj[key] = value;
      });
      newOptions.headers = headersObj;
    } else {
      newOptions.headers = { ...newOptions.headers };
    }

    // 添加标识头，让网关知道这是来自 Cursor 的劫持请求
    newOptions.headers['X-Gcli2api-Hijack'] = 'cursor';
    newOptions.headers['X-Gcli2api-Version'] = CONFIG.version;

    // 保留原始目标地址（用于调试）
    if (options._originalUrl) {
      newOptions.headers['X-Gcli2api-Original-Url'] = options._originalUrl;
    }

    return newOptions;
  }

  // ==================== Fetch 劫持 ====================

  const originalFetch = window.fetch;

  window.fetch = async function(url, options = {}) {
    const urlString = typeof url === 'string' ? url : url.toString();

    // 检查是否应该劫持
    if (shouldHijack(urlString)) {
      // 检查是否使用了用户自己的 API key（第三方 API 模式）
      if (hasUserAPIKey(options) || isThirdPartyAPIEnabled()) {
        log(`劫持 fetch 请求: ${urlString}`);

        // 保存原始 URL
        options._originalUrl = urlString;

        // 重写 URL
        const newUrl = rewriteUrl(urlString);

        // 修改选项
        const newOptions = modifyOptions(options);

        try {
          const response = await originalFetch.call(this, newUrl, newOptions);
          log(`劫持请求完成: ${response.status}`);
          return response;
        } catch (err) {
          error(`劫持请求失败: ${err.message}`);
          // 如果网关不可用，尝试回退到原始请求
          warn('网关不可用，回退到原始请求');
          return originalFetch.call(this, url, options);
        }
      }
    }

    // 不劫持的请求，直接透传
    return originalFetch.call(this, url, options);
  };

  // ==================== XMLHttpRequest 劫持 ====================

  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;
  const originalXHRSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function(method, url, async, user, password) {
    const urlString = typeof url === 'string' ? url : url.toString();

    // 保存原始 URL 和方法
    this._gcli2api_originalUrl = urlString;
    this._gcli2api_method = method;
    this._gcli2api_headers = {};

    // 检查是否应该劫持
    if (shouldHijack(urlString)) {
      this._gcli2api_shouldHijack = true;
      const newUrl = rewriteUrl(urlString);
      log(`劫持 XHR 请求: ${urlString} -> ${newUrl}`);
      return originalXHROpen.call(this, method, newUrl, async, user, password);
    }

    this._gcli2api_shouldHijack = false;
    return originalXHROpen.call(this, method, url, async, user, password);
  };

  XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
    // 保存 header 用于后续检查
    if (this._gcli2api_headers) {
      this._gcli2api_headers[name] = value;
    }
    return originalXHRSetRequestHeader.call(this, name, value);
  };

  XMLHttpRequest.prototype.send = function(body) {
    // 如果需要劫持，添加额外的 headers
    if (this._gcli2api_shouldHijack) {
      // 检查是否使用了用户自己的 API key
      if (hasUserAPIKey({ headers: this._gcli2api_headers }) || isThirdPartyAPIEnabled()) {
        originalXHRSetRequestHeader.call(this, 'X-Gcli2api-Hijack', 'cursor');
        originalXHRSetRequestHeader.call(this, 'X-Gcli2api-Version', CONFIG.version);
        if (this._gcli2api_originalUrl) {
          originalXHRSetRequestHeader.call(this, 'X-Gcli2api-Original-Url', this._gcli2api_originalUrl);
        }
      }
    }

    return originalXHRSend.call(this, body);
  };

  // ==================== 全局 API ====================

  // 暴露版本号
  window.__GCLI2API_HIJACK_VERSION = CONFIG.version;

  // 暴露状态查询函数
  window.__GCLI2API_HIJACK_STATUS = function() {
    return {
      version: CONFIG.version,
      enabled: CONFIG.enabled,
      debug: CONFIG.debug || window.__GCLI2API_DEBUG,
      gatewayUrl: CONFIG.gatewayUrl,
      thirdPartyAPIEnabled: isThirdPartyAPIEnabled(),
      hijackPatterns: CONFIG.hijackPatterns.map(p => p.toString())
    };
  };

  // 暴露禁用函数
  window.__GCLI2API_DISABLE_HIJACK = function() {
    CONFIG.enabled = false;
    console.log('[GCLI2API Hijack] 已禁用');
  };

  // 暴露启用函数
  window.__GCLI2API_ENABLE_HIJACK = function() {
    CONFIG.enabled = true;
    console.log('[GCLI2API Hijack] 已启用');
  };

  // 暴露配置更新函数
  window.__GCLI2API_SET_GATEWAY = function(url) {
    CONFIG.gatewayUrl = url;
    console.log(`[GCLI2API Hijack] 网关地址已更新: ${url}`);
  };

  // ==================== 初始化完成 ====================

  console.log(`[GCLI2API Hijack] v${CONFIG.version} 已加载`);
  log('配置:', CONFIG);

})();
