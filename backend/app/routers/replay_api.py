from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    CorrectionItem,
    ReplayRun,
    SampleReading,
    SampleReceipt,
    SampleReportBatch,
    SampleRuleVersion,
    User,
)
from ..schemas import CorrectionStatusIn, ReplayIn
from ..security import require_user
from ..services import corrections as corrections_svc
from ..services import replay as replay_svc

router = APIRouter(prefix="/api", tags=["replay"])


@router.get("/replay/samples")
def list_samples(db: Session = Depends(get_db), _: User = Depends(require_user)):
    """只读虚构报送样本：报送批次 + 回执 + 示例规则版本 + 原始读数。"""
    batches = db.query(SampleReportBatch).order_by(SampleReportBatch.id).all()
    receipts = {r.batch_code: r for r in db.query(SampleReceipt).all()}
    rules = db.query(SampleRuleVersion).order_by(SampleRuleVersion.factor).all()
    reading_counts = {}
    for r in db.query(SampleReading.batch_code).all():
        reading_counts[r.batch_code] = reading_counts.get(r.batch_code, 0) + 1
    return {
        "read_only": True,
        "note": "以下为只读虚构报送样本，仅供回放与影响追溯，不依赖任何真实报送能力，不可修改。",
        "batches": [
            {
                "batch_code": b.batch_code,
                "regulator": b.regulator,
                "scope": b.scope,
                "period_start": b.period_start.isoformat() if b.period_start else None,
                "period_end": b.period_end.isoformat() if b.period_end else None,
                "rule_version_labels": b.rule_version_labels or [],
                "submitted_at": b.submitted_at.isoformat() if b.submitted_at else None,
                "read_only": b.read_only,
                "note": b.note,
                "reading_count": reading_counts.get(b.batch_code, 0),
                "receipt": {
                    "receipt_no": receipts[b.batch_code].receipt_no,
                    "received_by": receipts[b.batch_code].received_by,
                    "received_at": receipts[b.batch_code].received_at.isoformat()
                    if receipts[b.batch_code].received_at
                    else None,
                    "status": receipts[b.batch_code].status,
                }
                if b.batch_code in receipts
                else None,
            }
            for b in batches
        ],
        "sample_rule_versions": [
            {
                "label": r.label,
                "factor": r.factor,
                "check_dimension": r.check_dimension,
                "version": r.version,
                "params": r.params,
                "published_at": r.published_at.isoformat() if r.published_at else None,
            }
            for r in rules
        ],
    }


@router.get("/replay/samples/{batch_code}/readings")
def sample_readings(batch_code: str, db: Session = Depends(get_db), _: User = Depends(require_user)):
    if db.query(SampleReportBatch).filter(SampleReportBatch.batch_code == batch_code).first() is None:
        raise HTTPException(404, "样本批次不存在")
    rows = db.query(SampleReading).filter(SampleReading.batch_code == batch_code).order_by(SampleReading.id).all()
    return [
        {
            "id": r.id,
            "batch_code": r.batch_code,
            "receipt_no": r.receipt_no,
            "device_point": r.device_point,
            "factor": r.factor,
            "reading_ts": r.reading_ts.isoformat() if r.reading_ts else None,
            "value": r.value,
            "reported_status": r.reported_status,
            "rule_label": r.rule_label,
            "device_status": r.device_status,
            "read_only": r.read_only,
        }
        for r in rows
    ]


@router.post("/replay/run")
def run_replay(body: ReplayIn, db: Session = Depends(get_db), _: User = Depends(require_user)):
    try:
        res = replay_svc.replay_batch(db, body.batch_code, body.candidate_rule, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return res


@router.get("/replay/runs")
def list_runs(db: Session = Depends(get_db), _: User = Depends(require_user)):
    rows = db.query(ReplayRun).order_by(ReplayRun.id.desc()).limit(50).all()
    return [
        {
            "id": r.id,
            "batch_code": r.batch_code,
            "baseline_label": r.baseline_label,
            "candidate_rule_version": r.candidate_rule_version,
            "total": r.total,
            "defects": r.defects,
            "alert_delta": r.alert_delta,
            "corrections_created": r.corrections_created,
            "affected_device_versions": r.affected_device_versions or [],
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "note": r.note,
        }
        for r in rows
    ]


@router.get("/replay/runs/{run_id}/results")
def run_results(run_id: int, db: Session = Depends(get_db), _: User = Depends(require_user)):
    if db.get(ReplayRun, run_id) is None:
        raise HTTPException(404, "回放运行不存在")
    return replay_svc.replay_results(db, run_id)


# ---- 影响通知册 ----
@router.get("/corrections")
def list_corrections(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_user)):
    return {
        "summary": corrections_svc.summary(db),
        "items": corrections_svc.list_corrections(db, status_filter=status),
    }


@router.post("/corrections/{correction_id}/status")
def update_status(correction_id: int, body: CorrectionStatusIn, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    try:
        c = corrections_svc.update_correction(db, correction_id, body.status, user.username, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if c is None:
        raise HTTPException(404, "补正事项不存在")
    return {"id": c.id, "status": c.status, "closed_at": c.closed_at.isoformat() if c.closed_at else None}
