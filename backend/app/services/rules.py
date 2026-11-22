"""规则目录与可用性：依赖、可用状态、发布版本、维护窗口与路线建议。

质量判定所需的规则按“因子 + 检查维度”组织。任一所适用规则不可用（维护中 / 被撤销）
时，读数不能给出正常或超标结论，只能保留为待判。
"""
from datetime import datetime, timedelta

from sqlalchemy import or_

from .. import config
from ..models import (
    Device,
    DeviceVersion,
    MaintenanceWindow,
    Partition,
    PendingConfig,
    PendingItem,
    ReceiveLog,
    RuleCatalog,
    RuleVersion,
    SampleReportBatch,
)


# ---------------------------------------------------------------------------
# 规则目录
# ---------------------------------------------------------------------------
def applicable_rules(db, factor: str) -> list[RuleCatalog]:
    """适用于某因子的规则：全局规则 + 该因子的规则。"""
    return (
        db.query(RuleCatalog)
        .filter(or_(RuleCatalog.scope == "global", RuleCatalog.factor == factor))
        .order_by(RuleCatalog.rule_code)
        .all()
    )


def active_maintenance(db, rule_code: str) -> MaintenanceWindow | None:
    return (
        db.query(MaintenanceWindow)
        .filter(MaintenanceWindow.rule_code == rule_code, MaintenanceWindow.status == "active")
        .first()
    )


def is_available(db, rule: RuleCatalog) -> bool:
    """规则可执行：目录标记可用且没有生效中的维护窗口。"""
    if not rule.available:
        return False
    return active_maintenance(db, rule.rule_code) is None


def unavailable_rules(db, factor: str) -> list[dict]:
    """返回该因子下当前不可用规则的快照集合（供待判队列记录缺失规则）。"""
    out = []
    for rule in applicable_rules(db, factor):
        if is_available(db, rule):
            continue
        win = active_maintenance(db, rule.rule_code)
        out.append(
            {
                "rule_code": rule.rule_code,
                "name": rule.name,
                "factor": rule.factor,
                "check_dimension": rule.check_dimension,
                "version": rule.current_version,
                "reason": "维护窗口" if win else "目录标记不可用",
                "maintenance_window_id": win.id if win else None,
                "strategy": win.strategy if win else None,
                "since": win.started_at.isoformat() if win and win.started_at else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 规则版本
# ---------------------------------------------------------------------------
def current_rule_version(db, rule_code: str) -> RuleVersion | None:
    rule = db.query(RuleCatalog).filter(RuleCatalog.rule_code == rule_code).first()
    if rule is None:
        return None
    return (
        db.query(RuleVersion)
        .filter(RuleVersion.rule_code == rule_code, RuleVersion.version == rule.current_version)
        .first()
    )


def version_revoked(db, rule_code: str, version: int) -> bool:
    rv = (
        db.query(RuleVersion)
        .filter(RuleVersion.rule_code == rule_code, RuleVersion.version == version)
        .first()
    )
    return bool(rv and rv.revoked)


def publish_rule_version(db, rule_code: str, params: dict, note: str = "") -> RuleVersion:
    rule = db.query(RuleCatalog).filter(RuleCatalog.rule_code == rule_code).first()
    if rule is None:
        raise ValueError("规则不存在")
    new_ver = (rule.current_version or 0) + 1
    rule.current_version = new_ver
    rv = RuleVersion(
        rule_code=rule_code, version=new_ver, status="published", params=params or {}, note=note
    )
    db.add(rv)
    db.flush()
    return rv


def revoke_rule_version(db, rule_code: str, version: int, reason: str) -> RuleVersion | None:
    rv = (
        db.query(RuleVersion)
        .filter(RuleVersion.rule_code == rule_code, RuleVersion.version == version)
        .first()
    )
    if rv is None:
        return None
    rv.revoked = True
    rv.status = "revoked"
    rv.revoked_reason = reason
    return rv


# ---------------------------------------------------------------------------
# 维护窗口
# ---------------------------------------------------------------------------
def open_maintenance(
    db, rule_code: str, strategy: str, reason: str, partitions: list[str] | None, created_by: str = ""
) -> MaintenanceWindow:
    rule = db.query(RuleCatalog).filter(RuleCatalog.rule_code == rule_code).first()
    if rule is None:
        raise ValueError("规则不存在")
    if strategy not in ("snapshot", "event_log"):
        raise ValueError("路线须为 snapshot 或 event_log")
    existing = active_maintenance(db, rule_code)
    if existing is not None:
        raise ValueError("该规则已在维护中")
    rule.available = False
    win = MaintenanceWindow(
        rule_code=rule_code,
        factor=rule.factor,
        check_dimension=rule.check_dimension,
        strategy=strategy,
        reason=reason,
        affected_partitions=partitions or [],
        created_by=created_by,
    )
    db.add(win)
    db.flush()
    return win


def close_maintenance(db, window_id: int) -> MaintenanceWindow | None:
    win = db.get(MaintenanceWindow, window_id)
    if win is None:
        return None
    win.status = "closed"
    win.closed_at = datetime.utcnow()
    rule = db.query(RuleCatalog).filter(RuleCatalog.rule_code == win.rule_code).first()
    if rule is not None:
        # 只有不存在其他生效窗口时才恢复可用
        other = (
            db.query(MaintenanceWindow)
            .filter(
                MaintenanceWindow.rule_code == win.rule_code,
                MaintenanceWindow.status == "active",
                MaintenanceWindow.id != win.id,
            )
            .first()
        )
        rule.available = other is None
    counts = (
        db.query(PendingItem)
        .filter(PendingItem.maintenance_window_id == win.id)
        .all()
    )
    win.pending_count = len(counts)
    win.rejudged_count = sum(1 for p in counts if p.status == "rejudged")
    db.flush()
    return win


# ---------------------------------------------------------------------------
# 设备版本（当时适用设备状态）
# ---------------------------------------------------------------------------
def record_device_version(db, device: Device, note: str = "") -> DeviceVersion:
    last = (
        db.query(DeviceVersion)
        .filter(DeviceVersion.device_point == device.point_code)
        .order_by(DeviceVersion.version.desc())
        .first()
    )
    ver = (last.version + 1) if last else 1
    dv = DeviceVersion(
        device_point=device.point_code,
        version=ver,
        status=device.status,
        risk_level=device.risk_level,
        threshold_min=device.threshold_min,
        threshold_max=device.threshold_max,
        partition_key=device.partition_key,
        note=note,
    )
    db.add(dv)
    db.flush()
    return dv


def device_version_at(db, point_code: str, ts: datetime) -> DeviceVersion | None:
    """按事件时间重建当时适用的设备版本（event_log 路线的重关联）。"""
    return (
        db.query(DeviceVersion)
        .filter(DeviceVersion.device_point == point_code, DeviceVersion.created_at <= ts)
        .order_by(DeviceVersion.created_at.desc(), DeviceVersion.version.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# 待判配置与路线建议
# ---------------------------------------------------------------------------
def get_pending_config(db) -> PendingConfig:
    cfg = db.get(PendingConfig, 1)
    if cfg is None:
        cfg = PendingConfig(
            id=1,
            default_strategy=config.DEFAULT_PENDING_STRATEGY,
            legal_deadline_hours=config.PENDING_LEGAL_DEADLINE_HOURS,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def strategy_recommendation(db) -> dict:
    """根据规则维护平均跨度、日志检索代价、需复现监管批次数给出路线建议。

    - snapshot：维护期封存输入快照与设备版本；恢复后直接补判。
      写入/存储负担高，但能稳定界定“当时判断范围”，适合长跨度、宽范围维护。
    - event_log：仅依赖持久接收日志按事件时间重建输入；额外副本少，
      但大范围维护结束时需扫描并重新关联设备状态，适合短跨度、窄范围维护。
    """
    rows = db.query(MaintenanceWindow).all()
    spans = []
    for w in rows:
        if w.started_at:
            end = w.closed_at or datetime.utcnow()
            spans.append(max(0.0, (end - w.started_at).total_seconds()))
    avg_span_h = (sum(spans) / len(spans) / 3600.0) if spans else 0.0

    # 日志检索代价：维护窗口影响分区内的接收日志行数（维护结束需扫描的范围）
    affected = set()
    for w in rows:
        for p in (w.affected_partitions or []):
            affected.add(p)
    if affected:
        log_rows = db.query(ReceiveLog).filter(ReceiveLog.partition_key.in_(affected)).count()
    else:
        log_rows = db.query(ReceiveLog).count()

    sample_batches = db.query(SampleReportBatch).count()

    reasons = []
    score_snapshot = 0
    if avg_span_h >= config.STRATEGY_SPAN_THRESHOLD_HOURS:
        score_snapshot += 1
        reasons.append(f"规则维护平均跨度 {avg_span_h:.1f}h ≥ {config.STRATEGY_SPAN_THRESHOLD_HOURS}h，长跨度需稳定界定判断范围")
    else:
        reasons.append(f"规则维护平均跨度 {avg_span_h:.1f}h，较短")
    if log_rows >= config.STRATEGY_LOG_SCAN_THRESHOLD:
        score_snapshot += 1
        reasons.append(f"维护影响分区日志 {log_rows} 行 ≥ {config.STRATEGY_LOG_SCAN_THRESHOLD}，重建扫描代价高")
    else:
        reasons.append(f"维护影响分区日志 {log_rows} 行，重建扫描代价可接受")
    if sample_batches >= config.STRATEGY_BATCH_THRESHOLD:
        score_snapshot += 1
        reasons.append(f"需复现监管批次 {sample_batches} 个 ≥ {config.STRATEGY_BATCH_THRESHOLD}，需稳定界定当时判断范围")
    else:
        reasons.append(f"需复现监管批次 {sample_batches} 个，数量较少")

    recommended = "snapshot" if score_snapshot >= 2 else "event_log"
    return {
        "recommended": recommended,
        "avg_maintenance_span_hours": round(avg_span_h, 2),
        "affected_log_rows": log_rows,
        "sample_batch_count": sample_batches,
        "thresholds": {
            "span_hours": config.STRATEGY_SPAN_THRESHOLD_HOURS,
            "log_rows": config.STRATEGY_LOG_SCAN_THRESHOLD,
            "sample_batches": config.STRATEGY_BATCH_THRESHOLD,
        },
        "reasons": reasons,
        "snapshot_score": score_snapshot,
    }


def partition_risk(db, partition_key: str) -> str:
    p = db.get(Partition, partition_key)
    return p.risk_class if p else "low"
