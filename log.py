"""
日志模块 - 支持彩色输出、结构化日志和性能监控

颜色方案：
- DEBUG:    灰色 (dim) - 调试信息
- INFO:     白色 - 一般信息
- ROUTE:    青色 (cyan) - 路由决策
- FALLBACK: 黄色 (yellow) - 降级操作
- SUCCESS:  绿色 (green) - 成功操作
- WARNING:  橙色 - 警告
- ERROR:    红色 - 错误
- CRITICAL: 红色加粗 - 严重错误
- PERF:     紫色 - 性能监控

结构化日志：
- 设置 LOG_FORMAT=json 启用 JSON 格式输出
- 设置 LOG_FORMAT=text 使用传统文本格式（默认）
"""

import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, Optional


class Colors:
    """ANSI 颜色代码"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_MAGENTA = "\033[95m"


def _supports_color() -> bool:
    """检测终端是否支持颜色"""
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except:
            return os.getenv("WT_SESSION") is not None
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_color_enabled = _supports_color()

LOG_LEVELS = {
    "debug": 0,
    "info": 1,
    "route": 1,
    "success": 1,
    "fallback": 2,
    "perf": 2,  # 性能监控日志
    "warning": 3,
    "error": 4,
    "critical": 5
}

LOG_STYLES = {
    "debug":    (Colors.DIM + Colors.WHITE, "DEBUG"),
    "info":     (Colors.WHITE, "INFO"),
    "route":    (Colors.BRIGHT_CYAN, "ROUTE"),
    "success":  (Colors.BRIGHT_GREEN, "SUCCESS"),
    "fallback": (Colors.BRIGHT_YELLOW, "FALLBACK"),
    "perf":     (Colors.BRIGHT_MAGENTA, "PERF"),  # 性能监控
    "warning":  (Colors.YELLOW + Colors.BOLD, "WARNING"),
    "error":    (Colors.RED, "ERROR"),
    "critical": (Colors.BRIGHT_RED + Colors.BOLD, "CRITICAL"),
}

_file_lock = threading.Lock()
_file_writing_disabled = False
_disable_reason = None

# 结构化日志配置
_structured_log_enabled = os.getenv("LOG_FORMAT", "text").lower() == "json"

# 性能监控数据存储
_perf_metrics: Dict[str, list] = {}
_perf_lock = threading.Lock()

# 请求上下文（用于追踪 request_id）
_request_context = threading.local()


def set_request_id(request_id: str):
    """设置当前请求的 request_id（用于日志追踪）"""
    _request_context.request_id = request_id


def get_request_id() -> Optional[str]:
    """获取当前请求的 request_id"""
    return getattr(_request_context, "request_id", None)


def clear_request_id():
    """清除当前请求的 request_id"""
    if hasattr(_request_context, "request_id"):
        delattr(_request_context, "request_id")


def _get_current_log_level() -> int:
    level = os.getenv("LOG_LEVEL", "info").lower()
    return LOG_LEVELS.get(level, LOG_LEVELS["info"])


def _get_log_file_path() -> str:
    return os.getenv("LOG_FILE", "log.txt")


def _get_json_log_file_path() -> str:
    """获取 JSON 格式日志文件路径"""
    return os.getenv("LOG_FILE_JSON", "log.jsonl")


def _write_to_file(message: str):
    global _file_writing_disabled, _disable_reason
    if _file_writing_disabled:
        return
    try:
        log_file = _get_log_file_path()
        with _file_lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(message + "\n")
                f.flush()
    except (PermissionError, OSError, IOError) as e:
        _file_writing_disabled = True
        _disable_reason = str(e)
        print(f"Warning: Disabling log file writing: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to write to log file: {e}", file=sys.stderr)


def _write_json_to_file(log_entry: Dict[str, Any]):
    """写入 JSON 格式日志到文件"""
    global _file_writing_disabled
    if _file_writing_disabled:
        return
    try:
        log_file = _get_json_log_file_path()
        with _file_lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")
                f.flush()
    except Exception as e:
        print(f"Warning: Failed to write JSON log: {e}", file=sys.stderr)


def _colorize(text: str, color: str) -> str:
    if not _color_enabled:
        return text
    return f"{color}{text}{Colors.RESET}"


def _log(level: str, message: str, tag: Optional[str] = None, **extra):
    """
    核心日志函数，支持结构化日志
    
    Args:
        level: 日志级别
        message: 日志消息
        tag: 可选标签
        **extra: 额外的结构化字段（request_id, duration_ms, etc.）
    """
    level = level.lower()
    if level not in LOG_LEVELS:
        print(f"Warning: Unknown log level '{level}'", file=sys.stderr)
        return

    current_level = _get_current_log_level()
    if LOG_LEVELS[level] < current_level:
        return

    color, label = LOG_STYLES.get(level, (Colors.WHITE, level.upper()))
    now = datetime.now()
    timestamp = now.strftime("%H:%M:%S")
    iso_timestamp = now.isoformat()

    # 构建结构化日志条目
    log_entry = {
        "timestamp": iso_timestamp,
        "level": label,
        "message": message,
    }
    if tag:
        log_entry["tag"] = tag
    if extra:
        log_entry.update(extra)

    # 文本格式输出
    if tag:
        plain_entry = f"[{timestamp}] [{label}] [{tag}] {message}"
        colored_entry = (
            f"{Colors.DIM}[{timestamp}]{Colors.RESET} "
            f"{_colorize(f'[{label}]', color)} "
            f"{_colorize(f'[{tag}]', Colors.BRIGHT_MAGENTA)} "
            f"{message}"
        )
    else:
        plain_entry = f"[{timestamp}] [{label}] {message}"
        colored_entry = (
            f"{Colors.DIM}[{timestamp}]{Colors.RESET} "
            f"{_colorize(f'[{label}]', color)} "
            f"{message}"
        )

    # 添加额外字段到文本输出（如果有）
    if extra:
        extra_str = " ".join(f"{k}={v}" for k, v in extra.items())
        plain_entry += f" | {extra_str}"
        colored_entry += f" {Colors.DIM}| {extra_str}{Colors.RESET}"

    # 输出到控制台
    if _structured_log_enabled:
        # JSON 格式输出到控制台
        json_output = json.dumps(log_entry, ensure_ascii=False, default=str)
        if level in ("error", "critical"):
            print(json_output, file=sys.stderr)
        else:
            print(json_output)
    else:
        # 传统文本格式输出
        if level in ("error", "critical"):
            print(colored_entry if _color_enabled else plain_entry, file=sys.stderr)
        else:
            print(colored_entry if _color_enabled else plain_entry)

    # 写入文件
    _write_to_file(plain_entry)
    _write_json_to_file(log_entry)


def set_log_level(level: str) -> bool:
    level = level.lower()
    if level not in LOG_LEVELS:
        print(f"Warning: Unknown log level '{level}'. Valid: {', '.join(LOG_LEVELS.keys())}")
        return False
    print(f"Note: Set LOG_LEVEL={level} environment variable")
    return True


class Logger:
    """支持多种调用方式的日志器，包含结构化日志和性能监控"""

    def __call__(self, level: str, message: str, tag: Optional[str] = None, **extra):
        _log(level, message, tag, **extra)

    def debug(self, message: str, tag: Optional[str] = None, **extra):
        _log("debug", message, tag, **extra)

    def info(self, message: str, tag: Optional[str] = None, **extra):
        _log("info", message, tag, **extra)

    def route(self, message: str, tag: Optional[str] = None, **extra):
        _log("route", message, tag, **extra)

    def success(self, message: str, tag: Optional[str] = None, **extra):
        _log("success", message, tag, **extra)

    def fallback(self, message: str, tag: Optional[str] = None, **extra):
        _log("fallback", message, tag, **extra)

    def warning(self, message: str, tag: Optional[str] = None, **extra):
        _log("warning", message, tag, **extra)

    def error(self, message: str, tag: Optional[str] = None, **extra):
        _log("error", message, tag, **extra)

    def critical(self, message: str, tag: Optional[str] = None, **extra):
        _log("critical", message, tag, **extra)

    def perf(self, message: str, tag: Optional[str] = None, **extra):
        """性能监控日志"""
        _log("perf", message, tag, **extra)

    def get_current_level(self) -> str:
        current_level = _get_current_log_level()
        for name, value in LOG_LEVELS.items():
            if value == current_level:
                return name
        return "info"

    def get_log_file(self) -> str:
        return _get_log_file_path()

    def is_color_enabled(self) -> bool:
        return _color_enabled

    def set_color_enabled(self, enabled: bool):
        global _color_enabled
        _color_enabled = enabled

    def is_structured_enabled(self) -> bool:
        """检查是否启用结构化日志"""
        return _structured_log_enabled

    # ==================== 性能监控方法 ====================

    @contextmanager
    def timer(self, operation: str, tag: Optional[str] = None, **extra):
        """
        计时器上下文管理器，用于测量代码块执行时间
        
        Usage:
            with log.timer("api_call", request_id="123"):
                response = await api.call()
        """
        start_time = time.perf_counter()
        # 如果用户没有提供 request_id，生成一个
        request_id = extra.pop("request_id", str(uuid.uuid4())[:8])
        
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.perf(
                f"{operation} completed in {duration_ms:.2f}ms",
                tag=tag,
                operation=operation,
                duration_ms=round(duration_ms, 2),
                request_id=request_id,
                **extra
            )
            # 记录性能指标
            self._record_metric(operation, duration_ms)

    def timed(self, operation: Optional[str] = None, tag: Optional[str] = None):
        """
        计时器装饰器，用于测量函数执行时间
        
        Usage:
            @log.timed("process_request")
            async def process_request(data):
                ...
        """
        def decorator(func: Callable) -> Callable:
            op_name = operation or func.__name__
            
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = time.perf_counter()
                try:
                    return await func(*args, **kwargs)
                finally:
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    self.perf(
                        f"{op_name} completed in {duration_ms:.2f}ms",
                        tag=tag,
                        operation=op_name,
                        duration_ms=round(duration_ms, 2),
                        function=func.__name__
                    )
                    self._record_metric(op_name, duration_ms)
            
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = time.perf_counter()
                try:
                    return func(*args, **kwargs)
                finally:
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    self.perf(
                        f"{op_name} completed in {duration_ms:.2f}ms",
                        tag=tag,
                        operation=op_name,
                        duration_ms=round(duration_ms, 2),
                        function=func.__name__
                    )
                    self._record_metric(op_name, duration_ms)
            
            # 判断是否是异步函数
            import inspect
            if inspect.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper
        
        return decorator

    def _record_metric(self, operation: str, duration_ms: float):
        """记录性能指标到内存"""
        with _perf_lock:
            if operation not in _perf_metrics:
                _perf_metrics[operation] = []
            _perf_metrics[operation].append({
                "timestamp": datetime.now().isoformat(),
                "duration_ms": duration_ms
            })
            # 保留最近 1000 条记录
            if len(_perf_metrics[operation]) > 1000:
                _perf_metrics[operation] = _perf_metrics[operation][-1000:]

    def get_metrics(self, operation: Optional[str] = None) -> Dict[str, Any]:
        """
        获取性能指标统计
        
        Args:
            operation: 可选，指定操作名称。如果不指定，返回所有操作的统计
            
        Returns:
            包含统计信息的字典
        """
        with _perf_lock:
            if operation:
                if operation not in _perf_metrics:
                    return {"operation": operation, "count": 0}
                durations = [m["duration_ms"] for m in _perf_metrics[operation]]
                return self._calculate_stats(operation, durations)
            else:
                result = {}
                for op, metrics in _perf_metrics.items():
                    durations = [m["duration_ms"] for m in metrics]
                    result[op] = self._calculate_stats(op, durations)
                return result

    def _calculate_stats(self, operation: str, durations: list) -> Dict[str, Any]:
        """计算统计数据"""
        if not durations:
            return {"operation": operation, "count": 0}
        
        sorted_durations = sorted(durations)
        count = len(durations)
        
        return {
            "operation": operation,
            "count": count,
            "min_ms": round(min(durations), 2),
            "max_ms": round(max(durations), 2),
            "avg_ms": round(sum(durations) / count, 2),
            "p50_ms": round(sorted_durations[count // 2], 2),
            "p95_ms": round(sorted_durations[int(count * 0.95)], 2) if count >= 20 else None,
            "p99_ms": round(sorted_durations[int(count * 0.99)], 2) if count >= 100 else None,
        }

    def clear_metrics(self, operation: Optional[str] = None):
        """清除性能指标"""
        with _perf_lock:
            if operation:
                if operation in _perf_metrics:
                    del _perf_metrics[operation]
            else:
                _perf_metrics.clear()


log = Logger()

__all__ = [
    "log",
    "set_log_level",
    "LOG_LEVELS",
    "Colors",
    "set_request_id",
    "get_request_id",
    "clear_request_id",
]
