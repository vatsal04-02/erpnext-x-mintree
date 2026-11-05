"""Brand & marketing overview (MINTREE_META_MARKETING_MODULE.md). Every figure comes from records;
performance is grouped by its source so DEMO, manually entered and (future) Meta API figures are
never added together. Cost per lead is shown only where leads exist."""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate


@frappe.whitelist(methods=["GET"])
def overview() -> dict:
	if not frappe.has_permission("Marketing Campaign", "read"):
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	today = getdate(nowdate())
	campaigns = frappe.get_all(
		"Marketing Campaign",
		fields=["name", "campaign_name", "platform", "status", "budget", "start_date", "end_date", "is_demo"],
		order_by="start_date desc",
		limit=200,
	)
	perf = frappe.get_all(
		"Campaign Performance",
		fields=["campaign", "source", "period_start", "spend", "impressions", "clicks", "leads", "purchase_value"],
		limit=5000,
	)
	by_source: dict = {}
	by_campaign: dict = {}
	for p in perf:
		s = by_source.setdefault(p.source, {"spend": 0.0, "impressions": 0, "clicks": 0, "leads": 0, "purchase_value": 0.0, "weeks": {}})
		c = by_campaign.setdefault(p.campaign, {"spend": 0.0, "leads": 0, "clicks": 0, "impressions": 0, "source": p.source})
		for agg in (s, c):
			agg["spend"] += flt(p.spend)
			agg["leads"] += int(p.leads or 0)
			agg["clicks"] += int(p.clicks or 0)
			agg["impressions"] += int(p.impressions or 0)
		s["purchase_value"] += flt(p.purchase_value)
		wk = getdate(p.period_start).strftime("%G-W%V")
		w = s["weeks"].setdefault(wk, {"week": wk, "spend": 0.0, "leads": 0})
		w["spend"] += flt(p.spend)
		w["leads"] += int(p.leads or 0)
	for s in by_source.values():
		s["cost_per_lead"] = s["spend"] / s["leads"] if s["leads"] else None
		s["weeks"] = sorted(s["weeks"].values(), key=lambda w: w["week"])[-12:]
	for c in by_campaign.values():
		c["cost_per_lead"] = c["spend"] / c["leads"] if c["leads"] else None
	content = []
	if frappe.has_permission("Marketing Content", "read"):
		content = frappe.get_all(
			"Marketing Content",
			filters={"planned_date": ["between", [add_days(today, -7), add_days(today, 21)]]},
			fields=["name", "title", "campaign", "content_type", "platform", "planned_date", "status", "is_demo"],
			order_by="planned_date asc",
		)
	meta = frappe.db.get_value("Meta Connection", {}, ["name", "connection_state", "last_tested_at"], as_dict=True)
	return {
		"campaigns": campaigns,
		"by_campaign": by_campaign,
		"by_source": by_source,
		"content": content,
		"awaiting_approval": {
			"campaigns": sum(1 for c in campaigns if c.status == "Awaiting Approval"),
			"content": sum(1 for c in content if c.status == "In Review"),
		},
		"meta": meta,
	}
