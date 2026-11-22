from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
)


def utcnow():
    return datetime.utcnow()


from .db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(32), default="operator")  # admin | operator
    created_at = Column(DateTime, default=utcnow)


class Device(Base):
    """设备目录：点位、因子、采样周期、风险级别、阈值、所属分区、运行状态。"""

    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    point_code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    category = Column(String(32), nullable=False)  # wastewater/waste_gas/tank_level/rain_valve
    factor = Column(String(64), nullable=False)
    unit = Column(String(16), default="")
    sampling_period_sec = Column(Integer, default=60)
    risk_level = Column(String(16), nullable=False, default="low")  # high/medium/low
    threshold_min = Column(Float, nullable=True)
    threshold_max = Column(Float, nullable=True)
    partition_key = Column(String(32), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="normal")  # normal/calibrating/maintenance
    created_at = Column(DateTime, default=utcnow)


class Partition(Base):
    """分区积压水位与游标：按分区跟踪 已确认/已入批/已处理 的位置。"""

    __tablename__ = "partitions"
    partition_key = Column(String(32), primary_key=True)
    risk_class = Column(String(16), nullable=False)  # high/medium/low
    description = Column(String(256), default="")
    confirmed_cursor = Column(BigInteger, default=0)  # 该分区已确认的最大接收日志 id
    batched_cursor = Column(BigInteger, default=0)  # 已放入批次的最大接收日志 id
    processed_cursor = Column(BigInteger, default=0)  # 已完成质量处理的最大接收日志 id
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class GatewaySession(Base):
    """接入网关会话：每次联网/重连建立一个会话，来源序列在会话内单调递增。"""

    __tablename__ = "gateway_sessions"
    id = Column(Integer, primary_key=True)
    gateway_code = Column(String(64), nullable=False, index=True)
    session_uuid = Column(String(64), unique=True, nullable=False)
    started_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, default=utcnow)
    status = Column(String(16), default="active")  # active/closed
    note = Column(String(256), default="")


class ReceiveLog(Base):
    """中心持久接收日志：每一次到达都追加一行，id 即接收顺序。

    status:
      confirmed —— 会话内该来源序列首次到达，被确认（只确认一次）
      duplicate —— 同会话+同序列+同指纹的重传
      conflict  —— 同会话+同序列但载荷不同的冲突证据（不覆盖已确认读数）
    日志在监管取证期内不做抽样删除。
    """

    __tablename__ = "receive_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gateway_code = Column(String(64), nullable=False, index=True)
    session_uuid = Column(String(64), nullable=False, index=True)
    source_seq = Column(BigInteger, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    device_point = Column(String(64), nullable=False, index=True)
    reading_ts = Column(DateTime, nullable=False, index=True)
    value = Column(Float, nullable=False)
    raw_payload = Column(JSON)
    device_status_at_receipt = Column(String(16), default="normal")
    partition_key = Column(String(32), index=True)
    risk_level = Column(String(16))
    status = Column(String(16), nullable=False, default="confirmed", index=True)
    note = Column(String(256), default="")
    received_at = Column(DateTime, default=utcnow, index=True)


Index("ix_receive_log_session_seq", ReceiveLog.session_uuid, ReceiveLog.source_seq)


class ReadingConflict(Base):
    """冲突证据：同序列不同载荷的后到值与已确认读数并列保存。"""

    __tablename__ = "reading_conflicts"
    id = Column(Integer, primary_key=True)
    session_uuid = Column(String(64), nullable=False)
    source_seq = Column(BigInteger, nullable=False)
    confirmed_log_id = Column(BigInteger, nullable=False)  # 保留的已确认读数
    conflict_log_id = Column(BigInteger, nullable=False, unique=True)  # 后到的冲突到达
    created_at = Column(DateTime, default=utcnow)


class Policy(Base):
    """调度策略版本：分区优先级权重与降级时高风险写入额度预留。版本不可变，台账可查。"""

    __tablename__ = "policies"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    priority_json = Column(JSON)  # {"high":100,"medium":50,"low":10}
    reserve_high_writes = Column(Integer, default=50)  # 降级时为高风险分区保留的写入额度 %
    active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    note = Column(String(256), default="")


class Batch(Base):
    """处理批次：创建时固化策略版本；策略升级只影响之后新建的批次。"""

    __tablename__ = "batches"
    id = Column(BigInteger, primary_key=True)
    partition_key = Column(String(32), index=True)
    policy_version = Column(Integer, nullable=False)
    source = Column(String(16), default="normal")  # normal | recovery
    recovery_run_id = Column(Integer, nullable=True)
    status = Column(String(16), default="open", index=True)  # open/processing/done
    item_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    processed_at = Column(DateTime, nullable=True)


class BatchItem(Base):
    __tablename__ = "batch_items"
    id = Column(BigInteger, primary_key=True)
    batch_id = Column(BigInteger, index=True)
    receive_log_id = Column(BigInteger, unique=True)  # 一条日志至多入批一次
    created_at = Column(DateTime, default=utcnow)


class QualityConclusion(Base):
    """质量结论：范围 / 时序 / 设备状态三项检查 + 总结论。

    conclusion: normal | exceed | pending_review（设备校准/维护期间的待核结论）
    confirmed=False 的结论不会被告警服务当作正式超标。
    """

    __tablename__ = "quality_conclusions"
    id = Column(BigInteger, primary_key=True)
    receive_log_id = Column(BigInteger, unique=True, index=True)
    batch_id = Column(BigInteger)
    device_point = Column(String(64), index=True)
    partition_key = Column(String(32), index=True)
    policy_version = Column(Integer)
    range_ok = Column(Boolean)
    ts_ok = Column(Boolean)
    device_status_ok = Column(Boolean)
    gap_detected = Column(Boolean, default=False)
    conclusion = Column(String(16))
    confirmed = Column(Boolean, default=True)
    detail = Column(String(512), default="")
    created_at = Column(DateTime, default=utcnow)


class Alert(Base):
    """告警：只消费 已确认 + 高风险 + 超标 的质量结论。latency_ms 为 接收→告警 的延迟。"""

    __tablename__ = "alerts"
    id = Column(BigInteger, primary_key=True)
    conclusion_id = Column(BigInteger, unique=True)
    receive_log_id = Column(BigInteger, index=True)
    device_point = Column(String(64), index=True)
    level = Column(String(16), default="high")
    message = Column(String(512))
    status = Column(String(16), default="open")  # open/acked
    received_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)
    latency_ms = Column(BigInteger)
    acked_at = Column(DateTime, nullable=True)
    acked_by = Column(String(64), nullable=True)


class RecoveryRun(Base):
    """恢复编排器的回补运行记录：按分区游标回补积压。"""

    __tablename__ = "recovery_runs"
    id = Column(Integer, primary_key=True)
    partition_key = Column(String(32), index=True)
    trigger = Column(String(16), default="auto")  # auto | manual
    from_cursor = Column(BigInteger)
    to_cursor = Column(BigInteger)
    recovered_count = Column(Integer, default=0)
    status = Column(String(16), default="done")  # done/empty
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)


class WriteMetric(Base):
    """数据库写入延迟探针记录。"""

    __tablename__ = "write_metrics"
    id = Column(BigInteger, primary_key=True)
    component = Column(String(32), default="probe")
    latency_ms = Column(Float)
    created_at = Column(DateTime, default=utcnow, index=True)


class SchedulerState(Base):
    """调度器运行状态（单行）：降级模式与低风险衍生计算暂停标志。"""

    __tablename__ = "scheduler_state"
    id = Column(Integer, primary_key=True)  # 固定为 1
    degraded = Column(Boolean, default=False)
    low_risk_paused = Column(Boolean, default=False)
    threshold_ms = Column(Integer, default=800)
    last_latency_ms = Column(Float, default=0)
    trends_paused_count = Column(BigInteger, default=0)  # 降级期间被暂停的衍生计算次数
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class SimFlag(Base):
    """演示开关（单行）：模拟数据库写入持续变慢。"""

    __tablename__ = "sim_flags"
    id = Column(Integer, primary_key=True)  # 固定为 1
    slow_write_enabled = Column(Boolean, default=False)
    slow_write_delay_ms = Column(Integer, default=0)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class DerivedTrend(Base):
    """低风险衍生计算结果（滚动均值）。降级模式下暂停写入，恢复后由恢复路径补齐。"""

    __tablename__ = "derived_trends"
    id = Column(BigInteger, primary_key=True)
    device_point = Column(String(64), index=True)
    window_start = Column(DateTime)
    avg_value = Column(Float)
    sample_count = Column(Integer)
    created_at = Column(DateTime, default=utcnow)
