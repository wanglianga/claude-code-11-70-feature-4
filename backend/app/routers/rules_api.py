from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import MaintenanceWindow, RuleCatalog, RuleVersion, User
from ..schemas import MaintenanceIn, PendingConfigIn, RevokeIn, RuleVersionIn
from ..security import require_user
from ..services import rules as rules_svc

router = APIRouter(prefix="/api/rules", tags=["rules"])


def _rule_to_dict(db: Session, r: RuleCatalog) -> dict:
    win = rules_svc.active_maintenance(db, r.rule_code)
    return {
        "id": r.id,
        "rule_code": r.rule_code,
        "name": r.name,
        "factor": r.factor,
        "check_dimension": r.check_dimension,
        "scope": r.scope,
        "dependencies": r.dependencies or [],
        "available": rules_svc.is_available(db, r),
        "catalog_available": r.available,
        "current_version": r.current_version,
        "maintenance": {
            "id": win.id,
            "strategy": win.strategy,
            "reason": win.reason,
            "started_at": win.started_at.isoformat() if win.started_at else None,
            "affected_partitions": win.affected_partitions or [],
        }
        if win
        else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "note": r.note,
    }


@router.get("")
def list_rules(db: Session = Depends(get_db), _: User = Depends(require_user)):
    rows = db.query(RuleCatalog).order_by(RuleCatalog.factor, RuleCatalog.rule_code).all()
    return [_rule_to_dict(db, r) for r in rows]


@router.get("/catalog")
def catalog_view(db: Session = Depends(get_db), _: User = Depends(require_user)):
    """规则目录：记录依赖、可用状态和发布版本。"""
    rows = list_rules(db, _)
    return {
        "rules": rows,
        "unavailable": [r for r in rows if not r["available"]],
        "strategy_recommendation": rules_svc.strategy_recommendation(db),
        "pending_config": _config_dict(rules_svc.get_pending_config(db)),
    }


def _config_dict(cfg) -> dict:
    return {
        "default_strategy": cfg.default_strategy,
        "legal_deadline_hours": cfg.legal_deadline_hours,
    }


@router.get("/{rule_code}/versions")
def list_versions(rule_code: str, db: Session = Depends(get_db), _: User = Depends(require_user)):
    rows = (
        db.query(RuleVersion)
        .filter(RuleVersion.rule_code == rule_code)
        .order_by(RuleVersion.version.desc())
        .all()
    )
    return [
        {
            "id": v.id,
            "rule_code": v.rule_code,
            "version": v.version,
            "status": v.status,
            "params": v.params,
            "revoked": v.revoked,
            "revoked_reason": v.revoked_reason,
            "published_at": v.published_at.isoformat() if v.published_at else None,
            "note": v.note,
        }
        for v in rows
    ]


@router.post("/{rule_code}/versions")
def publish_version(rule_code: str, body: RuleVersionIn, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    try:
        rv = rules_svc.publish_rule_version(db, rule_code, body.params, body.note or f"由 {user.username} 发布")
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    db.commit()
    return {"rule_code": rule_code, "version": rv.version, "status": rv.status}


@router.post("/{rule_code}/versions/{version}/revoke")
def revoke_version(rule_code: str, version: int, body: RevokeIn, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    rv = rules_svc.revoke_rule_version(db, rule_code, version, body.reason or f"由 {user.username} 撤销")
    if rv is None:
        raise HTTPException(404, "规则版本不存在")
    db.commit()
    return {"rule_code": rule_code, "version": version, "revoked": True, "reason": rv.revoked_reason}


# ---- 维护窗口 ----
@router.get("/maintenance")
def list_maintenance(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_user)):
    q = db.query(MaintenanceWindow)
    if status:
        q = q.filter(MaintenanceWindow.status == status)
    rows = q.order_by(MaintenanceWindow.id.desc()).all()
    return [
        {
            "id": w.id,
            "rule_code": w.rule_code,
            "factor": w.factor,
            "check_dimension": w.check_dimension,
            "strategy": w.strategy,
            "status": w.status,
            "reason": w.reason,
            "affected_partitions": w.affected_partitions or [],
            "started_at": w.started_at.isoformat() if w.started_at else None,
            "closed_at": w.closed_at.isoformat() if w.closed_at else None,
            "created_by": w.created_by,
            "pending_count": w.pending_count,
            "rejudged_count": w.rejudged_count,
            "span_hours": round(
                ((w.closed_at or datetime.utcnow()) - w.started_at).total_seconds() / 3600, 2
            )
            if w.started_at
            else None,
        }
        for w in rows
    ]


@router.post("/maintenance")
def open_maintenance(body: MaintenanceIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    try:
        win = rules_svc.open_maintenance(
            db, body.rule_code, body.strategy, body.reason, body.partitions, created_by=user.username
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"window_id": win.id, "rule_code": win.rule_code, "strategy": win.strategy, "status": win.status}


@router.post("/maintenance/{window_id}/close")
def close_maintenance(window_id: int, db: Session = Depends(get_db), _: User = Depends(require_user)):
    win = rules_svc.close_maintenance(db, window_id)
    if win is None:
        raise HTTPException(404, "维护窗口不存在")
    db.commit()
    return {
        "window_id": win.id,
        "status": win.status,
        "rule_code": win.rule_code,
        "pending_count": win.pending_count,
        "rejudged_count": win.rejudged_count,
    }


@router.get("/strategy-recommendation")
def strategy_recommendation(db: Session = Depends(get_db), _: User = Depends(require_user)):
    return rules_svc.strategy_recommendation(db)


@router.post("/pending-config")
def update_pending_config(body: PendingConfigIn, db: Session = Depends(get_db), _: User = Depends(require_user)):
    cfg = rules_svc.get_pending_config(db)
    if body.default_strategy:
        if body.default_strategy not in ("snapshot", "event_log"):
            raise HTTPException(400, "路线须为 snapshot 或 event_log")
        cfg.default_strategy = body.default_strategy
    if body.legal_deadline_hours is not None:
        cfg.legal_deadline_hours = body.legal_deadline_hours
    db.commit()
    return _config_dict(cfg)
