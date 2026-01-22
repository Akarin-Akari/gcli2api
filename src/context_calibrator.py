"""
Context Estimation Calibrator - 上下文 token 估算校准器

参考 Antigravity-Manager 的 `estimation_calibrator.rs` 设计：
- 记录「估算 token 数」与「真实 token 数」之间的偏差
- 使用指数滑动平均维护一个校准因子（calibration_factor）
- 之后所有估算值都乘以该因子，用于更准确地判断上下文压力

注意：
- 当前版本仅实现内存级别的校准，不强制持久化到磁盘
- 后续可以将统计信息落盘到 data/context_calibrator.json 以便重启后复用
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, asdict
from typing import Dict, Optional

from log import log


@dataclass
class CalibratorStats:
    """校准器统计信息数据结构"""

    total_estimated: int = 0
    total_actual: int = 0
    sample_count: int = 0
    calibration_factor: float = 2.0


class EstimationCalibrator:
    """
    Token 估算校准器（线程安全）

    - update(estimated, actual): 使用真实 token 数更新校准因子
    - calibrate(estimated): 返回校准后的估算值
    - get_factor(): 返回当前校准因子
    - get_stats(): 返回统计信息（用于调试/监控）
    """

    # 允许的校准因子范围（与 Rust 版一致）
    MIN_FACTOR = 0.8
    MAX_FACTOR = 4.0

    # 指数滑动平均权重（old * 0.6 + new * 0.4）
    OLD_WEIGHT = 0.6
    NEW_WEIGHT = 0.4

    def __init__(self, *, persist_path: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._stats = CalibratorStats()
        self._persist_path = persist_path

        if self._persist_path:
            self._load()

    # ====================== 公共 API ======================

    def update(self, estimated: int, actual: int) -> None:
        """
        使用一次真实请求的数据更新校准因子

        Args:
            estimated: 请求前的估算 token 数
            actual: 上游返回的真实 token 数（prompt + completion）
        """
        if estimated <= 0 or actual <= 0:
            return

        with self._lock:
            self._stats.total_estimated += int(estimated)
            self._stats.total_actual += int(actual)
            self._stats.sample_count += 1

            # 计算新的 raw 因子，并限制在合理区间
            new_factor = float(actual) / float(estimated)
            clamped = max(self.MIN_FACTOR, min(self.MAX_FACTOR, new_factor))

            old = self._stats.calibration_factor or 2.0
            updated = old * self.OLD_WEIGHT + clamped * self.NEW_WEIGHT
            self._stats.calibration_factor = updated

            log.info(
                "[Calibrator] Updated factor: %.2f -> %.2f (raw=%.2f, samples=%d)",
                old,
                updated,
                new_factor,
                self._stats.sample_count,
            )

            self._save()

    def calibrate(self, estimated: int) -> int:
        """
        返回校准后的估算值

        Args:
            estimated: 原始估算 token 数
        """
        if estimated <= 0:
            return 0

        factor = self.get_factor()
        calibrated = int((float(estimated) * factor) + 0.9999)
        return max(1, calibrated)

    def get_factor(self) -> float:
        """获取当前校准因子"""
        with self._lock:
            factor = self._stats.calibration_factor
        # 容错：确保返回值始终在范围内
        if factor <= 0:
            factor = 2.0
        return max(self.MIN_FACTOR, min(self.MAX_FACTOR, factor))

    def get_stats(self) -> Dict[str, float]:
        """获取当前统计信息（用于调试/监控）"""
        with self._lock:
            stats = {
                "total_estimated": int(self._stats.total_estimated),
                "total_actual": int(self._stats.total_actual),
                "sample_count": int(self._stats.sample_count),
                "calibration_factor": float(self._stats.calibration_factor),
            }
        return stats

    # ====================== 持久化支持（可选） ======================

    def _load(self) -> None:
        """从磁盘加载历史统计信息（如果存在）"""
        if not self._persist_path:
            return
        try:
            if os.path.exists(self._persist_path):
                with open(self._persist_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._stats = CalibratorStats(
                    total_estimated=int(data.get("total_estimated", 0)),
                    total_actual=int(data.get("total_actual", 0)),
                    sample_count=int(data.get("sample_count", 0)),
                    calibration_factor=float(data.get("calibration_factor", 2.0)),
                )
                log.info(
                    "[Calibrator] Loaded stats from %s (factor=%.2f, samples=%d)",
                    self._persist_path,
                    self._stats.calibration_factor,
                    self._stats.sample_count,
                )
        except Exception as e:
            log.warning("[Calibrator] Failed to load stats from %s: %s", self._persist_path, e)

    def _save(self) -> None:
        """将当前统计信息持久化到磁盘（最佳努力，失败不影响主流程）"""
        if not self._persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self._stats), f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning("[Calibrator] Failed to save stats to %s: %s", self._persist_path, e)


_GLOBAL_CALIBRATOR: Optional[EstimationCalibrator] = None
_CALIBRATOR_LOCK = threading.Lock()


def get_global_calibrator() -> EstimationCalibrator:
    """
    获取全局单例校准器

    - 默认持久化路径：data/context_calibrator.json
    - 若目录不可写，自动降级为内存模式
    """
    global _GLOBAL_CALIBRATOR
    if _GLOBAL_CALIBRATOR is not None:
        return _GLOBAL_CALIBRATOR

    with _CALIBRATOR_LOCK:
        if _GLOBAL_CALIBRATOR is None:
            persist_path = os.path.join("data", "context_calibrator.json")
            _GLOBAL_CALIBRATOR = EstimationCalibrator(persist_path=persist_path)
        return _GLOBAL_CALIBRATOR

