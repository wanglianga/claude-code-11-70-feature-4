"""影响通知册：把待补正的值班记录和监管交付逐项关联。

回放发现只读样本已报送读数存在质量缺陷时创建待补正事项；
保留原报送范围、回执和当时依据，不覆盖原样本；控制台展示“未闭合补正”。
"""
from datetime import datetime

from ..models import CorrectionItem, DutyRecord, SampleReceipt, SampleReportBatch


def list_corrections(db, status_filter: str | None = None) -> list[dict]:
    q = db.query(CorrectionItem)
    if status_filter:
        q = q.filter(CorrectionItem.status == status_filter)
    rows = q.order_by(CorrectionItem.id.desc()).all()

    batches = {b.batch_code: b for b in db.query(SampleReportBatch).all()}
    receipts = {r.receipt_no: r for r in db.query(SampleReceipt).all()}
    duties = {d.id: d for d in db.query(DutyRecord).all()}

    out = []
    for c in rows:
        batch = batches.get(c.batch_code)
        receipt = receipts.get(c.receipt_no)
        duty = duties.get(c.duty_record_id)
        out.append(
            {
                "id": c.id,
                "batch_code": c.batch_code,
                "receipt_no": c.receipt_no,
                "device_point": c.device_point,
                "factor": c.factor,
                "reading_ts": c.reading_ts.isoformat() if c.reading_ts else None,
                "issue": c.issue,
                "status": c.status,
                "original_scope": c.original_scope,
                "original_basis": c.original_basis,
                "replay_run_id": c.replay_run_id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "closed_at": c.closed_at.isoformat() if c.closed_at else None,
                "closed_by": c.closed_by,
                "note": c.note,
                # 关联：监管交付（原报送批次+回执）与值班记录
                "regulatory_delivery": {
                    "regulator": batch.regulator if batch else None,
                    "period_start": batch.period_start.isoformat() if batch and batch.period_start else None,
                    "period_end": batch.period_end.isoformat() if batch and batch.period_end else None,
                    "submitted_at": batch.submitted_at.isoformat() if batch and batch.submitted_at else None,
                    "receipt_no": receipt.receipt_no if receipt else c.receipt_no,
                    "received_by": receipt.received_by if receipt else None,
                    "received_at": receipt.received_at.isoformat() if receipt and receipt.received_at else None,
                },
                "duty_record": {
                    "id": duty.id,
                    "shift_label": duty.shift_label,
                    "operator": duty.operator,
                    "started_at": duty.started_at.isoformat() if duty and duty.started_at else None,
                    "ended_at": duty.ended_at.isoformat() if duty and duty.ended_at else None,
                }
                if duty
                else None,
            }
        )
    return out


def summary(db) -> dict:
    total = db.query(CorrectionItem).count()
    open_ = db.query(CorrectionItem).filter(CorrectionItem.status == "open").count()
    ack = db.query(CorrectionItem).filter(CorrectionItem.status == "acknowledged").count()
    closed = db.query(CorrectionItem).filter(CorrectionItem.status == "closed").count()
    return {
        "total": total,
        "open": open_,
        "acknowledged": ack,
        "closed": closed,
        "unclosed": open_ + ack,
    }


def update_correction(db, correction_id: int, status: str, user: str, note: str = "") -> CorrectionItem | None:
    c = db.get(CorrectionItem, correction_id)
    if c is None:
        return None
    if status not in ("open", "acknowledged", "closed"):
        raise ValueError("状态须为 open/acknowledged/closed")
    c.status = status
    if note:
        c.note = note
    if status == "closed":
        c.closed_at = datetime.utcnow()
        c.closed_by = user
    db.commit()
    return c
