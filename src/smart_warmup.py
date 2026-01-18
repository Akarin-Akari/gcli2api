"""
智能预热模块 - Smart Warmup v7.1

[FIX 2026-01-17 v7.1] 修复模型级冷却检查缺失问题
- 新增：在预热判断前检查 model_cooldowns 状态
- 修复：已进入冷却期的凭证（即使配额100%）不再被错误预热

[FIX 2026-01-17 v7] 配额保护架构：低配额自动禁用 + 预热时自动启用

核心原则：
1. 检测到 100% 配额时触发预热判断
2. 三重保险：
   - 检查1（新增）：模型级冷却检查（model_cooldowns）
   - 检查2（优先）：基于 resetTimeRaw 判断当前周期是否已预热
   - 检查3（保底）：基于本地计时，5小时内只允许一次
3. 429 也算成功：预热目的是触发配额消耗，429 说明已触发
4. 宁缺毋滥：任何不确定情况都跳过预热
5. [v7新增] 配额保护（直接复用网页端的禁用/启用方法）：
   - 检测到配额 ≤ 20% 时：调用 set_cred_disabled(True) 禁用凭证
   - 配额恢复到 100% 时：调用 set_cred_disabled(False) 启用凭证
   - 通过凭证状态中的 "auto_disabled_by_warmup" 字段区分自动禁用和手动禁用
   - 宁可少用，也不能等3天~1周的恢复时间

历史记录格式（v6升级）：
{
    "cred:model": {
        "last_attempt_time": 1234567890.0,  # 上次预热尝试时间（不管成功失败）
        "reset_time_raw": "2026-01-17T12:00:00Z"  # 当时获取的下次重置时间
    }
}
"""

import asyncio
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Set, Union
from log import log


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


class SmartWarmup:
    """智能预热模块 v7"""

    # 冷却期（秒）- Pro账号5h重置周期
    COOLDOWN_SECONDS = _get_env_int("SMART_WARMUP_COOLDOWN_SECONDS", 5 * 60 * 60)  # 默认 5 小时

    # 预热历史文件
    HISTORY_FILE = Path("warmup_history.json")

    # 扫描间隔（秒）- v7调整：因为100%配额持续时间长，降低扫描频率
    SCAN_INTERVAL = _get_env_int("SMART_WARMUP_SCAN_INTERVAL_SECONDS", 30 * 60)  # 默认 30 分钟

    # 批次间延迟（秒）
    BATCH_DELAY_SECONDS = max(0.0, _get_env_float("SMART_WARMUP_BATCH_DELAY_SECONDS", 2.0))

    # 跟随预热模型列表
    FOLLOW_WARMUP_MODELS = [
        model.strip().lower()
        for model in os.getenv("SMART_WARMUP_FOLLOW_MODELS", "gemini-3-pro-high").split(",")
        if model.strip()
    ]

    # Claude 模型前缀
    CLAUDE_MODEL_PREFIXES = ["claude-"]

    # [v7新增] 低配额自动禁用阈值（0.2 = 20%）
    AUTO_DISABLE_THRESHOLD = _get_env_float("SMART_WARMUP_AUTO_DISABLE_THRESHOLD", 0.20)

    # [v7新增] 是否启用预热时自动启用已禁用的凭证
    AUTO_ENABLE_ON_WARMUP = os.getenv("SMART_WARMUP_AUTO_ENABLE_ON_WARMUP", "true").lower() == "true"

    # [v7新增] 是否启用低配额自动禁用
    AUTO_DISABLE_LOW_QUOTA = os.getenv("SMART_WARMUP_AUTO_DISABLE_LOW_QUOTA", "true").lower() == "true"

    def __init__(self, credential_manager):
        """初始化智能预热模块"""
        self.credential_manager = credential_manager
        self.scheduler_task: Optional[asyncio.Task] = None
        self.is_running = False

    async def start_scheduler(self):
        """启动预热调度器"""
        if self.is_running:
            log.warning("[SmartWarmup] 调度器已在运行")
            return

        self.is_running = True
        self.scheduler_task = asyncio.create_task(self._warmup_loop())
        log.info(f"[SmartWarmup] ✓ 调度器已启动 (扫描间隔: {self.SCAN_INTERVAL}秒, 冷却期: {self.COOLDOWN_SECONDS}秒)")

    def stop(self):
        """停止调度器"""
        self.is_running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
            log.info("[SmartWarmup] 调度器已停止")

    async def _warmup_loop(self):
        """预热循环"""
        while self.is_running:
            try:
                await self._scan_and_warmup()
                await asyncio.sleep(self.SCAN_INTERVAL)
            except asyncio.CancelledError:
                log.info("[SmartWarmup] 调度器被取消")
                break
            except Exception as e:
                log.error(f"[SmartWarmup] 扫描循环错误: {e}")
                await asyncio.sleep(60)

    async def _scan_and_warmup(self):
        """扫描并预热 100% 配额的模型

        [FIX 2026-01-17 v7] 配额保护架构（直接复用网页端方法）：
        1. 检测低配额（≤20%）且未禁用 → 调用 set_cred_disabled(True) 禁用
        2. 检测100%配额且已禁用（自动禁用的）→ 调用 set_cred_disabled(False) 启用 → 执行预热
        """
        from config import get_smart_warmup_enabled, get_warmup_models
        from src.antigravity_api import fetch_quota_info

        # 检查是否启用
        enabled = await get_smart_warmup_enabled()
        if not enabled:
            return

        monitored_models = await get_warmup_models()
        if not monitored_models:
            return

        log.info("[SmartWarmup] ========== 开始扫描配额状态 ==========")

        # 加载历史记录（v6格式）
        history = self._load_history()
        now = time.time()

        # 获取所有 Antigravity 凭证（包括已禁用的，用于检测是否可以重新启用）
        all_credentials = await self.credential_manager._storage_adapter.list_credentials(
            is_antigravity=True
        )

        warmup_tasks = []
        skipped_cooldown = 0
        skipped_follow = 0
        auto_disabled_count = 0
        auto_enabled_count = 0

        for cred_name in all_credentials:
            try:
                cred_data = await self.credential_manager._storage_adapter.get_credential(
                    cred_name, is_antigravity=True
                )

                if not cred_data:
                    continue

                access_token = cred_data.get("access_token") or cred_data.get("token")
                if not access_token:
                    log.debug(f"[SmartWarmup] {cred_name} 没有 access_token，跳过")
                    continue

                # 获取配额信息（即使凭证被禁用也要查询，用于判断是否可以重新启用）
                quota_result = await fetch_quota_info(access_token, cache_key=cred_name)

                if not quota_result.get("success"):
                    log.debug(f"[SmartWarmup] {cred_name} 获取配额失败，跳过")
                    continue

                models_data = quota_result.get("models", {})

                # [v7] 从数据库重新读取凭证状态，确保状态一致
                is_disabled = cred_data.get("disabled", False)
                is_auto_disabled = cred_data.get("auto_disabled_by_warmup", False)

                # [FIX 2026-01-17 v7.1] 获取模型级冷却状态
                # 如果凭证的某个模型在冷却期内，即使配额为100%也不应该预热
                cred_state = await self.credential_manager._storage_adapter.get_credential_state(
                    cred_name, is_antigravity=True
                )
                model_cooldowns = cred_state.get("model_cooldowns", {}) or {}

                # [v7新增] 检查所有监控模型的配额状态
                min_quota = 1.0  # 最低配额（用于判断是否需要禁用）
                has_full_quota = False  # 是否有100%配额的模型

                for model_id, model_info in models_data.items():
                    if not self._is_monitored(model_id, monitored_models):
                        continue
                    remaining = model_info.get("remaining", 0)
                    min_quota = min(min_quota, remaining)
                    if remaining == 1.0:  # 100%
                        has_full_quota = True

                # [v7新增] 低配额自动禁用逻辑
                # 边界检查：确认当前未禁用 且 配额确实 ≤ 阈值
                if self.AUTO_DISABLE_LOW_QUOTA and not is_disabled and min_quota <= self.AUTO_DISABLE_THRESHOLD:
                    await self._auto_disable_credential(cred_name, min_quota)
                    auto_disabled_count += 1
                    continue  # 已禁用，跳过后续处理

                # [v7新增] 预热时自动启用逻辑
                # 边界检查：确认当前已禁用 且 是自动禁用的 且 配额已恢复到100%
                if self.AUTO_ENABLE_ON_WARMUP and is_disabled and is_auto_disabled and has_full_quota:
                    await self._auto_enable_credential(cred_name)
                    auto_enabled_count += 1
                    # 更新本地状态
                    is_disabled = False
                elif is_disabled:
                    # 已禁用但不满足自动启用条件，跳过
                    if is_auto_disabled and not has_full_quota:
                        log.debug(f"[SmartWarmup] {cred_name} 自动禁用中，配额未恢复到100%，跳过")
                    else:
                        log.debug(f"[SmartWarmup] {cred_name} 已禁用（手动禁用），跳过")
                    continue

                # 分离普通模型和跟随模型
                normal_tasks = []
                follow_tasks = []
                has_claude_warmup = False

                for model_id, model_info in models_data.items():
                    model_name = model_id
                    remaining_fraction = model_info.get("remaining", 0)
                    percentage = remaining_fraction * 100

                    # 只处理监控列表中的模型
                    if not self._is_monitored(model_name, monitored_models):
                        continue

                    key = f"{cred_name}:{model_name}"
                    reset_time_raw = model_info.get("resetTimeRaw", "")

                    # [FIX 2026-01-17 v6] 核心判断逻辑
                    #
                    # 条件1：必须是 100% 配额
                    # 条件2：双重保险判断是否需要预热
                    #
                    # 背景：谷歌把额度颗粒度改成 20% 一档
                    # 100% → 使用很久 → 还是显示 100% → 直到用掉 20% 才变成 80%

                    if percentage != 100:
                        # 配额不是 100%，不需要预热
                        continue

                    # [FIX 2026-01-17 v7.1] 检查模型级冷却
                    # 如果该模型在冷却期内，即使配额为100%也不应该预热
                    model_cooldown_until = model_cooldowns.get(model_name)
                    if model_cooldown_until is not None and now < model_cooldown_until:
                        remaining_cooldown = model_cooldown_until - now
                        log.debug(
                            f"[SmartWarmup] {key} 模型级冷却中，跳过预热 "
                            f"(剩余 {remaining_cooldown/3600:.1f}h)"
                        )
                        skipped_cooldown += 1
                        continue

                    # 双重保险判断
                    should_warmup = self._should_warmup_dual_check(
                        key, reset_time_raw, history, now
                    )
                    if not should_warmup:
                        skipped_cooldown += 1
                        continue

                    task = {
                        "cred_name": cred_name,
                        "model_name": model_name,
                        "cred_data": cred_data,
                        "reset_time_raw": reset_time_raw
                    }

                    if self._is_follow_model(model_name):
                        follow_tasks.append(task)
                    else:
                        normal_tasks.append(task)
                        if self._is_claude_model(model_name):
                            has_claude_warmup = True

                # 添加普通模型任务
                warmup_tasks.extend(normal_tasks)

                # 只有当有 Claude 模型需要预热时，才添加跟随模型任务
                if has_claude_warmup:
                    warmup_tasks.extend(follow_tasks)
                    if follow_tasks:
                        log.debug(
                            f"[SmartWarmup] {cred_name} 有 Claude 模型预热，"
                            f"顺带预热 {len(follow_tasks)} 个跟随模型"
                        )
                else:
                    skipped_follow += len(follow_tasks)

            except Exception as e:
                log.error(f"[SmartWarmup] 处理凭证 {cred_name} 失败: {e}")
                continue

        # 输出统计信息
        if auto_disabled_count > 0 or auto_enabled_count > 0:
            log.info(
                f"[SmartWarmup] 配额保护: 自动禁用 {auto_disabled_count} 个, 自动启用 {auto_enabled_count} 个"
            )

        if warmup_tasks:
            log.info(
                f"[SmartWarmup] 发现 {len(warmup_tasks)} 个待预热任务 "
                f"(跳过冷却期: {skipped_cooldown}, 跳过跟随模型: {skipped_follow})"
            )
            await self._execute_warmup_tasks(warmup_tasks, history)
        else:
            log.info(
                f"[SmartWarmup] 没有需要预热的任务 "
                f"(跳过冷却期: {skipped_cooldown}, 跳过跟随模型: {skipped_follow})"
            )

    async def _auto_disable_credential(self, cred_name: str, current_quota: float):
        """[v7新增] 自动禁用低配额凭证（直接复用网页端方法）

        Args:
            cred_name: 凭证名称
            current_quota: 当前配额（0.0-1.0）
        """
        try:
            # 1. 先设置标记字段，表示是自动禁用的
            await self.credential_manager.update_credential_state(
                cred_name,
                {"auto_disabled_by_warmup": True},
                is_antigravity=True
            )

            # 2. 调用网页端同样的禁用方法
            await self.credential_manager.set_cred_disabled(cred_name, True, is_antigravity=True)

            log.warning(
                f"[SmartWarmup] ⚠ 自动禁用凭证 {cred_name} "
                f"(配额: {current_quota*100:.0f}% ≤ {self.AUTO_DISABLE_THRESHOLD*100:.0f}%)"
            )
        except Exception as e:
            log.error(f"[SmartWarmup] 自动禁用凭证 {cred_name} 失败: {e}")

    async def _auto_enable_credential(self, cred_name: str):
        """[v7新增] 自动启用已恢复配额的凭证（直接复用网页端方法）

        Args:
            cred_name: 凭证名称
        """
        try:
            # 1. 调用网页端同样的启用方法
            await self.credential_manager.set_cred_disabled(cred_name, False, is_antigravity=True)

            # 2. 清除自动禁用标记
            await self.credential_manager.update_credential_state(
                cred_name,
                {"auto_disabled_by_warmup": False},
                is_antigravity=True
            )

            log.info(
                f"[SmartWarmup] ✓ 自动启用凭证 {cred_name} "
                f"(配额已恢复到100%)"
            )
        except Exception as e:
            log.error(f"[SmartWarmup] 自动启用凭证 {cred_name} 失败: {e}")

    def _should_warmup_dual_check(
        self,
        key: str,
        reset_time_raw: str,
        history: Dict[str, Any],
        now: float
    ) -> bool:
        """[FIX 2026-01-17 v6] 双重保险判断是否需要预热

        方案1（优先）：基于 resetTimeRaw 判断当前周期是否已预热
        方案2（保底）：基于本地计时，5小时内只允许一次

        Args:
            key: 历史记录的 key（cred_name:model_name）
            reset_time_raw: ISO格式的重置时间（可能为空）
            history: 预热历史记录（v6格式）
            now: 当前时间戳

        Returns:
            bool: True 表示需要预热，False 表示不需要
        """
        history_entry = history.get(key)

        # 没有历史记录，需要预热
        if not history_entry:
            log.debug(f"[SmartWarmup] {key} 无历史记录，需要预热")
            return True

        # 兼容旧格式（float）和新格式（dict）
        if isinstance(history_entry, (int, float)):
            # 旧格式，转换为新格式
            last_attempt_time = float(history_entry)
            saved_reset_time = ""
        else:
            last_attempt_time = history_entry.get("last_attempt_time", 0)
            saved_reset_time = history_entry.get("reset_time_raw", "")

        # ========== 方案1：基于 resetTimeRaw（优先） ==========
        if reset_time_raw:
            try:
                # 解析当前的 resetTimeRaw（下次重置时间）
                reset_str = reset_time_raw
                if reset_str.endswith("Z"):
                    reset_str = reset_str.replace("Z", "+00:00")
                next_reset_dt = datetime.fromisoformat(reset_str)
                next_reset_ts = next_reset_dt.timestamp()

                # 计算当前周期的开始时间（下次重置时间 - 5小时）
                current_cycle_start_ts = next_reset_ts - (5 * 60 * 60)

                # 如果上次预热时间 >= 当前周期开始时间，说明本周期已预热
                if last_attempt_time >= current_cycle_start_ts:
                    log.debug(
                        f"[SmartWarmup] {key} 本周期已预热 "
                        f"(上次: {datetime.fromtimestamp(last_attempt_time).strftime('%m-%d %H:%M:%S')}, "
                        f"周期开始: {datetime.fromtimestamp(current_cycle_start_ts).strftime('%m-%d %H:%M:%S')})"
                    )
                    return False

                # 当前时间还没到下次重置时间，但上次预热在上一个周期
                # 说明进入了新周期，需要预热
                log.debug(
                    f"[SmartWarmup] {key} 进入新周期，需要预热 "
                    f"(下次重置: {next_reset_dt.strftime('%m-%d %H:%M')})"
                )
                return True

            except Exception as e:
                log.warning(f"[SmartWarmup] 解析 resetTimeRaw 失败 ({key}): {e}，使用保底方案")
                # 解析失败，使用保底方案

        # ========== 方案2：基于本地计时（保底） ==========
        elapsed = now - last_attempt_time
        if elapsed < self.COOLDOWN_SECONDS:
            remaining = self.COOLDOWN_SECONDS - elapsed
            log.debug(
                f"[SmartWarmup] {key} 本地冷却期内，跳过 "
                f"(已过 {elapsed/3600:.1f}h, 剩余 {remaining/3600:.1f}h)"
            )
            return False

        log.debug(
            f"[SmartWarmup] {key} 本地冷却期已过，需要预热 "
            f"(已过 {elapsed/3600:.1f}h)"
        )
        return True

    def _is_follow_model(self, model_name: str) -> bool:
        """判断是否为跟随预热模型"""
        model_name_lower = model_name.lower()
        for follow_model in self.FOLLOW_WARMUP_MODELS:
            if model_name_lower.startswith(follow_model):
                return True
        return False

    def _is_claude_model(self, model_name: str) -> bool:
        """判断是否为 Claude 模型"""
        model_name_lower = model_name.lower()
        for prefix in self.CLAUDE_MODEL_PREFIXES:
            if model_name_lower.startswith(prefix):
                return True
        return False

    async def _execute_warmup_tasks(
        self,
        tasks: List[Dict[str, Any]],
        history: Dict[str, Any]
    ):
        """执行预热任务

        [FIX 2026-01-17 v6] 关键改进：
        1. 不管成功还是失败（包括429），都记录尝试时间
        2. 429 也算"成功"：预热目的是触发配额消耗，429 说明已触发
        3. 立即保存历史，防止中途退出丢失
        """
        success_count = 0
        fail_count = 0
        skip_count = 0
        blocked_credentials: Set[str] = set()

        for i, task in enumerate(tasks):
            cred_name = task["cred_name"]
            model_name = task["model_name"]
            cred_data = task["cred_data"]
            reset_time_raw = task.get("reset_time_raw", "")

            # 如果该凭证已被连接错误阻止，跳过
            if cred_name in blocked_credentials:
                log.debug(f"[SmartWarmup] 跳过 {cred_name}/{model_name}（凭证已被阻止）")
                skip_count += 1
                continue

            try:
                # 执行预热
                result = await self._warmup_model(cred_name, model_name, cred_data)

                key = f"{cred_name}:{model_name}"

                # [FIX 2026-01-17 v6] 关键改进：
                # 不管成功还是失败（包括429），都记录尝试时间和 resetTimeRaw
                # 这样可以防止短时间内重复尝试
                if result in ("success", "rate_limited"):
                    # 429 也算"成功"：预热目的是触发配额消耗，429 说明已触发
                    history[key] = {
                        "last_attempt_time": time.time(),
                        "reset_time_raw": reset_time_raw
                    }
                    self._save_history(history)

                    if result == "success":
                        log.info(f"[SmartWarmup] ✓ 预热成功 ({i+1}/{len(tasks)}): {cred_name}/{model_name}")
                        success_count += 1
                    else:
                        # 429 也记录为成功（已触发配额消耗）
                        log.info(f"[SmartWarmup] ✓ 预热触发429 ({i+1}/{len(tasks)}): {cred_name}/{model_name} (视为成功)")
                        success_count += 1

                elif result == "connect_error":
                    # 连接错误，阻止该凭证后续所有预热
                    blocked_credentials.add(cred_name)
                    log.warning(f"[SmartWarmup] ⚠ {cred_name} 连接失败，停止该凭证的所有预热")
                    fail_count += 1

                else:
                    # 其他失败，也记录时间（防止频繁重试）
                    history[key] = {
                        "last_attempt_time": time.time(),
                        "reset_time_raw": reset_time_raw
                    }
                    self._save_history(history)
                    fail_count += 1

                # 批次间延迟
                if self.BATCH_DELAY_SECONDS and i < len(tasks) - 1:
                    await asyncio.sleep(self.BATCH_DELAY_SECONDS)

            except Exception as e:
                log.error(f"[SmartWarmup] 预热异常 {cred_name}/{model_name}: {e}")
                fail_count += 1

        log.info(
            f"[SmartWarmup] ========== 预热完成 (成功: {success_count}, 失败: {fail_count}, 跳过: {skip_count}) =========="
        )

    async def _warmup_model(
        self,
        cred_name: str,
        model_name: str,
        cred_data: Dict[str, Any]
    ) -> str:
        """发送预热请求

        Returns:
            str: "success" | "rate_limited" | "failed" | "connect_error"
        """
        try:
            from src.antigravity_api import build_antigravity_headers, _throttle_antigravity_upstream
            from config import get_antigravity_api_url
            import httpx

            access_token = cred_data.get("access_token") or cred_data.get("token")
            project_id = cred_data.get("project_id", "")

            if not access_token:
                log.warning(f"[SmartWarmup] {cred_name} 没有 access_token")
                return "failed"

            # 使用全局节流器
            await _throttle_antigravity_upstream()

            # 构建请求
            api_url = await get_antigravity_api_url()
            target_url = f"{api_url}/v1internal:streamGenerateContent?alt=sse"

            headers = build_antigravity_headers(access_token, model_name)

            warmup_payload = {
                "model": model_name,
                "project": project_id,
                "request": {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": "ping"}]
                        }
                    ],
                    "generationConfig": {
                        "maxOutputTokens": 1
                    }
                }
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    target_url,
                    headers=headers,
                    json=warmup_payload
                )

                if response.status_code == 200:
                    return "success"
                elif response.status_code == 429:
                    # [FIX 2026-01-17 v6] 429 也是"成功"
                    # 预热的目的是触发配额消耗，429 说明已经触发了
                    return "rate_limited"
                else:
                    log.warning(
                        f"[SmartWarmup] {cred_name}/{model_name} 返回 {response.status_code}"
                    )
                    return "failed"

        except httpx.ConnectError:
            log.warning(f"[SmartWarmup] {cred_name}/{model_name} 连接失败")
            return "connect_error"
        except httpx.TimeoutException:
            # 超时通常意味着请求已发送成功
            log.debug(f"[SmartWarmup] {cred_name}/{model_name} 超时，视为成功")
            return "success"
        except Exception as e:
            log.error(f"[SmartWarmup] {cred_name}/{model_name} 请求失败: {e}")
            return "failed"

    def _is_monitored(self, model_name: str, monitored_list: List[str]) -> bool:
        """判断是否为监控模型"""
        model_name_lower = model_name.lower()
        for monitored in monitored_list:
            if model_name_lower.startswith(monitored.lower()):
                return True
        return False

    def _load_history(self) -> Dict[str, Any]:
        """加载预热历史（支持 v6 格式和旧格式兼容）"""
        if self.HISTORY_FILE.exists():
            try:
                data = json.loads(self.HISTORY_FILE.read_text(encoding="utf-8"))
                # 兼容旧格式：如果值是 float，保持原样（在使用时会转换）
                return data
            except Exception as e:
                log.warning(f"[SmartWarmup] 加载历史失败: {e}")
                return {}
        return {}

    def _save_history(self, history: Dict[str, Any]):
        """保存预热历史（v6格式）"""
        try:
            # 清理过期的历史记录（超过 24 小时）
            now = time.time()
            cleaned_history = {}

            for k, v in history.items():
                if isinstance(v, (int, float)):
                    # 旧格式
                    if now - v < 24 * 60 * 60:
                        cleaned_history[k] = v
                elif isinstance(v, dict):
                    # 新格式
                    last_attempt = v.get("last_attempt_time", 0)
                    if now - last_attempt < 24 * 60 * 60:
                        cleaned_history[k] = v

            self.HISTORY_FILE.write_text(
                json.dumps(cleaned_history, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            log.error(f"[SmartWarmup] 保存历史失败: {e}")

    async def trigger_manual_warmup(self, cred_name: Optional[str] = None):
        """手动触发预热（忽略冷却期）"""
        from src.antigravity_api import fetch_quota_info

        log.info(f"[SmartWarmup] 手动触发预热: {cred_name or '全部'}")

        if cred_name:
            cred_data = await self.credential_manager._storage_adapter.get_credential(
                cred_name, is_antigravity=True
            )
            if cred_data:
                from config import get_warmup_models
                monitored = await get_warmup_models()

                access_token = cred_data.get("access_token") or cred_data.get("token")
                if not access_token:
                    log.warning(f"[SmartWarmup] {cred_name} 没有 access_token")
                    return

                quota_result = await fetch_quota_info(access_token, cache_key=cred_name)

                if quota_result.get("success"):
                    models_data = quota_result.get("models", {})
                    for model_id in models_data.keys():
                        if self._is_monitored(model_id, monitored):
                            await self._warmup_model(cred_name, model_id, cred_data)
                else:
                    log.warning(f"[SmartWarmup] {cred_name} 获取配额失败")
        else:
            await self._scan_and_warmup()
