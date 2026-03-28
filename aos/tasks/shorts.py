from __future__ import annotations

import frappe

from aos.services.analytics_service import AnalyticsService
from aos.services.ranking_service import RankingService


# ANALYTICS TASK
def aggregate_short_metrics():
    try:
        frappe.logger().info("[Shorts Task] Aggregating metrics")

        AnalyticsService.aggregate_all_shorts(limit=1000)

        frappe.logger().info("[Shorts Task] Metrics aggregation done")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Shorts Metrics Task Failed",
        )


# RANKING TASK
def update_short_ranking():
    try:
        frappe.logger().info("[Shorts Task] Updating ranking")

        RankingService.update_batch(limit=1000)

        frappe.logger().info("[Shorts Task] Ranking update done")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Shorts Ranking Task Failed",
        )
