"""待判队列：规则不可用时封存读数与缺失规则集合，规则恢复后补判。

两条路线：
  snapshot   —— 入队时封存窗口输入快照与适用设备版本；恢复后直接补判（稳定界定当时范围，写入/存储高）
  event_log  —— 只记录接收日志 id；恢复时按事件时间从持久接收日志重建输入并重新关联设备状态
                （额外副本少，大范围维护结束时需扫描）
两种路线都把读数保留为待判（pending），绝不写入 normal/exceed 结论，直到补判。
"""
from datetime import datetime, timedelta

from .. import config
from ..models import (
    Device,
    PendingConfig,
    PendingItem,
    ReceiveLog,
)
from . import alerts as alerts_svc
from . import judge as judge_mod
from . import rules as rules_svc


def _legal_deadline(cfg: PendingConfig, received_at: datetime) -> datetime:
    return received_at + timedelta(hours=cfg.legal_deadline_hours or 24)


def _window_key(missing: list[dict], factor: str, reading_ts: datetime) -> str:
    codes = ",".join(sorted(m.get("rule_code", "") for m in missing))
    bucket = reading_ts.replace(minute=0, second=0, microsecond=0).isoformat()
    return f"{codes}|{factor}|{bucket}"


def enqueue_pending(
    db, log: ReceiveLog, device: Device | None, missing: list[dict], strategy: str
) -> PendingItem | None:
    """把一条无法判定的读数放入待判队列。已存在则幂等返回。"""
    existing = db.query(PendingItem).filter(PendingItem.receive_log_id == log.id).first()
    if existing is not None:
        return existing
    cfg = rules_svc.get_pending_config(db)
    factor = device.factor if device else (missing[0].get("factor") if missing else "*")
    win_id = next((m.get("maintenance_window_id") for m in missing if m.get("maintenance_window_id")), None)

    snapshot = None
    device_version_id = None
    if strategy == "snapshot":
        # 封存当时适用设备版本与窗口输入
        if device is not None:
            dv = rules_svc.record_device_version(db, device, note="待判窗口封存")
            device_version_id = dv.id
        snapshot = {
            "reading": {
                "receive_log_id": log.id,
                "device_point": log.device_point,
                "factor": factor,
                "value": log.value,
                "reading_ts": log.reading_ts.isoformat(),
                "received_at": log.received_at.isoformat(),
                "session_uuid": log.session_uuid,
                "gateway_code": log.gateway_code,
                "source_seq": log.source_seq,
                "fingerprint": log.fingerprint,
                "risk_level": log.risk_level,
                "partition_key": log.partition_key,
            },
            "device": {
                "status": device.status if device else log.device_status_at_receipt,
                "risk_level": device.risk_level if device else log.risk_level,
                "threshold_min": device.threshold_min if device else None,
                "threshold_max": device.threshold_max if device else None,
                "partition_key": device.partition_key if device else log.partition_key,
            },
            "missing_rules": missing,
            "captured_at": datetime.utcnow().isoformat(),
        }

    item = PendingItem(
        receive_log_id=log.id,
        device_point=log.device_point,
        partition_key=log.partition_key,
        risk_level=log.risk_level,
        factor=factor,
        reading_ts=log.reading_ts,
        received_at=log.received_at,
        missing_rules=missing,
        input_snapshot=snapshot,
        device_version_id=device_version_id,
        device_status=log.device_status_at_receipt,
        device_threshold_min=device.threshold_min if device else None,
        device_threshold_max=device.threshold_max if device else None,
        window_key=_window_key(missing, factor, log.reading_ts),
        legal_deadline=_legal_deadline(cfg, log.received_at),
        strategy=strategy,
        status="pending",
        maintenance_window_id=win_id,
    )
    db.add(item)
    db.flush()
    return item


def _reconstruct_input(db, item: PendingItem) -> dict:
    """按路线取得判定输入：snapshot 直接用封存副本；event_log 从接收日志按事件时间重建。"""
    if item.strategy == "snapshot" and item.input_snapshot:
        snap = item.input_snapshot
        r = snap["reading"]
        d = snap["device"]
        return {
            "value": r["value"],
            "reading_ts": datetime.fromisoformat(r["reading_ts"]),
            "received_at": datetime.fromisoformat(r["received_at"]),
            "device_status": d["status"],
            "threshold_min": d["threshold_min"],
            "threshold_max": d["threshold_max"],
            "reconstructed": False,
        }
    # event_log：从持久接收日志读取原始读数，并按事件时间重新关联设备状态
    log = db.get(ReceiveLog, item.receive_log_id)
    if log is None:
        raise LookupError("接收日志缺失，无法重建输入")
    device = db.query(Device).filter(Device.point_code == log.device_point).first()
    dv = rules_svc.device_version_at(db, log.device_point, log.reading_ts)
    if dv is not None:
        status, tmin, tmax = dv.status, dv.threshold_min, dv.threshold_max
        ver_id = dv.id
    elif device is not None:
        status, tmin, tmax = device.status, device.threshold_min, device.threshold_max
        ver_id = None
    else:
        status, tmin, tmax = log.device_status_at_receipt, None, None
        ver_id = None
    item.device_version_id = ver_id
    return {
        "value": log.value,
        "reading_ts": log.reading_ts,
        "received_at": log.received_at,
        "device_status": status,
        "threshold_min": tmin,
        "threshold_max": tmax,
        "reconstructed": True,
    }


def _rules_blocked(db, item: PendingItem) -> tuple[bool, str]:
    """规则版本在维护中途被撤销时，禁止以替代版本静默覆盖。

    仅当用户已显式指定替代版本（resolved_version）时才放行该规则。
    """
    for m in item.missing_rules or []:
        code = m.get("rule_code")
        ver = m.get("version")
        if not code:
            continue
        if m.get("resolved_version"):
            # 用户已显式指定替代版本，视为已处理撤销问题
            continue
        if ver is not None and rules_svc.version_revoked(db, code, ver):
            return True, f"规则 {code} v{ver} 已在维护中途撤销，禁止以替代版本静默覆盖，需显式处理"
        cur = rules_svc.current_rule_version(db, code)
        if cur is None or cur.revoked:
            return True, f"规则 {code} 当前发布版本已撤销，需显式指定替代版本或处理方式"
    return False, ""


def rejudgeable_items(db, strategy_filter: str | None = None) -> list[PendingItem]:
    """可补判的待判项：所缺规则现已全部可用。"""
    q = db.query(PendingItem).filter(PendingItem.status == "pending")
    if strategy_filter and strategy_filter != "all":
        q = q.filter(PendingItem.strategy == strategy_filter)
    candidates = q.all()
    ready = []
    for item in candidates:
        factor = item.factor or "*"
        if not rules_svc.unavailable_rules(db, factor):
            ready.append(item)
    return ready


def _order_key(item: PendingItem, now: datetime):
    """补判次序：先实时高风险读数，再按法定时限、再按时间顺序。"""
    from .rules import partition_risk

    live_high = item.risk_level == "high" and item.received_at and (now - item.received_at) <= timedelta(
        minutes=config.ALERT_LATENCY_TARGET_MIN
    )
    tier = 0 if live_high else 1
    deadline = item.legal_deadline or item.received_at
    return (tier, deadline, item.reading_ts, item.id)


def _rejudge_one(db, item: PendingItem, run_id: int) -> tuple[str, object | None]:
    """补判单条：返回 (status, alert)。blocked 时不产生结论。"""
    blocked, reason = _rules_blocked(db, item)
    if blocked:
        item.status = "blocked"
        item.blocked_reason = reason
        item.rejudge_run_id = run_id
        return "blocked", None

    reconstructed = _reconstruct_input(db, item)
    device = db.query(Device).filter(Device.point_code == item.device_point).first()
    res = judge_mod.judge(
        value=reconstructed["value"],
        reading_ts=reconstructed["reading_ts"],
        received_at=reconstructed["received_at"],
        device_status=reconstructed["device_status"],
        threshold_min=reconstructed["threshold_min"],
        threshold_max=reconstructed["threshold_max"],
    )
    from ..models import QualityConclusion

    # 补判时记录当前规则版本（供旧新规则差异对照）
    cur_versions = []
    for m in item.missing_rules or []:
        code = m.get("rule_code")
        if code:
            rv = rules_svc.current_rule_version(db, code)
            cur_versions.append({"rule_code": code, "old_version": m.get("version"), "new_version": rv.version if rv else None})

    detail = res["detail"] + (
        f"；规则恢复后补判（缺失规则 {len(item.missing_rules or [])} 项）"
        f"{'，输入由接收日志按事件时间重建' if reconstructed['reconstructed'] else '，输入取自封存快照'}"
    )
    # 每条接收日志保留唯一结论：把原 pending_judgment 结论就地转为补判结论，
    # 原待判事实由 PendingItem（缺失规则/快照/策略）完整留存，便于追溯“当时未能判断”。
    concl = (
        db.query(QualityConclusion)
        .filter(QualityConclusion.receive_log_id == item.receive_log_id)
        .first()
    )
    if concl is None:
        concl = QualityConclusion(
            receive_log_id=item.receive_log_id,
            batch_id=None,
            device_point=item.device_point,
            partition_key=item.partition_key,
            policy_version=None,
        )
        db.add(concl)
    concl.device_point = item.device_point
    concl.partition_key = item.partition_key
    concl.range_ok = res["range_ok"]
    concl.ts_ok = res["ts_ok"]
    concl.device_status_ok = res["device_status_ok"]
    concl.gap_detected = False
    concl.conclusion = res["conclusion"]
    concl.confirmed = res["confirmed"]
    concl.detail = detail[:512]
    concl.deferred = True
    concl.missing_rules = item.missing_rules
    concl.rule_version = cur_versions[0]["new_version"] if cur_versions else None
    concl.rejudge_run_id = run_id
    db.flush()

    log = db.get(ReceiveLog, item.receive_log_id)
    alert = None
    if log is not None:
        alert = alerts_svc.maybe_alert(db, concl, log, device, deferred=True, rejudge_run_id=run_id)

    item.status = "rejudged"
    item.rejudge_run_id = run_id
    item.rejudge_conclusion_id = concl.id
    item.rejudged_at = datetime.utcnow()
    return "rejudged", alert


def run_rejudge(
    db, trigger: str = "auto", strategy_filter: str | None = None
) -> dict:
    """执行一次补判调度，返回运行摘要。"""
    from ..models import RejudgeRun

    ready = rejudgeable_items(db, strategy_filter=strategy_filter)
    if not ready:
        return {"total": 0, "processed": 0, "alerted": 0, "blocked": 0, "run_id": None, "order_preview": []}

    now = datetime.utcnow()
    ready.sort(key=lambda it: _order_key(it, now))
    limited = ready[: config.REJUDGE_TICK_LIMIT]
    preview = [
        {
            "pending_id": it.id,
            "receive_log_id": it.receive_log_id,
            "device_point": it.device_point,
            "factor": it.factor,
            "window_key": it.window_key,
            "risk_level": it.risk_level,
            "legal_deadline": it.legal_deadline.isoformat() if it.legal_deadline else None,
            "reading_ts": it.reading_ts.isoformat() if it.reading_ts else None,
            "live_high": bool(it.risk_level == "high" and (now - it.received_at) <= timedelta(minutes=15)),
        }
        for it in limited[:20]
    ]
    # 按因子 / 时段（窗口桶）/ 风险 汇总积压，用于展示补判调度如何处理积压
    groups: dict[tuple, int] = {}
    for it in limited:
        bucket = (it.reading_ts or now).replace(minute=0, second=0, microsecond=0).isoformat()
        key = (it.factor or "*", bucket, it.risk_level or "low")
        groups[key] = groups.get(key, 0) + 1
    backlog_groups = [
        {"factor": k[0], "window_bucket": k[1], "risk_level": k[2], "count": v}
        for k, v in sorted(groups.items(), key=lambda kv: (kv[0][2] != "high", kv[0][1], kv[0][0]))
    ]

    run = RejudgeRun(
        trigger=trigger,
        strategy=strategy_filter or "all",
        rule_codes=sorted({m.get("rule_code") for it in limited for m in (it.missing_rules or []) if m.get("rule_code")}),
        ordered_by="live_high_risk,legal_deadline,reading_ts",
        total=len(limited),
        order_preview=preview,
        status="running",
    )
    db.add(run)
    db.flush()

    alerted = blocked = 0
    for item in limited:
        try:
            status, alert = _rejudge_one(db, item, run.id)
            if status == "blocked":
                blocked += 1
            if alert is not None:
                alerted += 1
        except Exception as exc:  # 单条失败不中断整批
            item.blocked_reason = f"补判异常：{exc}"[:256]
            blocked += 1
        db.flush()
    run.processed = len(limited)
    run.alerted = alerted
    run.blocked = blocked
    run.finished_at = datetime.utcnow()
    run.status = "done"
    db.commit()
    return {
        "run_id": run.id,
        "total": run.total,
        "processed": run.processed,
        "alerted": run.alerted,
        "blocked": run.blocked,
        "order_preview": preview,
        "backlog_groups": backlog_groups,
    }


def rejudge_tick(db) -> int:
    """后台巡检：仅在存在可补判项时运行一次。"""
    ready = rejudgeable_items(db)
    if not ready:
        return 0
    res = run_rejudge(db, trigger="auto")
    return res.get("processed", 0)


def resolve_blocked(db, pending_id: int, rule_versions: dict | None, user: str) -> PendingItem | None:
    """显式处理被撤销阻塞的待判项：允许指定替代版本后补判，禁止静默覆盖。"""
    item = db.get(PendingItem, pending_id)
    if item is None or item.status != "blocked":
        return None
    # 显式指定替代版本后，重新打开为 pending，并刷新缺失规则记录中的版本
    if rule_versions:
        for m in item.missing_rules or []:
            code = m.get("rule_code")
            if code in rule_versions:
                m["resolved_version"] = rule_versions[code]
    item.status = "pending"
    item.blocked_reason = (item.blocked_reason or "") + f" | 已由 {user} 显式指定替代版本处理"
    db.commit()
    return item


# ---------------------------------------------------------------------------
# 窗口视图（控制台展示）
# ---------------------------------------------------------------------------
def pending_windows(db, status_filter: str | None = None) -> list[dict]:
    """按 window_key 聚合待判窗口，给出输入完整性与缺失规则。"""
    q = db.query(PendingItem)
    if status_filter:
        q = q.filter(PendingItem.status == status_filter)
    items = q.all()
    windows: dict[str, dict] = {}
    for it in items:
        key = it.window_key or f"item-{it.id}"
        w = windows.get(key)
        if w is None:
            w = windows[key] = {
                "window_key": key,
                "factor": it.factor,
                "partition_key": it.partition_key,
                "risk_level": it.risk_level,
                "strategy": it.strategy,
                "missing_rules": it.missing_rules or [],
                "window_start": it.reading_ts.isoformat() if it.reading_ts else None,
                "legal_deadline": it.legal_deadline.isoformat() if it.legal_deadline else None,
                "total": 0,
                "pending": 0,
                "rejudged": 0,
                "blocked": 0,
                "with_snapshot": 0,
                "with_device_version": 0,
                "completeness": 0.0,
                "maintenance_window_id": it.maintenance_window_id,
            }
        w["total"] += 1
        w[it.status] = w.get(it.status, 0) + 1
        if it.input_snapshot is not None:
            w["with_snapshot"] += 1
        if it.device_version_id is not None:
            w["with_device_version"] += 1
    for w in windows.values():
        total = w["total"] or 1
        members = [it for it in items if (it.window_key or f"item-{it.id}") == w["window_key"]]
        if w["strategy"] == "snapshot":
            # 快照路线：输入完整性 = 有快照且有设备版本的比例
            complete = sum(1 for it in members if it.input_snapshot is not None and it.device_version_id is not None)
            w["completeness"] = round(complete / total, 3)
            w["completeness_basis"] = "快照+设备版本"
        else:
            # 事件日志路线：完整性 = 持久日志仍在 且 事件时间可重新关联到设备版本 的比例
            recon = 0
            for it in members:
                log = db.get(ReceiveLog, it.receive_log_id)
                if log is None:
                    continue
                if rules_svc.device_version_at(db, it.device_point, it.reading_ts) is not None:
                    recon += 1
            w["completeness"] = round(recon / total, 3)
            w["completeness_basis"] = "持久日志+事件时间设备版本"
    return sorted(windows.values(), key=lambda w: w["window_start"] or "", reverse=True)


def rule_diffs(db, status_filter: str | None = None) -> list[dict]:
    """旧新规则差异：逐待判项对照 缺失规则时的版本 与 补判使用的当前版本。"""
    q = db.query(PendingItem)
    if status_filter:
        q = q.filter(PendingItem.status == status_filter)
    out = []
    for it in q.order_by(PendingItem.id.desc()).limit(300).all():
        for m in it.missing_rules or []:
            code = m.get("rule_code")
            if not code:
                continue
            cur = rules_svc.current_rule_version(db, code)
            out.append(
                {
                    "pending_id": it.id,
                    "device_point": it.device_point,
                    "factor": it.factor,
                    "rule_code": code,
                    "old_version": m.get("version"),
                    "new_version": cur.version if cur else None,
                    "changed": bool(cur and m.get("version") is not None and cur.version != m.get("version")),
                    "current_revoked": bool(cur and cur.revoked),
                    "status": it.status,
                    "reading_ts": it.reading_ts.isoformat() if it.reading_ts else None,
                }
            )
    return out
