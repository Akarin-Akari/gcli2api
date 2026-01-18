"""
Async Write Queue - Background writer for L2 cache persistence
异步写入队列 - 后台写入器用于 L2 缓存持久化

This module provides:
    - Background thread for async L2 writes
    - Batch commit optimization
    - Retry mechanism with exponential backoff
    - Graceful shutdown with queue draining
    - Queue overflow protection

Architecture:
    Producer (cache_signature) -> Queue -> Consumer (background thread) -> L2 SQLite
"""

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# 支持多种导入方式 - log.py 在 gcli2api/ 目录下
import sys
import os as _os
_cache_dir = _os.path.dirname(_os.path.abspath(__file__))  # cache/
_src_dir = _os.path.dirname(_cache_dir)  # src/
_project_dir = _os.path.dirname(_src_dir)  # gcli2api/
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)
from log import log

from .cache_interface import CacheEntry


class QueueState(Enum):
    """Queue state enumeration"""
    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"
    DRAINING = "draining"


@dataclass
class WriteTask:
    """
    Write task for async queue
    异步队列写入任务

    Attributes:
        entry: Cache entry to write
        retry_count: Number of retries attempted
        created_at: When task was created
        priority: Task priority (lower = higher priority)
    """
    entry: CacheEntry
    retry_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    priority: int = 0

    def __lt__(self, other: "WriteTask") -> bool:
        """For priority queue ordering"""
        return self.priority < other.priority


@dataclass
class QueueStats:
    """
    Queue statistics
    队列统计信息
    """
    total_enqueued: int = 0
    total_processed: int = 0
    total_failed: int = 0
    total_retried: int = 0
    total_dropped: int = 0
    current_queue_size: int = 0
    batch_count: int = 0
    avg_batch_size: float = 0.0
    last_flush_time: Optional[datetime] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "total_enqueued": self.total_enqueued,
            "total_processed": self.total_processed,
            "total_failed": self.total_failed,
            "total_retried": self.total_retried,
            "total_dropped": self.total_dropped,
            "current_queue_size": self.current_queue_size,
            "batch_count": self.batch_count,
            "avg_batch_size": self.avg_batch_size,
            "last_flush_time": self.last_flush_time.isoformat() if self.last_flush_time else None,
            "last_error": self.last_error,
            "success_rate": self._calculate_success_rate(),
        }

    def _calculate_success_rate(self) -> float:
        """Calculate success rate"""
        total = self.total_processed + self.total_failed
        if total == 0:
            return 1.0
        return self.total_processed / total


@dataclass
class AsyncWriteConfig:
    """
    Configuration for async write queue
    异步写入队列配置

    Attributes:
        max_queue_size: Maximum queue size (0 = unlimited)
        batch_size: Number of entries to batch before writing
        batch_timeout_ms: Maximum wait time before flushing batch
        max_retries: Maximum retry attempts for failed writes
        retry_delay_ms: Initial delay between retries (exponential backoff)
        worker_threads: Number of worker threads
        drop_on_overflow: Whether to drop entries when queue is full
    """
    max_queue_size: int = 10000
    batch_size: int = 100
    batch_timeout_ms: int = 1000
    max_retries: int = 3
    retry_delay_ms: int = 100
    worker_threads: int = 1
    drop_on_overflow: bool = True


class AsyncWriteQueue:
    """
    Async Write Queue for L2 cache persistence
    异步写入队列 - 用于 L2 缓存持久化

    Features:
        - Background thread for non-blocking writes
        - Batch commit optimization
        - Retry mechanism with exponential backoff
        - Graceful shutdown with queue draining
        - Queue overflow protection

    Usage:
        from .signature_database import SignatureDatabase

        db = SignatureDatabase()
        config = AsyncWriteConfig(batch_size=50, batch_timeout_ms=500)
        queue = AsyncWriteQueue(db, config)

        # Start processing
        queue.start()

        # Enqueue entries
        queue.enqueue(entry)

        # Graceful shutdown
        queue.stop(wait=True)
    """

    def __init__(
        self,
        l2_cache: Any,  # SignatureDatabase
        config: Optional[AsyncWriteConfig] = None
    ):
        """
        Initialize AsyncWriteQueue

        Args:
            l2_cache: L2 cache layer (SignatureDatabase)
            config: Queue configuration
        """
        self.config = config or AsyncWriteConfig()
        self._l2_cache = l2_cache

        # Task queue
        if self.config.max_queue_size > 0:
            self._queue: queue.Queue[WriteTask] = queue.Queue(maxsize=self.config.max_queue_size)
        else:
            self._queue: queue.Queue[WriteTask] = queue.Queue()

        # State management
        self._state = QueueState.STOPPED
        self._state_lock = threading.Lock()

        # Worker threads
        self._workers: List[threading.Thread] = []

        # Batch buffer
        self._batch_buffer: List[WriteTask] = []
        self._batch_lock = threading.Lock()
        self._last_flush_time = time.time()

        # Statistics
        self._stats = QueueStats()
        self._stats_lock = threading.Lock()

        # Shutdown event
        self._shutdown_event = threading.Event()

        log.info(f"[ASYNC_QUEUE] Initialized with batch_size={self.config.batch_size}, "
                f"timeout={self.config.batch_timeout_ms}ms, max_queue={self.config.max_queue_size}")

    @property
    def state(self) -> QueueState:
        """Get current queue state"""
        with self._state_lock:
            return self._state

    @property
    def queue_size(self) -> int:
        """Get current queue size"""
        return self._queue.qsize()

    @property
    def pending_count(self) -> int:
        """Get number of pending tasks (queue + batch buffer)"""
        with self._batch_lock:
            return self._queue.qsize() + len(self._batch_buffer)

    def start(self) -> None:
        """Start the async write queue"""
        with self._state_lock:
            if self._state == QueueState.RUNNING:
                log.warning("[ASYNC_QUEUE] Already running")
                return

            self._state = QueueState.RUNNING
            self._shutdown_event.clear()

        # Start worker threads
        for i in range(self.config.worker_threads):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"AsyncWriteWorker-{i}",
                daemon=True
            )
            worker.start()
            self._workers.append(worker)

        log.info(f"[ASYNC_QUEUE] Started with {self.config.worker_threads} worker(s)")

    def stop(self, wait: bool = True, timeout: float = 30.0) -> None:
        """
        Stop the async write queue

        Args:
            wait: Whether to wait for queue to drain
            timeout: Maximum time to wait for draining
        """
        with self._state_lock:
            if self._state == QueueState.STOPPED:
                return

            if wait:
                self._state = QueueState.DRAINING
            else:
                self._state = QueueState.STOPPING

        log.info(f"[ASYNC_QUEUE] Stopping (wait={wait})...")

        # Signal shutdown
        self._shutdown_event.set()

        if wait:
            # Wait for queue to drain
            start_time = time.time()
            while not self._queue.empty() and (time.time() - start_time) < timeout:
                time.sleep(0.1)

            # Flush remaining batch
            self._flush_batch(force=True)

        # Wait for workers to finish
        for worker in self._workers:
            worker.join(timeout=5.0)

        self._workers.clear()

        with self._state_lock:
            self._state = QueueState.STOPPED

        log.info("[ASYNC_QUEUE] Stopped")

    def enqueue(self, entry: CacheEntry, priority: int = 0) -> bool:
        """
        Enqueue a cache entry for async writing

        Args:
            entry: Cache entry to write
            priority: Task priority (lower = higher priority)

        Returns:
            True if enqueued successfully, False if dropped
        """
        if self.state != QueueState.RUNNING:
            log.warning("[ASYNC_QUEUE] Cannot enqueue: queue not running")
            return False

        task = WriteTask(entry=entry, priority=priority)

        try:
            if self.config.drop_on_overflow:
                # Non-blocking put with overflow protection
                try:
                    self._queue.put_nowait(task)
                except queue.Full:
                    with self._stats_lock:
                        self._stats.total_dropped += 1
                    log.warning(f"[ASYNC_QUEUE] Queue full, dropping entry: hash={entry.thinking_hash[:16]}...")
                    return False
            else:
                # Blocking put
                self._queue.put(task, timeout=1.0)

            with self._stats_lock:
                self._stats.total_enqueued += 1
                self._stats.current_queue_size = self._queue.qsize()

            return True

        except Exception as e:
            log.error(f"[ASYNC_QUEUE] Error enqueueing: {e}")
            return False

    def _worker_loop(self) -> None:
        """Worker thread main loop"""
        log.debug(f"[ASYNC_QUEUE] Worker {threading.current_thread().name} started")

        while not self._shutdown_event.is_set() or not self._queue.empty():
            try:
                # Get task from queue with timeout
                try:
                    task = self._queue.get(timeout=0.1)
                except queue.Empty:
                    # Check if we need to flush batch due to timeout
                    self._check_batch_timeout()
                    continue

                # Add to batch buffer
                with self._batch_lock:
                    self._batch_buffer.append(task)

                    # Check if batch is ready
                    if len(self._batch_buffer) >= self.config.batch_size:
                        self._flush_batch()

                # Mark task as done
                self._queue.task_done()

            except Exception as e:
                log.error(f"[ASYNC_QUEUE] Worker error: {e}")
                with self._stats_lock:
                    self._stats.last_error = str(e)

        # Final flush on shutdown
        self._flush_batch(force=True)

        log.debug(f"[ASYNC_QUEUE] Worker {threading.current_thread().name} stopped")

    def _check_batch_timeout(self) -> None:
        """Check if batch should be flushed due to timeout"""
        with self._batch_lock:
            if not self._batch_buffer:
                return

            elapsed_ms = (time.time() - self._last_flush_time) * 1000
            if elapsed_ms >= self.config.batch_timeout_ms:
                self._flush_batch()

    def _flush_batch(self, force: bool = False) -> None:
        """
        Flush batch buffer to L2 cache

        Args:
            force: Force flush even if batch is small
        """
        with self._batch_lock:
            if not self._batch_buffer:
                return

            if not force and len(self._batch_buffer) < self.config.batch_size:
                return

            # Take batch
            batch = self._batch_buffer.copy()
            self._batch_buffer.clear()
            self._last_flush_time = time.time()

        if not batch:
            return

        # Process batch
        entries = [task.entry for task in batch]
        failed_tasks: List[WriteTask] = []

        try:
            # Bulk write to L2
            success_count = self._l2_cache.bulk_set(entries, update_if_exists=True)

            with self._stats_lock:
                self._stats.total_processed += success_count
                self._stats.batch_count += 1
                self._stats.avg_batch_size = (
                    (self._stats.avg_batch_size * (self._stats.batch_count - 1) + len(batch))
                    / self._stats.batch_count
                )
                self._stats.last_flush_time = datetime.now()
                self._stats.current_queue_size = self._queue.qsize()

            # Check for partial failures
            if success_count < len(entries):
                failed_count = len(entries) - success_count
                log.warning(f"[ASYNC_QUEUE] Partial batch failure: {failed_count}/{len(entries)} failed")

                # For simplicity, we don't track which specific entries failed
                # In production, you might want more sophisticated tracking
                with self._stats_lock:
                    self._stats.total_failed += failed_count

            log.debug(f"[ASYNC_QUEUE] Flushed batch: {success_count}/{len(batch)} entries")

        except Exception as e:
            log.error(f"[ASYNC_QUEUE] Batch write error: {e}")

            with self._stats_lock:
                self._stats.last_error = str(e)

            # Retry failed tasks
            for task in batch:
                if task.retry_count < self.config.max_retries:
                    task.retry_count += 1
                    failed_tasks.append(task)
                else:
                    with self._stats_lock:
                        self._stats.total_failed += 1

        # Re-enqueue failed tasks with backoff
        for task in failed_tasks:
            delay = self.config.retry_delay_ms * (2 ** (task.retry_count - 1)) / 1000.0
            threading.Timer(delay, self._retry_task, args=[task]).start()

            with self._stats_lock:
                self._stats.total_retried += 1

    def _retry_task(self, task: WriteTask) -> None:
        """Retry a failed task"""
        if self.state != QueueState.RUNNING:
            return

        try:
            success = self._l2_cache.set(task.entry)

            with self._stats_lock:
                if success:
                    self._stats.total_processed += 1
                else:
                    self._stats.total_failed += 1

        except Exception as e:
            log.error(f"[ASYNC_QUEUE] Retry failed: {e}")

            if task.retry_count < self.config.max_retries:
                task.retry_count += 1
                delay = self.config.retry_delay_ms * (2 ** (task.retry_count - 1)) / 1000.0
                threading.Timer(delay, self._retry_task, args=[task]).start()

                with self._stats_lock:
                    self._stats.total_retried += 1
            else:
                with self._stats_lock:
                    self._stats.total_failed += 1

    def get_stats(self) -> QueueStats:
        """Get queue statistics"""
        with self._stats_lock:
            stats = QueueStats(
                total_enqueued=self._stats.total_enqueued,
                total_processed=self._stats.total_processed,
                total_failed=self._stats.total_failed,
                total_retried=self._stats.total_retried,
                total_dropped=self._stats.total_dropped,
                current_queue_size=self._queue.qsize(),
                batch_count=self._stats.batch_count,
                avg_batch_size=self._stats.avg_batch_size,
                last_flush_time=self._stats.last_flush_time,
                last_error=self._stats.last_error,
            )
        return stats

    def reset_stats(self) -> None:
        """Reset queue statistics"""
        with self._stats_lock:
            self._stats = QueueStats()
        log.info("[ASYNC_QUEUE] Statistics reset")

    def force_flush(self) -> int:
        """
        Force flush all pending entries

        Returns:
            Number of entries flushed
        """
        # Flush batch buffer
        with self._batch_lock:
            batch_size = len(self._batch_buffer)

        self._flush_batch(force=True)

        return batch_size

    def wait_until_empty(self, timeout: float = 30.0) -> bool:
        """
        Wait until queue is empty

        Args:
            timeout: Maximum time to wait

        Returns:
            True if queue became empty, False if timeout
        """
        start_time = time.time()

        while (time.time() - start_time) < timeout:
            if self._queue.empty():
                with self._batch_lock:
                    if not self._batch_buffer:
                        return True

            time.sleep(0.1)

        return False


# ==================== Factory Function ====================

def create_async_queue(
    l2_cache: Any,
    config: Optional[AsyncWriteConfig] = None,
    auto_start: bool = True
) -> AsyncWriteQueue:
    """
    Create and optionally start an async write queue

    Args:
        l2_cache: L2 cache layer (SignatureDatabase)
        config: Queue configuration
        auto_start: Whether to start the queue immediately

    Returns:
        AsyncWriteQueue instance
    """
    queue_instance = AsyncWriteQueue(l2_cache, config)

    if auto_start:
        queue_instance.start()

    return queue_instance
