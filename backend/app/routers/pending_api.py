from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import PendingItem, RejudgeRun, User
from ..schemas import RejudgeIn, ResolvePendingIn
from ..security import require_user
from ..services import pending as pending_svc

router = APIRouter(prefix="/api", tags=["pending"])


@router.get("/pending/summary")
def pending_summary(db: Session = Depends(get_db), _: User = Depends(require_user)):
    windows = pending_svc.pending_windows(db)
    items = db.query(PendingItem).all()
    by_status = {}
    for it in items:
        by_status[it.status] = by_status.get(it.status, 0) + 1
    live_high = sum(
        1
        for it in items
        if it.status == "pending" and it.risk_level == "high"
    )
    return {
        "windows": len(windows),
        "items": len(items),
        "pending": by_status.get("pending", 0),
        "rejudged": by_status.get("rejudged", 0),
        "blocked": by_status.get("blocked", 0),
        "live_high_pending": live_high,
    }


@router.get("/pending/windows")
def pending_windows(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_user)):
    return pending_svc.pending_windows(db, status_filter=status)


@router.get("/pending/items")
def pending_items(
    status: str | None = None,
    strategy: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
):
    q = db.query(PendingItem)
    if status:
        q = q.filter(PendingItem.status == status)
    if strategy:
        q = q.filter(PendingItem.strategy == strategy)
    total = q.count()
    rows = q.order_by(PendingItem.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "items": [
            {
                "id": it.id,
                "receive_log_id": it.receive_log_id,
                "device_point": it.device_point,
                "factor": it.factor,
                "partition_key": it.partition_key,
                "risk_level": it.risk_level,
                "reading_ts": it.reading_ts.isoformat() if it.reading_ts else None,
                "received_at": it.received_at.isoformat() if it.received_at else None,
                "missing_rules": it.missing_rules or [],
                "strategy": it.strategy,
                "has_snapshot": it.input_snapshot is not None,
                "device_version_id": it.device_version_id,
                "legal_deadline": it.legal_deadline.isoformat() if it.legal_deadline else None,
                "status": it.status,
                "blocked_reason": it.blocked_reason,
                "rejudge_run_id": it.rejudge_run_id,
                "maintenance_window_id": it.maintenance_window_id,
            }
            for it in rows
        ],
    }


@router.get("/pending/rule-diffs")
def rule_diffs(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_user)):
    return pending_svc.rule_diffs(db, status_filter=status)


@router.post("/pending/rejudge")
def trigger_rejudge(body: RejudgeIn, db: Session = Depends(get_db), _: User = Depends(require_user)):
    res = pending_svc.run_rejudge(db, trigger="manual", strategy_filter=body.strategy)
    return res


@router.post("/pending/{pending_id}/resolve")
def resolve_blocked(pending_id: int, body: ResolvePendingIn, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    item = pending_svc.resolve_blocked(db, pending_id, body.rule_versions, user.username)
    if item is None:
        raise HTTPException(404, "待判项不存在或不是阻塞状态")
    return {"pending_id": item.id, "status": item.status}


@router.get("/rejudge/runs")
def list_runs(db: Session = Depends(get_db), _: User = Depends(require_user)):
    rows = db.query(RejudgeRun).order_by(RejudgeRun.id.desc()).limit(100).all()
    return [
        {
            "id": r.id,
            "trigger": r.trigger,
            "strategy": r.strategy,
            "rule_codes": r.rule_codes or [],
            "ordered_by": r.ordered_by,
            "total": r.total,
            "processed": r.processed,
            "alerted": r.alerted,
            "blocked": r.blocked,
            "order_preview": r.order_preview or [],
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
        }
        for r in rows
    ]
