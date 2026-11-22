from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Device, SimFlag, User
from ..schemas import ReadingIn, SimBackfillIn, SimRealtimeIn, SlowWriteIn
from ..security import require_user
from ..services import rules as rules_svc
from ..services import simulator
from ..services.common import set_slow_write
from ..services.ingest import ingest_readings

router = APIRouter(prefix="/api/simulate", tags=["simulate"])


@router.post("/realtime")
def sim_realtime(body: SimRealtimeIn, db: Session = Depends(get_db), _: User = Depends(require_user)):
    """场景一：实时流（含高风险超标因子）。"""
    return simulator.sim_realtime(db, body.count, body.exceed_ratio)


@router.post("/backfill")
def sim_backfill(body: SimBackfillIn, db: Session = Depends(get_db), _: User = Depends(require_user)):
    """场景二：断网恢复后的补传流（数小时前的读数集中上传）。"""
    return simulator.sim_backfill(db, body.hours, body.step_sec)


@router.post("/conflict")
def sim_conflict(db: Session = Depends(get_db), _: User = Depends(require_user)):
    """场景三：同会话同序列的重传与冲突载荷。"""
    return simulator.sim_conflict(db)


@router.post("/slow-writes")
def sim_slow_writes(body: SlowWriteIn, db: Session = Depends(get_db), _: User = Depends(require_user)):
    """场景四：数据库写入持续变慢（触发调度器降级与恢复）。"""
    flag = set_slow_write(db, body.enabled, body.delay_ms)
    return {"slow_write_enabled": flag.slow_write_enabled, "slow_write_delay_ms": flag.slow_write_delay_ms}


@router.get("/flags")
def sim_flags(db: Session = Depends(get_db), _: User = Depends(require_user)):
    flag = db.get(SimFlag, 1)
    return {
        "slow_write_enabled": bool(flag and flag.slow_write_enabled),
        "slow_write_delay_ms": flag.slow_write_delay_ms if flag else 0,
    }


class MaintenanceCycleIn(BaseModel):
    rule_code: str = "R-COD-RANGE"
    strategy: str = "snapshot"
    point_code: str = "WW-COD-01"
    value: float = 150.0
    reason: str = "演示：夜间规则维护"
    auto_close: bool = True


@router.post("/maintenance-cycle")
def sim_maintenance_cycle(body: MaintenanceCycleIn, db: Session = Depends(get_db), _: User = Depends(require_user)):
    """演示：开启规则维护 → 注入一条超标读数（进入待判）→ 可选关闭维护触发补判。

    验证：维护期间读数保留为待判、不告警；关闭后补判重新产生应得告警（标记延迟告警）。
    """
    import uuid
    from datetime import datetime

    try:
        win = rules_svc.open_maintenance(db, body.rule_code, body.strategy, body.reason, [], created_by="simulate")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()

    device = db.query(Device).filter(Device.point_code == body.point_code).first()
    if device is None:
        raise HTTPException(404, "点位不存在")
    session = f"sim-mt-{uuid.uuid4().hex[:8]}"
    ingest = ingest_readings(
        db,
        "GW-SIM-MT",
        session,
        [ReadingIn(point_code=body.point_code, source_seq=1, ts=datetime.utcnow(), value=body.value)],
        note="演示：规则维护期间读数",
    )
    result = {"window_id": win.id, "ingest": ingest, "closed": False}
    if body.auto_close:
        rules_svc.close_maintenance(db, win.id)
        db.commit()
        result["closed"] = True
    return result
