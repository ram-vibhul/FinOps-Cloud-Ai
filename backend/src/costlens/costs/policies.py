"""Retention policies for cost data."""

from costlens.privacy.retention import RetentionPolicy, register

register(
    RetentionPolicy(
        kind="cost_line_items",
        label="Detailed cost lines",
        description="Every cost line by resource and day. Needed for resource-level history.",
        table="cost_line_items",
        time_column="charge_date",
        default_days=1095,  # 3 years
        min_days=365,
        max_days=3650,
    )
)
register(
    RetentionPolicy(
        kind="cost_daily",
        label="Daily cost summaries",
        description="Daily totals by account, service and region, used by charts and reports.",
        table="cost_daily",
        time_column="charge_date",
        default_days=2555,  # 7 years
        min_days=730,
        max_days=3650,
    )
)
register(
    RetentionPolicy(
        kind="cost_anomalies",
        label="Cost anomalies",
        description="Detected unusual cost increases and what people said about them.",
        table="cost_anomalies",
        time_column="day",
        default_days=1095,
        min_days=180,
        max_days=3650,
    )
)
register(
    RetentionPolicy(
        kind="change_events",
        label="Change timeline",
        description="Deployments and infrastructure changes shown next to costs.",
        table="change_events",
        time_column="occurred_at",
        default_days=730,
        min_days=90,
        max_days=3650,
    )
)
register(
    RetentionPolicy(
        kind="cost_hourly",
        label="Hourly costs",
        description="Cost per hour for recent days, from hourly billing exports.",
        table="cost_hourly",
        time_column="hour",
        default_days=14,
        min_days=7,
        max_days=31,
    )
)
