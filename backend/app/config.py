import os

# 数据库连接：默认使用容器内置 PostgreSQL（ccdev/ccdev@127.0.0.1:5432/ccdev）
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://ccdev:ccdev@127.0.0.1:5432/ccdev",
)

# 会话令牌签名密钥（演示环境默认值，生产应通过环境变量覆盖）
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "24"))

# 网关接入令牌（X-Ingest-Token）
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "ingest-dev-token")

# 数据库写入延迟高风险阈值（毫秒）：超过则调度器进入降级模式
WRITE_LATENCY_THRESHOLD_MS = int(os.getenv("WRITE_LATENCY_THRESHOLD_MS", "800"))

# 告警延迟目标（分钟）：值班员需在该时间内看到高风险超标
ALERT_LATENCY_TARGET_MIN = int(os.getenv("ALERT_LATENCY_TARGET_MIN", "15"))

# 实时/补传分流阈值（秒）：读数时间早于当前该时长即视为补传，交由恢复编排器回补
BACKFILL_AGE_SEC = int(os.getenv("BACKFILL_AGE_SEC", "600"))

# 调度器每 tick 单分区批量上限 / 全局上限
BATCH_MAX_ITEMS = int(os.getenv("BATCH_MAX_ITEMS", "50"))
SCHED_TICK_LIMIT = int(os.getenv("SCHED_TICK_LIMIT", "200"))

# 恢复编排器每次运行单分区回补上限
RECOVERY_MAX_ITEMS = int(os.getenv("RECOVERY_MAX_ITEMS", "500"))

# 延迟质量判定：规则维护期间待判队列的默认路线（snapshot | event_log）与法定补判时限（小时）
DEFAULT_PENDING_STRATEGY = os.getenv("DEFAULT_PENDING_STRATEGY", "snapshot")
PENDING_LEGAL_DEADLINE_HOURS = int(os.getenv("PENDING_LEGAL_DEADLINE_HOURS", "24"))

# 补判调度：每 tick 处理上限 / 巡检间隔（秒）
REJUDGE_TICK_LIMIT = int(os.getenv("REJUDGE_TICK_LIMIT", "200"))
REJUDGE_INTERVAL_SEC = float(os.getenv("REJUDGE_INTERVAL_SEC", "2.0"))

# 策略建议阈值：平均维护跨度 / 需要扫描的日志行数 / 需复现监管批次数
STRATEGY_SPAN_THRESHOLD_HOURS = float(os.getenv("STRATEGY_SPAN_THRESHOLD_HOURS", "4"))
STRATEGY_LOG_SCAN_THRESHOLD = int(os.getenv("STRATEGY_LOG_SCAN_THRESHOLD", "5000"))
STRATEGY_BATCH_THRESHOLD = int(os.getenv("STRATEGY_BATCH_THRESHOLD", "3"))

# 前端构建产物目录（存在时由后端直接托管，便于单进程本地运行）
FRONTEND_DIST = os.getenv(
    "FRONTEND_DIST",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "frontend", "dist"),
)
