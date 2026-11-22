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
    UniqueConstraint,
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
    # —— 延迟质量判定（规则维护期间进入待判队列，规则恢复后补判） ——
    rule_version = Column(Integer, nullable=True)  # 判定时引用的规则版本
    deferred = Column(Boolean, default=False)  # 是否由待判队列补判产生
    missing_rules = Column(JSON, nullable=True)  # 判定时缺失的规则集合快照
    rejudge_run_id = Column(BigInteger, nullable=True)  # 补判运行
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
    # —— 补判重演：延迟判定恢复后产生的告警标记来源，便于与实时告警区分 ——
    deferred = Column(Boolean, default=False)
    rejudge_run_id = Column(BigInteger, nullable=True)
    rule_version = Column(Integer, nullable=True)


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


# =====================================================================
# 延迟质量判定与告警重演
# =====================================================================

class DeviceVersion(Base):
    """设备配置版本：每次点位阈值/状态/风险级别变更追加一版，供待判窗口界定“当时适用设备版本”。"""

    __tablename__ = "device_versions"
    id = Column(BigInteger, primary_key=True)
    device_point = Column(String(64), index=True, nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String(16), default="normal")
    risk_level = Column(String(16), default="low")
    threshold_min = Column(Float, nullable=True)
    threshold_max = Column(Float, nullable=True)
    partition_key = Column(String(32))
    note = Column(String(256), default="")
    created_at = Column(DateTime, default=utcnow)


class RuleCatalog(Base):
    """规则目录：一条质量规则（按因子+检查维度定义）及其依赖、可用状态和当前发布版本。"""

    __tablename__ = "rule_catalog"
    id = Column(Integer, primary_key=True)
    rule_code = Column(String(64), unique=True, nullable=False, index=True)  # 例如 R-COD-RANGE
    name = Column(String(128), nullable=False)
    factor = Column(String(64), nullable=False, index=True)  # COD / NH3-N / level / valve_state / *
    check_dimension = Column(String(16), nullable=False)  # range / time_series / device_status
    scope = Column(String(16), default="factor")  # factor（按因子） | global（全局时段/风险级）
    dependencies = Column(JSON, default=list)  # 依赖的因子或规则编码列表
    available = Column(Boolean, default=True)  # 维护期间置 False：该规则暂不可执行
    current_version = Column(Integer, default=1)  # 当前发布版本号
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    note = Column(String(256), default="")


class RuleVersion(Base):
    """规则版本历史：每次发布或维护追加一版。版本不可静默覆盖，撤销需显式标记。"""

    __tablename__ = "rule_versions"
    id = Column(BigInteger, primary_key=True)
    rule_code = Column(String(64), index=True, nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String(16), default="published")  # published | revoked
    params = Column(JSON, default=dict)  # 版本参数（阈值/容差等）
    published_at = Column(DateTime, default=utcnow)
    revoked = Column(Boolean, default=False)
    revoked_reason = Column(String(256), default="")
    note = Column(String(256), default="")


class MaintenanceWindow(Base):
    """规则维护窗口：记录规则在维护期间不可执行的范围、起止与策略路线。

    strategy:
      snapshot —— 维护期封存每个待判窗口的输入快照和适用设备版本（写入/存储负担高，界定稳定）
      event_log —— 维护期只由持久接收日志按事件时间重建输入（额外副本少，恢复后扫描重关联）
    """

    __tablename__ = "maintenance_windows"
    id = Column(Integer, primary_key=True)
    rule_code = Column(String(64), nullable=False, index=True)
    factor = Column(String(64), nullable=False)
    check_dimension = Column(String(16), nullable=False)
    strategy = Column(String(16), default="snapshot")  # snapshot | event_log
    status = Column(String(16), default="active")  # active | closed
    reason = Column(String(256), default="")
    affected_partitions = Column(JSON, default=list)
    started_at = Column(DateTime, default=utcnow, index=True)
    closed_at = Column(DateTime, nullable=True)
    created_by = Column(String(64), default="")
    pending_count = Column(Integer, default=0)
    rejudged_count = Column(Integer, default=0)  # 补判时发现需告警/需复核的数量


class PendingItem(Base):
    """待判队列：规则不可用时暂存的读数、缺失规则集合、输入快照与适用设备版本。

    status:
      pending   —— 读数已保留为待判，尚未补判
      rejudged  —— 规则恢复后已完成补判
      blocked   —— 补判时规则版本在维护中途被撤销，需显式处理
    """

    __tablename__ = "pending_items"
    id = Column(BigInteger, primary_key=True)
    receive_log_id = Column(BigInteger, index=True, nullable=False)
    device_point = Column(String(64), index=True, nullable=False)
    partition_key = Column(String(32), index=True)
    risk_level = Column(String(16))
    factor = Column(String(64))
    reading_ts = Column(DateTime, index=True, nullable=False)
    received_at = Column(DateTime, default=utcnow)
    missing_rules = Column(JSON, default=list)  # 缺失规则集合（规则编码 + 当时不可用原因）
    input_snapshot = Column(JSON, nullable=True)  # 窗口输入快照（snapshot 路线；event_log 路线为 null）
    device_version_id = Column(BigInteger, nullable=True)  # 适用设备版本（snapshot 路线）
    device_status = Column(String(16))
    device_threshold_min = Column(Float, nullable=True)
    device_threshold_max = Column(Float, nullable=True)
    window_key = Column(String(96), index=True)  # 规则+因子+时段，用于按窗口聚合
    legal_deadline = Column(DateTime, index=True)  # 法定补判时限
    strategy = Column(String(16), default="snapshot")
    status = Column(String(16), default="pending", index=True)
    rejudge_run_id = Column(BigInteger, nullable=True)
    rejudge_conclusion_id = Column(BigInteger, nullable=True)
    blocked_reason = Column(String(256), default="")
    created_at = Column(DateTime, default=utcnow)
    rejudged_at = Column(DateTime, nullable=True)
    maintenance_window_id = Column(Integer, nullable=True, index=True)


class RejudgeRun(Base):
    """补判调度运行：按 实时高风险优先 → 法定时限 → 时间顺序 处理积压。"""

    __tablename__ = "rejudge_runs"
    id = Column(BigInteger, primary_key=True)
    trigger = Column(String(16), default="auto")  # auto | manual
    strategy = Column(String(16), default="all")
    rule_codes = Column(JSON, default=list)
    ordered_by = Column(String(64), default="risk_deadline_time")
    total = Column(Integer, default=0)
    processed = Column(Integer, default=0)
    alerted = Column(Integer, default=0)
    blocked = Column(Integer, default=0)
    degraded_blocked = Column(Integer, default=0)
    order_preview = Column(JSON, default=list)  # 前若干条处理顺序（用于核验排序）
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(16), default="done")


# =====================================================================
# 只读虚构报送样本（不依赖任何迭代实现的真实报送能力）
# =====================================================================

class SampleReportBatch(Base):
    """只读虚构报送批次：仅供回放与影响追溯，不允许修改。"""

    __tablename__ = "sample_report_batches"
    id = Column(Integer, primary_key=True)
    batch_code = Column(String(64), unique=True, nullable=False)
    regulator = Column(String(128), nullable=False)  # 监管机构（虚构）
    scope = Column(String(256), default="")  # 报送范围
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    rule_version_labels = Column(JSON, default=list)  # 该批次当时引用的规则版本标签
    submitted_at = Column(DateTime, nullable=False)
    read_only = Column(Boolean, default=True)
    note = Column(String(256), default="")


class SampleReceipt(Base):
    """只读报送回执：证明样本批次在何时被谁收到。"""

    __tablename__ = "sample_receipts"
    id = Column(Integer, primary_key=True)
    batch_code = Column(String(64), index=True, nullable=False)
    receipt_no = Column(String(64), unique=True, nullable=False)
    received_by = Column(String(128), nullable=False)
    received_at = Column(DateTime, nullable=False)
    status = Column(String(16), default="accepted")
    read_only = Column(Boolean, default=True)
    note = Column(String(256), default="")


class SampleRuleVersion(Base):
    """只读示例规则版本：回放基准，与实时规则目录分离，不会被维护撤销影响。"""

    __tablename__ = "sample_rule_versions"
    id = Column(Integer, primary_key=True)
    label = Column(String(64), unique=True, nullable=False)  # 例如 RV-COD-2024.1
    factor = Column(String(64), nullable=False)
    check_dimension = Column(String(16), nullable=False)
    version = Column(Integer, nullable=False)
    params = Column(JSON, default=dict)
    read_only = Column(Boolean, default=True)
    published_at = Column(DateTime, nullable=False)


class SampleReading(Base):
    """只读示例原始读数：回放输入，status 为当时报送时的判定（部分样本含质量缺陷）。"""

    __tablename__ = "sample_readings"
    id = Column(Integer, primary_key=True)
    batch_code = Column(String(64), index=True, nullable=False)
    receipt_no = Column(String(64), nullable=False)
    device_point = Column(String(64), nullable=False)
    factor = Column(String(64), nullable=False)
    reading_ts = Column(DateTime, nullable=False)
    value = Column(Float, nullable=False)
    reported_status = Column(String(16), default="normal")  # 当时报送判定：normal/exceed
    rule_label = Column(String(64))  # 当时引用的示例规则版本
    device_status = Column(String(16), default="normal")
    read_only = Column(Boolean, default=True)


class ReplayRun(Base):
    """回放沙箱运行：对只读样本计算质量结论和告警影响。"""

    __tablename__ = "replay_runs"
    id = Column(Integer, primary_key=True)
    batch_code = Column(String(64), index=True)
    baseline_label = Column(String(64), default="sample")  # 回放使用的示例规则集合
    candidate_rule_version = Column(Integer, nullable=True)  # 可选：当前规则目录版本对照
    total = Column(Integer, default=0)
    defects = Column(Integer, default=0)  # 发现质量缺陷的读数
    alert_delta = Column(Integer, default=0)  # 告警影响：新结论与报送判定差异数
    corrections_created = Column(Integer, default=0)
    affected_device_versions = Column(JSON, default=list)
    status = Column(String(16), default="done")
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    note = Column(String(256), default="")


class ReplayResult(Base):
    """回放结果：逐条读数的新旧质量结论差异与告警影响。"""

    __tablename__ = "replay_results"
    id = Column(BigInteger, primary_key=True)
    run_id = Column(Integer, index=True, nullable=False)
    sample_reading_id = Column(Integer, nullable=False)
    device_point = Column(String(64))
    factor = Column(String(64))
    reported_status = Column(String(16))
    replay_conclusion = Column(String(16))  # normal/exceed/defect
    range_ok = Column(Boolean)
    ts_ok = Column(Boolean)
    device_status_ok = Column(Boolean)
    diff_class = Column(String(24))  # same | newly_defect | changed_severity | recovered
    alert_impact = Column(String(24))  # none | would_alert | missed_alert
    note = Column(String(512), default="")


class DutyRecord(Base):
    """值班记录（样本回放发现缺陷时生成的待补正事项挂靠的值班记录）。"""

    __tablename__ = "duty_records"
    id = Column(Integer, primary_key=True)
    batch_code = Column(String(64), index=True)
    shift_label = Column(String(64), default="")
    operator = Column(String(64), default="")
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    note = Column(String(256), default="")
    read_only = Column(Boolean, default=False)


class CorrectionItem(Base):
    """影响通知册：把待补正的值班记录和监管交付逐项关联。

    回放发现只读样本中已报送读数质量缺陷时创建，保留原报送范围、回执与当时依据，不覆盖原样本。
    status: open | acknowledged | closed  —— 控制台展示“未闭合补正”。
    """

    __tablename__ = "corrections"
    id = Column(Integer, primary_key=True)
    batch_code = Column(String(64), index=True, nullable=False)
    receipt_no = Column(String(64), nullable=False)  # 保留原回执编号
    duty_record_id = Column(Integer, nullable=True)
    sample_reading_id = Column(Integer, nullable=True)
    replay_run_id = Column(Integer, nullable=True)
    device_point = Column(String(64))
    factor = Column(String(64))
    reading_ts = Column(DateTime, nullable=True)
    original_scope = Column(String(256), default="")  # 原报送范围（不被改写）
    original_basis = Column(JSON, default=dict)  # 当时依据（规则版本标签/判定）
    issue = Column(String(512), default="")
    status = Column(String(16), default="open", index=True)  # open | acknowledged | closed
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String(64), nullable=True)
    note = Column(String(256), default="")


class PendingConfig(Base):
    """待判配置（单行）：默认路线与法定补判时限。"""

    __tablename__ = "pending_config"
    id = Column(Integer, primary_key=True)  # 固定为 1
    default_strategy = Column(String(16), default="snapshot")  # snapshot | event_log
    legal_deadline_hours = Column(Integer, default=24)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
