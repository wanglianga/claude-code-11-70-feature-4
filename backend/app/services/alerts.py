from datetime import datetime

from ..models import Alert, QualityConclusion, ReceiveLog


def maybe_alert(
    db,
    conclusion: QualityConclusion,
    log: ReceiveLog,
    device,
    deferred: bool = False,
    rejudge_run_id: int | None = None,
) -> Alert | None:
    """告警服务：只消费 已确认 + 超标 + 高风险 的质量结论。

    设备校准/维护期间产生的 pending_review（未确认）结论、规则维护期间保留为
    pending_judgment 的读数都不会进入告警。补判产生的告警标记 deferred=True，
    以便与实时告警区分（延迟告警重演）。
    """
    if not conclusion.confirmed:
        return None
    if conclusion.conclusion != "exceed":
        return None
    if device is None or device.risk_level != "high":
        return None
    # 幂等：同一结论至多一条告警
    existing = db.query(Alert).filter(Alert.conclusion_id == conclusion.id).first()
    if existing is not None:
        return existing
    now = datetime.utcnow()
    latency_ms = int((now - log.received_at).total_seconds() * 1000)
    alert = Alert(
        conclusion_id=conclusion.id,
        receive_log_id=log.id,
        device_point=log.device_point,
        level="high",
        message=(
            f"{device.name}（{log.device_point}）{device.factor}={log.value}{device.unit} "
            f"超出阈值 [{device.threshold_min}, {device.threshold_max}]"
        ),
        received_at=log.received_at,
        latency_ms=latency_ms,
        deferred=deferred,
        rejudge_run_id=rejudge_run_id,
        rule_version=conclusion.rule_version,
    )
    db.add(alert)
    return alert
