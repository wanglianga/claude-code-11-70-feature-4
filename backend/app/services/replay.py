"""回放沙箱：对只读样本批次计算质量结论与告警影响。

只读样本（报送批次 / 回执 / 示例规则版本 / 原始读数）与实时数据完全分离，
不依赖任何迭代实现的真实报送能力，仅供回放与影响追溯。

关键约束：
- 回放不修改只读样本；发现已报送读数质量缺陷时，通知册创建待补正事项，
  但保留原报送范围、回执与当时依据。
- 规则版本在维护中途撤销时，沙箱标出受影响输入并禁止以替代版本静默覆盖。
"""
from datetime import datetime

from ..models import (
    CorrectionItem,
    DutyRecord,
    ReplayResult,
    ReplayRun,
    SampleReading,
    SampleReceipt,
    SampleReportBatch,
    SampleRuleVersion,
)
from . import judge as judge_mod, rules as rules_svc


def _sample_rule_params(db, batch: SampleReportBatch) -> dict[str, dict]:
    """该样本批次当时引用的只读示例规则版本参数（按因子索引）。"""
    labels = batch.rule_version_labels or []
    out: dict[str, dict] = {}
    if not labels:
        return out
    rows = db.query(SampleRuleVersion).filter(SampleRuleVersion.label.in_(labels)).all()
    for rv in rows:
        out[rv.factor] = {
            "label": rv.label,
            "version": rv.version,
            "check_dimension": rv.check_dimension,
            "params": rv.params or {},
        }
    return out


def _replay_one(reading: SampleReading, rules_by_factor: dict[str, dict]) -> dict:
    rv = rules_by_factor.get(reading.factor, {})
    params = rv.get("params") or {}
    tmin = params.get("threshold_min")
    tmax = params.get("threshold_max")
    res = judge_mod.judge(
        value=reading.value,
        reading_ts=reading.reading_ts,
        received_at=reading.reading_ts,  # 只读样本无接收时间，视同按时到达
        device_status=reading.device_status,
        threshold_min=tmin,
        threshold_max=tmax,
    )
    reported = reading.reported_status or "normal"
    # 映射到严重度
    sev = {"normal": 0, "exceed": 2, "pending_review": 1}.get(res["conclusion"], 0)
    rep_sev = {"normal": 0, "exceed": 2}.get(reported, 0)

    defect = False
    if reported == "normal" and res["conclusion"] in ("exceed", "pending_review"):
        diff_class = "newly_defect"
        defect = True
    elif reported == "exceed" and res["conclusion"] == "normal":
        diff_class = "recovered"
    elif sev != rep_sev:
        diff_class = "changed_severity"
    else:
        diff_class = "same"

    if reported != "exceed" and res["conclusion"] == "exceed":
        alert_impact = "would_alert"
    elif reported == "exceed" and res["conclusion"] == "normal":
        alert_impact = "false_alert"
    else:
        alert_impact = "none"

    note = res["detail"]
    if reading.device_status != "normal":
        note += f"；样本设备状态为 {reading.device_status}"
    return {
        "replay_conclusion": res["conclusion"],
        "range_ok": res["range_ok"],
        "ts_ok": res["ts_ok"],
        "device_status_ok": res["device_status_ok"],
        "diff_class": diff_class,
        "alert_impact": alert_impact,
        "defect": defect,
        "note": note[:512],
    }


def _affected_by_revoked(db, batch: SampleReportBatch, rules_by_factor: dict[str, dict]) -> list[dict]:
    """检测：候选（实时目录）规则版本在维护中途被撤销时，标出受影响的只读输入。

    对样本批次引用的因子，若实时目录中对应规则当前版本被撤销，则该批次的读数受影响。
    沙箱禁止以替代版本静默覆盖：受影响输入标记 needs_explicit_review，且不自动改写样本。
    """
    affected = []
    factors = {r.factor for r in db.query(SampleReading).filter(SampleReading.batch_code == batch.batch_code).all()}
    for factor in factors:
        for rule in rules_svc.applicable_rules(db, factor):
            rv = rules_svc.current_rule_version(db, rule.rule_code)
            if rv is not None and rv.revoked:
                affected.append(
                    {
                        "rule_code": rule.rule_code,
                        "factor": factor,
                        "revoked_version": rv.version,
                        "reason": rv.revoked_reason or "维护中途撤销",
                        "needs_explicit_review": True,
                    }
                )
    return affected


def replay_batch(db, batch_code: str, candidate_rule: str | None = None, note: str = "") -> dict:
    batch = db.query(SampleReportBatch).filter(SampleReportBatch.batch_code == batch_code).first()
    if batch is None:
        raise ValueError("样本批次不存在")
    rules_by_factor = _sample_rule_params(db, batch)
    readings = (
        db.query(SampleReading)
        .filter(SampleReading.batch_code == batch_code)
        .order_by(SampleReading.id)
        .all()
    )
    affected = _affected_by_revoked(db, batch, rules_by_factor)
    blocked_override = False
    candidate_version = None
    if candidate_rule:
        rv = rules_svc.current_rule_version(db, candidate_rule)
        if rv is None:
            raise ValueError("候选规则不存在")
        candidate_version = rv.version
        if rv.revoked:
            blocked_override = True
            affected.append(
                {
                    "rule_code": candidate_rule,
                    "factor": rv.params.get("factor", "*") if rv.params else "*",
                    "revoked_version": rv.version,
                    "reason": rv.revoked_reason or "候选规则版本已撤销",
                    "needs_explicit_review": True,
                }
            )

    run = ReplayRun(
        batch_code=batch_code,
        baseline_label="sample:" + ",".join(batch.rule_version_labels or []),
        candidate_rule_version=candidate_version,
        total=len(readings),
        status="running",
        note=note,
    )
    db.add(run)
    db.flush()

    defects = []
    alert_delta = 0
    affected_devices = sorted({r.device_point for r in readings})
    for reading in readings:
        out = _replay_one(reading, rules_by_factor)
        if out["alert_impact"] != "none":
            alert_delta += 1
        db.add(
            ReplayResult(
                run_id=run.id,
                sample_reading_id=reading.id,
                device_point=reading.device_point,
                factor=reading.factor,
                reported_status=reading.reported_status,
                replay_conclusion=out["replay_conclusion"],
                range_ok=out["range_ok"],
                ts_ok=out["ts_ok"],
                device_status_ok=out["device_status_ok"],
                diff_class=out["diff_class"],
                alert_impact=out["alert_impact"],
                note=out["note"],
            )
        )
        if out["defect"]:
            defects.append((reading, out, rules_by_factor.get(reading.factor, {})))

    run.defects = len(defects)
    run.alert_delta = alert_delta
    run.affected_device_versions = affected_devices
    run.finished_at = datetime.utcnow()
    run.status = "done"

    # 只读样本中已报送读数存在质量缺陷 → 创建待补正事项（保留原范围/回执/当时依据）
    created = 0
    if not blocked_override:
        receipt = (
            db.query(SampleReceipt).filter(SampleReceipt.batch_code == batch_code).first()
        )
        duty = db.query(DutyRecord).filter(DutyRecord.batch_code == batch_code).first()
        for reading, out, rv in defects:
            exists = (
                db.query(CorrectionItem)
                .filter(
                    CorrectionItem.batch_code == batch_code,
                    CorrectionItem.sample_reading_id == reading.id,
                )
                .first()
            )
            if exists is not None:
                continue
            db.add(
                CorrectionItem(
                    batch_code=batch_code,
                    receipt_no=reading.receipt_no,
                    duty_record_id=duty.id if duty else None,
                    sample_reading_id=reading.id,
                    replay_run_id=run.id,
                    device_point=reading.device_point,
                    factor=reading.factor,
                    reading_ts=reading.reading_ts,
                    original_scope=batch.scope,
                    original_basis={
                        "reported_status": reading.reported_status,
                        "rule_label": reading.rule_label,
                        "device_status": reading.device_status,
                        "receipt_no": reading.receipt_no,
                        "rule_versions": batch.rule_version_labels or [],
                    },
                    issue=out["note"],
                    status="open",
                )
            )
            created += 1
    run.corrections_created = created
    db.commit()
    return {
        "run_id": run.id,
        "batch_code": batch_code,
        "total": run.total,
        "defects": run.defects,
        "alert_delta": run.alert_delta,
        "corrections_created": run.corrections_created,
        "affected_inputs": affected,
        "blocked_override": blocked_override,
        "candidate_rule_version": candidate_version,
    }


def replay_results(db, run_id: int) -> list[dict]:
    rows = db.query(ReplayResult).filter(ReplayResult.run_id == run_id).order_by(ReplayResult.id).all()
    return [
        {
            "id": r.id,
            "sample_reading_id": r.sample_reading_id,
            "device_point": r.device_point,
            "factor": r.factor,
            "reported_status": r.reported_status,
            "replay_conclusion": r.replay_conclusion,
            "range_ok": r.range_ok,
            "ts_ok": r.ts_ok,
            "device_status_ok": r.device_status_ok,
            "diff_class": r.diff_class,
            "alert_impact": r.alert_impact,
            "note": r.note,
        }
        for r in rows
    ]
