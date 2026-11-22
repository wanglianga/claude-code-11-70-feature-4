from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import config
from .models import (
    Device,
    DeviceVersion,
    DutyRecord,
    Partition,
    PendingConfig,
    Policy,
    RuleCatalog,
    RuleVersion,
    SampleReading,
    SampleReceipt,
    SampleReportBatch,
    SampleRuleVersion,
    SchedulerState,
    SimFlag,
    User,
)
from .security import hash_password

DEVICES = [
    # point_code, name, category, factor, unit, period, risk, tmin, tmax, partition
    ("WW-COD-01", "废水总排口 COD", "wastewater", "COD", "mg/L", 60, "high", None, 100, "P-HIGH-1"),
    ("WW-NH3-01", "废水总排口 氨氮", "wastewater", "NH3-N", "mg/L", 60, "high", None, 15, "P-HIGH-1"),
    ("WW-PH-01", "废水总排口 pH", "wastewater", "pH", "", 60, "medium", 6, 9, "P-MED-1"),
    ("WG-SO2-01", "废气排口 SO2", "waste_gas", "SO2", "mg/m3", 60, "high", None, 50, "P-HIGH-2"),
    ("WG-NOX-01", "废气排口 NOx", "waste_gas", "NOx", "mg/m3", 60, "high", None, 100, "P-HIGH-2"),
    ("WG-VOC-01", "废气排口 VOCs", "waste_gas", "VOCs", "mg/m3", 120, "medium", None, 60, "P-MED-1"),
    ("TK-LV-01", "1# 储罐液位", "tank_level", "level", "m", 30, "medium", 0.2, 12, "P-MED-1"),
    ("TK-LV-02", "2# 储罐液位", "tank_level", "level", "m", 30, "medium", 0.2, 12, "P-MED-1"),
    ("TK-LV-03", "3# 储罐液位", "tank_level", "level", "m", 30, "low", 0.2, 12, "P-LOW-1"),
    ("RV-01", "雨污切换阀 1#", "rain_valve", "valve_state", "", 10, "high", 0, 1, "P-HIGH-1"),
    ("RV-02", "雨污切换阀 2#", "rain_valve", "valve_state", "", 10, "low", 0, 1, "P-LOW-1"),
    ("WW-FLOW-01", "废水排口流量", "wastewater", "flow", "m3/h", 60, "low", 0, 500, "P-LOW-1"),
]

PARTITIONS = [
    ("P-HIGH-1", "high", "高风险分区 1（废水超标因子 + 雨污阀）"),
    ("P-HIGH-2", "high", "高风险分区 2（废气超标因子）"),
    ("P-MED-1", "medium", "中风险分区（pH/VOCs/储罐液位）"),
    ("P-LOW-1", "low", "低风险分区（趋势类因子）"),
]

# 规则目录：按 因子 + 检查维度。scope=global 的规则适用于所有因子（时段/风险级类）。
RULES = [
    # rule_code, name, factor, dimension, scope, dependencies
    ("R-COD-RANGE", "COD 阈值范围检查", "COD", "range", "factor", ["device_threshold"]),
    ("R-NH3-RANGE", "氨氮阈值范围检查", "NH3-N", "range", "factor", ["device_threshold"]),
    ("R-SO2-RANGE", "SO2 阈值范围检查", "SO2", "range", "factor", ["device_threshold"]),
    ("R-NOX-RANGE", "NOx 阈值范围检查", "NOx", "range", "factor", ["device_threshold"]),
    ("R-LEVEL-RANGE", "储罐液位范围检查", "level", "range", "factor", ["device_threshold"]),
    ("R-VALVE-RANGE", "雨污阀状态范围检查", "valve_state", "range", "factor", ["device_threshold"]),
    ("R-TS-GLOBAL", "读数时序合理性检查", "*", "time_series", "global", ["clock_sync"]),
    ("R-DEVSTATUS-GLOBAL", "设备状态可用性检查", "*", "device_status", "global", ["device_registry"]),
]


def _seed_rules(db: Session):
    if db.query(RuleCatalog).count():
        return
    for code, name, factor, dim, scope, deps in RULES:
        db.add(
            RuleCatalog(
                rule_code=code,
                name=name,
                factor=factor,
                check_dimension=dim,
                scope=scope,
                dependencies=deps,
                available=True,
                current_version=1,
            )
        )
        db.flush()
        db.add(
            RuleVersion(
                rule_code=code,
                version=1,
                status="published",
                params={"check_dimension": dim, "dependencies": deps},
                note="初始发布",
            )
        )
    db.commit()


def _seed_device_versions(db: Session):
    if db.query(DeviceVersion).count():
        return
    for d in db.query(Device).all():
        db.add(
            DeviceVersion(
                device_point=d.point_code,
                version=1,
                status=d.status,
                risk_level=d.risk_level,
                threshold_min=d.threshold_min,
                threshold_max=d.threshold_max,
                partition_key=d.partition_key,
                note="种子基线",
            )
        )
    db.commit()


def _seed_samples(db: Session):
    """只读虚构报送样本：一个已报送批次（含质量缺陷），供回放与影响追溯。

    这些数据与实时通道无关，不依赖任何迭代实现的报送能力；不可修改。
    """
    if db.query(SampleReportBatch).count():
        return
    now = datetime.utcnow()
    period_start = now - timedelta(days=2)
    period_end = period_start + timedelta(hours=6)
    code = "SAMPLE-2026-11-A"
    labels = ["RV-COD-2026.1", "RV-NH3-2026.1", "RV-SO2-2026.1"]
    db.add(
        SampleReportBatch(
            batch_code=code,
            regulator="虚构·市生态环境监测中心",
            scope="废水总排口 + 废气排口 6 小时均值报送",
            period_start=period_start,
            period_end=period_end,
            rule_version_labels=labels,
            submitted_at=period_start + timedelta(hours=7),
            note="只读虚构样本；其中含 2 条报送时判定为正常的读数，回放将发现质量缺陷",
        )
    )
    db.add(
        SampleReceipt(
            batch_code=code,
            receipt_no="RCPT-SAMPLE-A-0001",
            received_by="虚构·监测中心受理台",
            received_at=period_start + timedelta(hours=7, minutes=12),
            status="accepted",
        )
    )
    sample_rules = [
        ("RV-COD-2026.1", "COD", "range", 1, {"threshold_min": None, "threshold_max": 100}),
        ("RV-NH3-2026.1", "NH3-N", "range", 1, {"threshold_min": None, "threshold_max": 15}),
        ("RV-SO2-2026.1", "SO2", "range", 1, {"threshold_min": None, "threshold_max": 50}),
    ]
    for label, factor, dim, ver, params in sample_rules:
        db.add(
            SampleRuleVersion(
                label=label,
                factor=factor,
                check_dimension=dim,
                version=ver,
                params=params,
                published_at=period_start - timedelta(days=30),
            )
        )
    readings = [
        # device, factor, offset_h, value, reported_status, rule_label, device_status
        ("WW-COD-01", "COD", 0.0, 82.0, "normal", "RV-COD-2026.1", "normal"),
        ("WW-COD-01", "COD", 1.0, 88.5, "normal", "RV-COD-2026.1", "normal"),
        ("WW-COD-01", "COD", 2.0, 131.4, "normal", "RV-COD-2026.1", "normal"),  # 缺陷：报送正常但超阈值
        ("WW-COD-01", "COD", 3.0, 95.0, "normal", "RV-COD-2026.1", "normal"),
        ("WW-NH3-01", "NH3-N", 0.0, 9.2, "normal", "RV-NH3-2026.1", "normal"),
        ("WW-NH3-01", "NH3-N", 1.5, 12.0, "normal", "RV-NH3-2026.1", "normal"),
        ("WW-NH3-01", "NH3-N", 3.0, 18.7, "normal", "RV-NH3-2026.1", "normal"),  # 缺陷：超阈值
        ("WG-SO2-01", "SO2", 0.5, 31.0, "normal", "RV-SO2-2026.1", "normal"),
        ("WG-SO2-01", "SO2", 2.5, 44.0, "exceed", "RV-SO2-2026.1", "normal"),
        ("WG-SO2-01", "SO2", 4.0, 28.0, "normal", "RV-SO2-2026.1", "maintenance"),  # 设备维护：回放标待核
    ]
    for point, factor, off, value, rep, label, dstat in readings:
        db.add(
            SampleReading(
                batch_code=code,
                receipt_no="RCPT-SAMPLE-A-0001",
                device_point=point,
                factor=factor,
                reading_ts=period_start + timedelta(hours=off),
                value=value,
                reported_status=rep,
                rule_label=label,
                device_status=dstat,
            )
        )
    db.add(
        DutyRecord(
            batch_code=code,
            shift_label="2026-11-19 夜班",
            operator="虚构·值班员 A",
            started_at=period_start,
            ended_at=period_end,
            note="样本报送时值班记录",
        )
    )
    db.commit()


def seed(db: Session):
    if not db.query(User).count():
        db.add_all(
            [
                User(username="admin", password_hash=hash_password("admin123"), role="admin"),
                User(username="operator", password_hash=hash_password("operator123"), role="operator"),
            ]
        )
    if not db.query(Partition).count():
        for key, cls, desc in PARTITIONS:
            db.add(Partition(partition_key=key, risk_class=cls, description=desc))
    if not db.query(Device).count():
        for code, name, cat, factor, unit, period, risk, tmin, tmax, part in DEVICES:
            db.add(
                Device(
                    point_code=code,
                    name=name,
                    category=cat,
                    factor=factor,
                    unit=unit,
                    sampling_period_sec=period,
                    risk_level=risk,
                    threshold_min=tmin,
                    threshold_max=tmax,
                    partition_key=part,
                )
            )
    if not db.query(Policy).count():
        db.add(
            Policy(
                version=1,
                name="初始策略 v1",
                priority_json={"high": 100, "medium": 50, "low": 10},
                reserve_high_writes=50,
                active=True,
                note="系统初始调度策略",
            )
        )
    if db.query(SchedulerState).count() == 0:
        db.add(SchedulerState(id=1, threshold_ms=config.WRITE_LATENCY_THRESHOLD_MS))
    if db.query(SimFlag).count() == 0:
        db.add(SimFlag(id=1))
    if db.query(PendingConfig).count() == 0:
        db.add(
            PendingConfig(
                id=1,
                default_strategy=config.DEFAULT_PENDING_STRATEGY,
                legal_deadline_hours=config.PENDING_LEGAL_DEADLINE_HOURS,
            )
        )
    db.commit()
    _seed_rules(db)
    _seed_device_versions(db)
    _seed_samples(db)
