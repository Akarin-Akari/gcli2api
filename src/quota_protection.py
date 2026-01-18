"""
配额保护模块 - Quota Protection

当账号的高级模型（Claude系列、Gemini 3 Pro等）剩余配额低于阈值时自动禁用账号。
配额恢复后自动重新启用账号。

特性:
- 可配置保护阈值（默认 10%）
- 可配置监控模型列表
- 自动禁用/启用逻辑
- 详细的保护日志
"""

import time
from typing import Dict, Any, List, Optional
from log import log


class QuotaProtection:
    """配额保护模块"""
    
    # 禁用原因标识
    DISABLE_REASON = "quota_protection"
    
    def __init__(self, credential_manager):
        """初始化配额保护模块
        
        Args:
            credential_manager: 凭证管理器实例
        """
        self.credential_manager = credential_manager
        
    async def check_and_protect(
        self,
        credential_name: str,
        credential_data: Dict[str, Any],
        is_antigravity: bool = False
    ) -> bool:
        """检查配额并执行保护

        检查账号的高级模型配额是否低于阈值，
        如果低于阈值则禁用账号，
        如果配额已恢复则重新启用账号。

        Args:
            credential_name: 凭证名称
            credential_data: 凭证数据
            is_antigravity: 是否为 Antigravity 凭证

        Returns:
            True: 账号可用
            False: 账号被保护（禁用）
        """
        from config import (
            get_quota_protection_enabled,
            get_quota_protection_threshold,
            get_quota_protection_models
        )
        from src.antigravity_api import fetch_quota_info

        # 检查是否启用配额保护
        enabled = await get_quota_protection_enabled()
        if not enabled:
            return True  # 未启用保护，放行

        threshold = await get_quota_protection_threshold()
        monitored_models = await get_quota_protection_models()

        if not monitored_models:
            return True  # 没有监控模型，放行

        # [FIX 2026-01-17] 从 Google API 获取配额信息（使用内存缓存）
        # 原代码错误地从 credential_data.get("quota", {}) 读取，但 quota 不在数据库中
        access_token = credential_data.get("access_token") or credential_data.get("token")
        if not access_token:
            # 没有 access_token，检查是否之前因配额保护禁用
            return await self._handle_no_quota_data(
                credential_name, credential_data, is_antigravity
            )

        # 调用 fetch_quota_info（会自动使用内存缓存）
        quota_result = await fetch_quota_info(access_token, cache_key=credential_name)

        if not quota_result.get("success"):
            # 获取配额失败，检查是否之前因配额保护禁用
            return await self._handle_no_quota_data(
                credential_name, credential_data, is_antigravity
            )

        # 遍历配额信息中的模型
        models_data = quota_result.get("models", {})

        if not models_data:
            # 没有配额信息，检查是否之前因配额保护禁用
            return await self._handle_no_quota_data(
                credential_name, credential_data, is_antigravity
            )

        # 检查监控的模型配额
        for model_id, model_info in models_data.items():
            model_name = model_id
            # remaining 是小数（0.0-1.0），转换为百分比
            remaining_fraction = model_info.get("remaining", 1.0)
            percentage = remaining_fraction * 100

            # 只检查监控的模型
            if self._is_monitored_model(model_name, monitored_models):
                if percentage <= threshold:
                    log.warning(
                        f"[QuotaProtection] ⚠️ {credential_name} 的 {model_name} "
                        f"配额 ({percentage:.1f}%) <= 阈值 ({threshold}%)，触发保护"
                    )

                    # 禁用账号
                    await self._disable_credential(
                        credential_name, is_antigravity,
                        f"配额保护: {model_name} = {percentage:.1f}%"
                    )
                    return False

        # 所有监控模型都正常，检查是否需要恢复
        await self._try_restore_credential(
            credential_name, credential_data, is_antigravity
        )

        return True
        
    async def _handle_no_quota_data(
        self,
        credential_name: str,
        credential_data: Dict[str, Any],
        is_antigravity: bool
    ) -> bool:
        """处理没有配额数据的情况"""
        # 如果之前因配额保护禁用，保持禁用状态
        if credential_data.get("disabled", False):
            disabled_reason = credential_data.get("disabled_reason", "")
            if disabled_reason == self.DISABLE_REASON:
                log.debug(
                    f"[QuotaProtection] {credential_name} 之前因配额保护禁用，"
                    "无配额数据时保持禁用"
                )
                return False
        return True
        
    async def _try_restore_credential(
        self,
        credential_name: str,
        credential_data: Dict[str, Any],
        is_antigravity: bool
    ):
        """尝试恢复被保护的凭证"""
        # 检查是否因配额保护禁用
        if credential_data.get("disabled", False):
            disabled_reason = credential_data.get("disabled_reason", "")
            
            if disabled_reason == self.DISABLE_REASON:
                log.info(
                    f"[QuotaProtection] ✓ {credential_name} 配额已恢复，"
                    "自动启用账号"
                )
                await self._enable_credential(credential_name, is_antigravity)
        
    async def _disable_credential(
        self,
        credential_name: str,
        is_antigravity: bool,
        reason: str
    ):
        """禁用凭证"""
        try:
            # 更新状态：标记为禁用，并记录原因
            await self.credential_manager.update_credential_state(
                credential_name,
                {
                    "disabled": True,
                    "disabled_reason": self.DISABLE_REASON,
                    "disabled_at": time.time(),
                    "disabled_detail": reason
                },
                is_antigravity=is_antigravity
            )
            log.info(f"[QuotaProtection] 账号已禁用: {credential_name} ({reason})")
        except Exception as e:
            log.error(f"[QuotaProtection] 禁用账号失败 {credential_name}: {e}")
            
    async def _enable_credential(
        self,
        credential_name: str,
        is_antigravity: bool
    ):
        """启用凭证"""
        try:
            await self.credential_manager.update_credential_state(
                credential_name,
                {
                    "disabled": False,
                    "disabled_reason": None,
                    "disabled_at": None,
                    "disabled_detail": None
                },
                is_antigravity=is_antigravity
            )
            log.info(f"[QuotaProtection] ✓ 账号已恢复: {credential_name}")
        except Exception as e:
            log.error(f"[QuotaProtection] 启用账号失败 {credential_name}: {e}")
            
    def _is_monitored_model(self, model_name: str, monitored_list: List[str]) -> bool:
        """判断是否为监控的模型
        
        支持前缀匹配，例如 "claude-sonnet-4-5" 可以匹配 "claude-sonnet-4-5-xxx"
        """
        model_name_lower = model_name.lower()
        for monitored in monitored_list:
            if model_name_lower.startswith(monitored.lower()):
                return True
        return False
        
    async def scan_all_credentials(self, is_antigravity: bool = True):
        """扫描所有凭证，执行配额保护检查
        
        通常由后台调度器定期调用
        """
        log.info("[QuotaProtection] 开始扫描所有凭证配额...")
        
        try:
            # 获取所有凭证
            all_credentials = await self.credential_manager._storage_adapter.list_credentials(
                is_antigravity=is_antigravity
            )
            
            protected_count = 0
            restored_count = 0
            
            for cred_name in all_credentials:
                cred_data = await self.credential_manager._storage_adapter.get_credential(
                    cred_name, is_antigravity=is_antigravity
                )
                
                if not cred_data:
                    continue
                    
                was_disabled = cred_data.get("disabled", False)
                
                # 执行保护检查
                is_ok = await self.check_and_protect(
                    cred_name, cred_data, is_antigravity
                )
                
                if not is_ok and not was_disabled:
                    protected_count += 1
                elif is_ok and was_disabled:
                    # 可能已恢复
                    cred_data_new = await self.credential_manager._storage_adapter.get_credential(
                        cred_name, is_antigravity=is_antigravity
                    )
                    if cred_data_new and not cred_data_new.get("disabled", False):
                        restored_count += 1
            
            log.info(
                f"[QuotaProtection] 扫描完成: "
                f"新保护 {protected_count} 个账号, "
                f"恢复 {restored_count} 个账号"
            )
            
        except Exception as e:
            log.error(f"[QuotaProtection] 扫描失败: {e}")
