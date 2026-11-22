from datetime import datetime

from sqlalchemy import case

from ..models import (
    Batch,
    BatchItem,
    DerivedTrend,
    Device,
    Partition,
    QualityConclusion,
    ReceiveLog,
)
from . import alerts, judge as judge_mod, pending as pending_svc, rules as rules_svc
from .common import get_state


def evaluate(db, log: ReceiveLog, device: Device | None, batch: Batch) -> QualityConclusion:
    """质量服务：范围 / 时序 / 设备状态三项检查，输出结论。

    若该因子当前有规则不可用（维护/撤销），则不给出 normal/exceed，
    而是写入 pending_judgment（保留读数，未确认），并放入待判队列等待补判。
    """
    factor = device.factor if device else "*"
    missing = rules_svc.unavailable_rules(db, factor)

    if missing:
        # 规则不可执行：读数保留为待判，绝不当作合格或超标
        cfg = rules_svc.get_pending_config(db)
        # 路线以维护窗口选择为准；无窗口（如目录标记不可用）时退回默认路线
        win_strategy = next(
            (m.get("strategy") for m in missing if m.get("strategy")), None
        )
        if win_strategy is None:
            # 从维护窗口查询（missing 中携带 window id）
            win_id = next((m.get("maintenance_window_id") for m in missing if m.get("maintenance_window_id")), None)
            if win_id is not None:
                from ..models import MaintenanceWindow

                w = db.get(MaintenanceWindow, win_id)
                win_strategy = w.strategy if w else None
        strategy = win_strategy or cfg.default_strategy or "snapshot"
        concl = QualityConclusion(
            receive_log_id=log.id,
            batch_id=batch.id,
            device_point=log.device_point,
            partition_key=log.partition_key,
            policy_version=batch.policy_version,
            range_ok=None,
            ts_ok=None,
            device_status_ok=None,
            gap_detected=False,
            conclusion="pending_judgment",
            confirmed=False,
            detail=(
                f"规则不可执行（{', '.join(m['rule_code'] for m in missing)}），"
                f"读数保留待判，规则恢复后补判"
            )[:512],
            deferred=False,
            missing_rules=missing,
            rule_version=None,
        )
        db.add(concl)
        db.flush()
        pending_svc.enqueue_pending(db, log, device, missing, strategy)
        return concl

    res = judge_mod.judge(
        value=log.value,
        reading_ts=log.reading_ts,
        received_at=log.received_at,
        device_status=log.device_status_at_receipt,
        threshold_min=device.threshold_min if device else None,
        threshold_max=device.threshold_max if device else None,
    )

    # 采样间隔缺口检查（信息项，不改变结论）
    gap = False
    if device is not None:
        prev = (
            db.query(ReceiveLog)
            .filter(
                ReceiveLog.device_point == log.device_point,
                ReceiveLog.status == "confirmed",
                ReceiveLog.reading_ts < log.reading_ts,
            )
            .order_by(ReceiveLog.reading_ts.desc())
            .first()
        )
        if prev is not None:
            gap = (log.reading_ts - prev.reading_ts).total_seconds() > 2 * device.sampling_period_sec

    return QualityConclusion(
        receive_log_id=log.id,
        batch_id=batch.id,
        device_point=log.device_point,
        partition_key=log.partition_key,
        policy_version=batch.policy_version,  # 结论引用批次创建时的策略版本
        range_ok=res["range_ok"],
        ts_ok=res["ts_ok"],
        device_status_ok=res["device_status_ok"],
        gap_detected=gap,
        conclusion=res["conclusion"],
        confirmed=res["confirmed"],
        detail=res["detail"],
    )


def quality_tick(db) -> bool:
    """处理一个待处理批次（高风险分区优先）。返回是否处理了批次。"""
    state = get_state(db)
    batch = (
        db.query(Batch)
        .filter(Batch.status == "open")
        .join(Partition, Batch.partition_key == Partition.partition_key)
        .order_by(case((Partition.risk_class == "high", 0), else_=1), Batch.id)
        .first()
    )
    if batch is None:
        return False
    batch.status = "processing"
    db.commit()

    rows = (
        db.query(BatchItem, ReceiveLog)
        .join(ReceiveLog, BatchItem.receive_log_id == ReceiveLog.id)
        .filter(BatchItem.batch_id == batch.id)
        .order_by(ReceiveLog.id)
        .all()
    )
    devices = {d.point_code: d for d in db.query(Device).all()}
    max_log_id = 0
    for item, log in rows:
        device = devices.get(log.device_point)
        # 同一日志只判定一次（补判/重跑幂等）
        if db.query(QualityConclusion).filter(QualityConclusion.receive_log_id == log.id).first() is not None:
            max_log_id = max(max_log_id, log.id)
            continue
        conclusion = evaluate(db, log, device, batch)
        db.add(conclusion)
        db.flush()
        alerts.maybe_alert(db, conclusion, log, device)
        # 低风险衍生计算（滚动均值）：降级模式下暂停，把写入额度让给高风险分区
        if device is not None and device.risk_level == "low":
            if state.degraded:
                state.trends_paused_count = (state.trends_paused_count or 0) + 1
            else:
                window = log.reading_ts.replace(minute=0, second=0, microsecond=0)
                db.add(
                    DerivedTrend(
                        device_point=log.device_point,
                        window_start=window,
                        avg_value=log.value,
                        sample_count=1,
                    )
                )
        max_log_id = max(max_log_id, log.id)

    batch.status = "done"
    batch.processed_at = datetime.utcnow()
    part = db.get(Partition, batch.partition_key)
    if part is not None and max_log_id > (part.processed_cursor or 0):
        part.processed_cursor = max_log_id
    db.commit()
    return True
