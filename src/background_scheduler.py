"""
后台任务调度器 - Background Task Scheduler

提供后台自动刷新配额的调度功能，是配额保护和智能预热的基础设施。

特性:
- 定期自动刷新所有账号配额
- 并发刷新（限制最大并发数为 5）
- 异常容错和自动重试
- 可配置刷新间隔
"""

import asyncio
import os
import random
import time
from typing import Optional
from log import log


class _AsyncMinIntervalLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = float(min_interval_seconds)
        self._lock: Optional[asyncio.Lock] = None
        self._next_allowed_at: float = 0.0

    async def wait(self) -> None:
        if self._min_interval <= 0:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.time()
            if now < self._next_allowed_at:
                await asyncio.sleep(self._next_allowed_at - now)
            self._next_allowed_at = time.time() + self._min_interval


class BackgroundScheduler:
    """后台任务调度器

    [v7.2] 人类化改进：添加刷新间隔随机抖动
    """

    def __init__(self, credential_manager):
        """初始化调度器

        Args:
            credential_manager: 凭证管理器实例
        """
        self.credential_manager = credential_manager
        self.refresh_task: Optional[asyncio.Task] = None
        self.is_running = False
        self._cooldown_until: float = 0.0
        self._quota_refresh_limiter: Optional[_AsyncMinIntervalLimiter] = None

    @staticmethod
    def _apply_jitter(base_value: float, jitter_ratio: float) -> float:
        """[v7.2新增] 应用随机抖动，模拟人类行为

        Args:
            base_value: 基础值
            jitter_ratio: 抖动比例（如 0.15 表示 ±15%）

        Returns:
            带抖动的值
        """
        if jitter_ratio <= 0:
            return base_value
        return base_value * random.uniform(1.0 - jitter_ratio, 1.0 + jitter_ratio)

    async def _throttle_quota_refresh(self) -> None:
        if self._quota_refresh_limiter is None:
            try:
                v = float(os.getenv("BACKGROUND_REFRESH_MIN_REQUEST_INTERVAL_SECONDS", "0.2"))
            except ValueError:
                v = 0.2
            self._quota_refresh_limiter = _AsyncMinIntervalLimiter(min_interval_seconds=v)
        await self._quota_refresh_limiter.wait()
        
    async def start_auto_refresh(self, interval_minutes: int = 15):
        """启动后台自动刷新
        
        Args:
            interval_minutes: 刷新间隔（分钟），默认 15 分钟
        """
        if self.is_running:
            log.warning("[BackgroundScheduler] 已在运行，跳过重复启动")
            return
            
        self.is_running = True
        self.refresh_task = asyncio.create_task(
            self._refresh_loop(interval_minutes)
        )
        log.info(f"[BackgroundScheduler] ✓ 启动自动刷新调度器 (间隔: {interval_minutes}分钟)")
        
    async def _refresh_loop(self, interval_minutes: int):
        """刷新循环主逻辑

        [v7.2] 人类化改进：添加刷新间隔随机抖动（±15%）

        Args:
            interval_minutes: 刷新间隔（分钟）
        """
        run_immediately = os.getenv("BACKGROUND_REFRESH_RUN_IMMEDIATELY", "").lower() in ("true", "1", "yes", "on")

        # [v7.2] 初始延迟也添加抖动
        if not run_immediately:
            initial_delay = self._apply_jitter(max(1, interval_minutes) * 60, 0.15)
            await asyncio.sleep(initial_delay)

        while self.is_running:
            try:
                now = time.time()
                if now < self._cooldown_until:
                    sleep_s = self._cooldown_until - now
                    log.warning(f"[BackgroundScheduler] 全局冷却中，跳过本轮刷新，sleep={sleep_s:.0f}s")
                    await asyncio.sleep(sleep_s)
                    continue

                # 立即执行一次刷新
                await self._refresh_all_quotas()

                # [v7.2] 等待下一个周期（添加±15%随机抖动）
                jittered_interval = self._apply_jitter(interval_minutes * 60, 0.15)
                log.debug(f"[BackgroundScheduler] 等待 {jittered_interval/60:.1f} 分钟后执行下一次刷新")
                await asyncio.sleep(jittered_interval)
                
            except asyncio.CancelledError:
                log.info("[BackgroundScheduler] 调度器被取消")
                break
            except Exception as e:
                log.error(f"[BackgroundScheduler] 刷新循环错误: {e}")
                # 出错后做退避，避免误触发“刷新风暴”
                await asyncio.sleep(max(60, interval_minutes * 60))
                
    async def _refresh_all_quotas(self):
        """并发刷新所有账号配额
        
        使用信号量限制最大并发数为 5，避免过度消耗资源和触发 API 限流
        """
        start_time = time.time()
        
        log.info("[BackgroundScheduler] ========== 开始批量刷新所有账号配额 ==========")
        
        try:
            # 获取所有凭证名称
            all_credentials = await self.credential_manager._storage_adapter.list_credentials()
            
            if not all_credentials:
                log.warning("[BackgroundScheduler] 没有找到任何凭证")
                return
                
            random.shuffle(all_credentials)
            try:
                max_per_cycle = int(os.getenv("BACKGROUND_REFRESH_MAX_ACCOUNTS_PER_CYCLE", "20"))
            except ValueError:
                max_per_cycle = 20
            if max_per_cycle > 0:
                all_credentials = all_credentials[:max_per_cycle]

            log.info(f"[BackgroundScheduler] 本轮刷新账号数: {len(all_credentials)}")
              
            # 使用信号量限制并发数
            try:
                MAX_CONCURRENT = max(1, int(os.getenv("BACKGROUND_REFRESH_MAX_CONCURRENT", "5")))
            except ValueError:
                MAX_CONCURRENT = 5
            semaphore = asyncio.Semaphore(MAX_CONCURRENT)

            try:
                min_cred_refresh_minutes = int(os.getenv("BACKGROUND_REFRESH_MIN_CRED_REFRESH_MINUTES", str(max(15, 2 * MAX_CONCURRENT))))
            except ValueError:
                min_cred_refresh_minutes = 15

            try:
                cooldown_on_429_seconds = int(os.getenv("BACKGROUND_REFRESH_429_COOLDOWN_SECONDS", "600"))
            except ValueError:
                cooldown_on_429_seconds = 600
            
            async def refresh_single(cred_name: str):
                """刷新单个凭证"""
                async with semaphore:
                    try:
                        # 获取凭证数据
                        cred_data = await self.credential_manager._storage_adapter.get_credential(cred_name)
                        
                        if not cred_data:
                            log.warning(f"[BackgroundScheduler] 凭证不存在: {cred_name}")
                            return False
                            
                        # 检查是否禁用
                        if cred_data.get("disabled", False):
                            log.debug(f"[BackgroundScheduler] 跳过已禁用账号: {cred_name}")
                            return None  # None 表示跳过

                        # 避免在短时间内重复刷新同一账号（重启/抖动场景会触发）
                        last_ts = cred_data.get("last_quota_refresh")
                        if isinstance(last_ts, (int, float)) and last_ts > 0:
                            if time.time() - float(last_ts) < (min_cred_refresh_minutes * 60):
                                return None
                            
                        # 刷新凭证（调用现有方法）
                        from src.credentials import Credentials
                        credentials = Credentials.from_dict(cred_data)
                        
                        # 刷新 token（如果需要）
                        token_refreshed = await credentials.refresh_if_needed()
                        
                        if token_refreshed:
                            log.info(f"[BackgroundScheduler] Token已刷新: {cred_name}")
                            # 更新存储
                            await self.credential_manager._storage_adapter.store_credential(
                                cred_name,
                                credentials.to_dict()
                            )
                            
                        # 刷新配额信息
                        # [FIX 2026-01-17] 使用 fetch_quota_info 从 Google API 获取配额（使用内存缓存）
                        # 原代码调用了不存在的 fetch_quota_data 函数
                        # 注意：配额信息不持久化到数据库，只存在于内存缓存中
                        is_antigravity = cred_data.get("type") == "antigravity"
                        await self._throttle_quota_refresh()

                        if is_antigravity:
                            # Antigravity 凭证刷新配额（使用内存缓存）
                            from src.antigravity_api import fetch_quota_info
                            quota_result = await fetch_quota_info(
                                credentials.access_token,
                                cache_key=cred_name,
                                force_refresh=True  # 强制刷新缓存
                            )
                            if quota_result.get("success"):
                                log.info(f"[BackgroundScheduler] ✓ 配额已刷新: {cred_name}")
                            else:
                                log.warning(f"[BackgroundScheduler] ⚠ 配额刷新失败: {cred_name}")
                        else:
                            # GeminiCLI 凭证 - 目前不支持配额刷新
                            log.debug(f"[BackgroundScheduler] 跳过 GeminiCLI 凭证配额刷新: {cred_name}")

                        # 更新最后刷新时间（不存储配额数据到数据库）
                        cred_data["last_quota_refresh"] = time.time()

                        await self.credential_manager._storage_adapter.store_credential(
                            cred_name,
                            cred_data
                        )

                        return True
                        
                    except Exception as e:
                        # 429 期间做全局冷却，避免后台刷新把池子刷爆
                        msg = str(e)
                        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                            self._cooldown_until = max(self._cooldown_until, time.time() + float(cooldown_on_429_seconds))
                            log.warning(
                                f"[BackgroundScheduler] 检测到刷新 429，进入全局冷却 {cooldown_on_429_seconds}s"
                            )
                        log.error(f"[BackgroundScheduler] ✗ 刷新失败 {cred_name}: {e}")
                        return False
            
            # 并发执行所有刷新任务
            tasks = [refresh_single(cred_name) for cred_name in all_credentials]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 统计结果
            success_count = sum(1 for r in results if r is True)
            failed_count = sum(1 for r in results if r is False)
            skipped_count = sum(1 for r in results if r is None)
            
            elapsed = time.time() - start_time
            
            log.info(
                f"[BackgroundScheduler] ========== 刷新完成 ========== "
                f"成功: {success_count}/{len(all_credentials)} | "
                f"失败: {failed_count} | 跳过: {skipped_count} | "
                f"耗时: {elapsed:.2f}s"
            )
            
        except Exception as e:
            log.error(f"[BackgroundScheduler] 批量刷新异常: {e}")
            
    def stop(self):
        """停止调度器"""
        log.info("[BackgroundScheduler] 停止调度器")
        self.is_running = False
        if self.refresh_task:
            self.refresh_task.cancel()
