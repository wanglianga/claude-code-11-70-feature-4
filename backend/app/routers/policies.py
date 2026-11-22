from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Batch, Policy, QualityConclusion, User
from ..schemas import PolicyIn
from ..security import require_user

router = APIRouter(prefix="/api", tags=["policies"])


def policy_to_dict(p: Policy):
    return {
        "id": p.id,
        "version": p.version,
        "name": p.name,
        "priority": p.priority_json,
        "reserve_high_writes": p.reserve_high_writes,
        "active": p.active,
        "note": p.note,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/policies")
def list_policies(db: Session = Depends(get_db), _: User = Depends(require_user)):
    return [policy_to_dict(p) for p in db.query(Policy).order_by(Policy.version).all()]


@router.post("/policies")
def create_policy(body: PolicyIn, db: Session = Depends(get_db), _: User = Depends(require_user)):
    max_ver = db.query(func.max(Policy.version)).scalar() or 0
    if body.activate:
        db.query(Policy).update({Policy.active: False})
    p = Policy(
        version=max_ver + 1,
        name=body.name,
        priority_json=body.priority,
        reserve_high_writes=body.reserve_high_writes,
        active=body.activate,
        note=body.note,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return policy_to_dict(p)


@router.get("/policies/ledger")
def policy_ledger(db: Session = Depends(get_db), _: User = Depends(require_user)):
    """策略台账：每个版本被多少批次引用、产生了多少质量结论。"""
    rows = []
    for p in db.query(Policy).order_by(Policy.version).all():
        batch_count = db.query(Batch).filter(Batch.policy_version == p.version).count()
        concl_count = db.query(QualityConclusion).filter(QualityConclusion.policy_version == p.version).count()
        rows.append({**policy_to_dict(p), "batch_count": batch_count, "conclusion_count": concl_count})
    return rows


@router.get("/batches")
def list_batches(
    partition_key: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
):
    q = db.query(Batch)
    if partition_key:
        q = q.filter(Batch.partition_key == partition_key)
    total = q.count()
    rows = q.order_by(Batch.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "items": [
            {
                "id": b.id,
                "partition_key": b.partition_key,
                "policy_version": b.policy_version,
                "source": b.source,
                "recovery_run_id": b.recovery_run_id,
                "status": b.status,
                "item_count": b.item_count,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "processed_at": b.processed_at.isoformat() if b.processed_at else None,
            }
            for b in rows
        ],
    }
