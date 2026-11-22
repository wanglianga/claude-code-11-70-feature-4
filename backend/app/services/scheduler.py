from datetime import datetime, timedelta

from .. import config
from ..models import Batch, BatchItem, Partition, Policy, ReceiveLog
from .common import batch_lock, get_state


def active_policy(db) -> Policy | None:
    return db.query(Policy).filter(Policy.active.is_(True)).first()


def unbatched_query(db):
    """已确认但尚未放入任何批次的接收日志。"""
    subq = db.query(BatchItem.receive_log_id)
    return db.query(ReceiveLog).filter(ReceiveLog.status == "confirmed", ~ReceiveLog.id.in_(subq))


def make_batches(db, entries, policy: Policy, source: str, recovery_run_id=None) -> int:
    """把接收日志按分区分组入批；批次在创建时固化当前策略版本。调用方须持有 batch_lock。"""
    by_part: dict[str, list] = {}
    for e in entries:
        by_part.setdefault(e.partition_key, []).append(e)
    made = 0
    for pkey, items in by_part.items():
        for i in range(0, len(items), config.BATCH_MAX_ITEMS):
            chunk = items[i : i + config.BATCH_MAX_ITEMS]
            batch = Batch(
                partition_key=pkey,
                policy_version=policy.version,  # 版本在创建时固化，之后策略升级不回溯改写
                source=source,
                recovery_run_id=recovery_run_id,
                item_count=len(chunk),
            )
            db.add(batch)
            db.flush()
            for e in chunk:
                db.add(BatchItem(batch_id=batch.id, receive_log_id=e.id))
            made += 1
        part = db.get(Partition, pkey)
        max_id = max(e.id for e in items)
        if part is not None and max_id > (part.batched_cursor or 0):
            part.batched_cursor = max_id
    return made


def scheduler_tick(db) -> int:
    """实时通道：只接近期读数；降级模式下只为高风险分区分配写入额度。"""
    state = get_state(db)
    policy = active_policy(db)
    if policy is None:
        return 0
    cutoff = datetime.utcnow() - timedelta(seconds=config.BACKFILL_AGE_SEC)
    with batch_lock:
        q = unbatched_query(db).filter(ReceiveLog.reading_ts >= cutoff)
        if state.degraded:
            high_parts = [p.partition_key for p in db.query(Partition).filter_by(risk_class="high")]
            q = q.filter(ReceiveLog.partition_key.in_(high_parts))
        entries = q.order_by(ReceiveLog.id).limit(config.SCHED_TICK_LIMIT).all()
        if not entries:
            return 0
        # 按策略权重排序：高优先级分区先入批
        weights = policy.priority_json or {}
        parts = {p.partition_key: p for p in db.query(Partition).all()}
        entries.sort(
            key=lambda e: (
                -int(weights.get(parts[e.partition_key].risk_class, 0)) if e.partition_key in parts else 0,
                e.id,
            )
        )
        made = make_batches(db, entries, policy, source="normal")
        db.commit()
        return made
