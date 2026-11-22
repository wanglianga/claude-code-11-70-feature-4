"""纯判定逻辑：范围 / 时序 / 设备状态三项检查。

实时质量服务、延迟补判、回放沙箱共用同一套判定语义，保证“当时按旧规则判定”
与“后来按新规则判定”的差异可逐项对照。
"""
from datetime import datetime, timedelta

# 时钟偏差容忍
CLOCK_SKEW = timedelta(minutes=5)


def range_ok(value: float, threshold_min: float | None, threshold_max: float | None) -> bool:
    if threshold_min is not None and value < threshold_min:
        return False
    if threshold_max is not None and value > threshold_max:
        return False
    return True


def ts_ok(reading_ts: datetime, received_at: datetime) -> bool:
    # 读数时间不应晚于接收时间（容忍时钟偏差）
    return reading_ts <= received_at + CLOCK_SKEW


def device_ok(device_status: str | None) -> bool:
    return (device_status or "normal") == "normal"


def judge(
    value: float,
    reading_ts: datetime,
    received_at: datetime,
    device_status: str | None,
    threshold_min: float | None,
    threshold_max: float | None,
) -> dict:
    """返回三项检查结果与总结论。

    conclusion: normal | exceed | pending_review
    confirmed=False 的结论（校准/维护/时序异常）不会成为正式超标。
    """
    r_ok = range_ok(value, threshold_min, threshold_max)
    t_ok = ts_ok(reading_ts, received_at)
    d_ok = device_ok(device_status)
    if not d_ok:
        conclusion, confirmed = "pending_review", False
        detail = f"设备接收时状态为 {device_status}，保留来源，待人工复核"
    elif not t_ok:
        conclusion, confirmed = "pending_review", False
        detail = "时序异常：读数时间晚于接收时间，待人工复核"
    elif not r_ok:
        conclusion, confirmed = "exceed", True
        detail = f"数值 {value} 超出阈值 [{threshold_min}, {threshold_max}]"
    else:
        conclusion, confirmed = "normal", True
        detail = "范围/时序/设备状态检查通过"
    return {
        "range_ok": r_ok,
        "ts_ok": t_ok,
        "device_status_ok": d_ok,
        "conclusion": conclusion,
        "confirmed": confirmed,
        "detail": detail,
    }
