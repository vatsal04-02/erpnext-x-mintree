// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt
// The Decision Centre page. Presentation only: the queue, priorities, permissions and actions are all
// computed and enforced on the server (factory_ops.decisions); this page asks and shows.

frappe.pages["decision-centre"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Decision Centre"), single_column: true });
	wrapper.decision_centre = new DecisionCentre(page);
};
frappe.pages["decision-centre"].on_page_show = function (wrapper) {
	wrapper.decision_centre && wrapper.decision_centre.refresh();
};

const FO_DEPARTMENTS = ["Procurement", "Inventory & Stores", "Production", "Quality Control", "Dispatch & Sales", "Plant Maintenance", "Accounts"];
const FO_PRIORITY_PILL = { Critical: "red", High: "orange", Medium: "blue", Low: "gray" };
const FO_STATE_PILL = { Blocked: "red", Overdue: "orange", "Needs attention": "yellow", Pending: "blue" };
const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

class DecisionCentre {
	constructor(page) {
		this.page = page;
		this.make_filters();
		this.$body = $('<div class="fo-dc"></div>').appendTo(page.main);
		page.set_primary_action(__("Refresh"), () => this.refresh(true), "refresh");
		this.timer = setInterval(() => {
			if (page.wrapper && page.wrapper.is(":visible")) this.refresh();
		}, 60000);
	}

	make_filters() {
		const on_change = () => this.refresh();
		const field = (df) => this.page.add_field({ ...df, change: on_change });
		this.f = {
			department: field({ fieldname: "department", label: __("Department"), fieldtype: "Select", options: ["", ...FO_DEPARTMENTS].join("\n") }),
			priority: field({ fieldname: "priority", label: __("Priority"), fieldtype: "Select", options: "\nCritical\nHigh\nMedium\nLow" }),
			state: field({ fieldname: "state", label: __("Status"), fieldtype: "Select", options: "\nBlocked\nOverdue\nNeeds attention\nPending" }),
			from_date: field({ fieldname: "from_date", label: __("From"), fieldtype: "Date" }),
			to_date: field({ fieldname: "to_date", label: __("To"), fieldtype: "Date" }),
			responsible: field({ fieldname: "responsible", label: __("Responsible"), fieldtype: "Link", options: "User" }),
			text: field({ fieldname: "text", label: __("Search"), fieldtype: "Data" }),
		};
	}

	filters() {
		const out = {};
		Object.entries(this.f).forEach(([k, ctl]) => {
			const v = ctl.get_value();
			if (v) out[k] = v;
		});
		return out;
	}

	refresh(force) {
		return frappe
			.call({
				method: "factory_ops.decisions.queue.get_decision_queue",
				args: { filters: this.filters(), refresh: force ? 1 : 0 },
				freeze: !!force,
			})
			.then((r) => this.render(r.message));
	}

	render(data) {
		this.$body.empty();
		this.render_summary(data.summary);
		const note = [];
		if (data.scope === "assigned") note.push(__("You see the items assigned to you."));
		if (data.truncated && data.truncated.length) note.push(__("Some lists were cut at the row limit: {0}", [data.truncated.join(", ")]));
		note.push(__("Updated {0}", [esc(frappe.datetime.str_to_user(data.generated_at))]));
		this.$body.append(`<div class="text-muted small" style="margin:6px 0 10px">${note.join(" · ")}</div>`);
		this.render_table(data.entries, data.matching);
	}

	render_summary(s) {
		const cards = [
			["total_pending", __("Pending decisions"), ""],
			["critical", __("Critical"), "red"],
			["overdue", __("Overdue"), "orange"],
			["qc_approvals_pending", __("QC approvals"), ""],
			["procurement_approvals_pending", __("Procurement approvals"), ""],
			["production_blockers", __("Production blockers"), "red"],
			["dispatch_delays", __("Dispatch delays"), "orange"],
		];
		const html = cards
			.map(
				([k, label, tone]) => `<div class="fo-dc-card ${tone}" data-k="${k}" style="flex:1 1 130px;min-width:130px;border:1px solid var(--border-color);border-radius:8px;padding:10px 12px;cursor:pointer">
					<div class="text-muted small">${esc(label)}</div><div style="font-size:24px;font-weight:600">${esc(s[k])}</div></div>`
			)
			.join("");
		this.$body.append(`<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px">${html}</div>`);
		const dept = FO_DEPARTMENTS.map((d) => `<a class="fo-dc-dept" data-d="${esc(d)}" style="margin-right:14px;cursor:pointer">${esc(d)} <b>${esc(s.by_department[d] || 0)}</b></a>`).join("");
		this.$body.append(`<div style="margin-top:10px">${dept}</div>`);
		this.$body.find(".fo-dc-dept").on("click", (e) => {
			this.f.department.set_value($(e.currentTarget).data("d"));
		});
		this.$body.find(".fo-dc-card").on("click", (e) => {
			const k = $(e.currentTarget).data("k");
			if (k === "critical") this.f.priority.set_value("Critical");
			else if (k === "overdue") this.f.state.set_value("Overdue");
			else if (k === "production_blockers") this.f.state.set_value("Blocked");
		});
	}

	render_table(rows, matching) {
		if (!rows.length) {
			this.$body.append(`<div class="text-muted" style="padding:30px 0">${__("Nothing is waiting for a decision in what you can see.")}</div>`);
			return;
		}
		const head = ["Priority", "Department", "Issue", "Document", "Status", "Raised", "Pending", "Responsible", ""].map((h) => `<th>${__(h)}</th>`).join("");
		const body = rows.map((r) => this.row_html(r)).join("");
		this.$body.append(`<div class="table-responsive"><table class="table table-bordered table-sm" style="font-size:13px"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
		if (matching > rows.length) this.$body.append(`<div class="text-muted small">${__("Showing {0} of {1}", [rows.length, matching])}</div>`);
		this.$body.find("[data-act]").on("click", (e) => this.act($(e.currentTarget)));
	}

	row_html(r) {
		const link = frappe.utils.get_form_link(r.ref_doctype, r.ref_name);
		const who = r.responsible_name || r.responsible_department || "";
		const also = r.also && r.also.length ? `<div class="text-muted small">${__("Also")}: ${esc(r.also.join(", "))}</div>` : "";
		const state = `<span class="indicator-pill ${FO_STATE_PILL[r.state] || "gray"}">${esc(__(r.state))}</span>`;
		return `<tr data-key="${esc(r.key)}">
			<td><span class="indicator-pill ${FO_PRIORITY_PILL[r.priority] || "gray"}">${esc(__(r.priority))}</span></td>
			<td>${esc(r.department)}${also}</td>
			<td><div>${esc(r.issue)}</div><div class="text-muted small">${esc(r.category)}</div></td>
			<td><a href="${link}">${esc(r.ref_doctype)}<br>${esc(r.ref_name)}</a></td>
			<td>${state}<div class="text-muted small">${esc(r.status)}</div></td>
			<td>${r.since ? esc(frappe.datetime.str_to_user(r.since.slice(0, 10))) : ""}</td>
			<td>${esc(r.age_label)}</td>
			<td>${esc(who)}${r.raised_by ? `<div class="text-muted small">${__("raised by")} ${esc(r.raised_by)}</div>` : ""}</td>
			<td style="white-space:nowrap">${this.actions_html(r, link)}</td></tr>`;
	}

	actions_html(r, link) {
		const a = r.action || {};
		const out = [];
		if (a.id === "approve_po" || a.id === "submit") {
			const off = a.permitted ? "" : `disabled title="${esc(a.reason)}"`;
			out.push(`<button class="btn btn-xs btn-primary" data-act="${esc(a.id)}" ${off}>${esc(__(a.label))}</button>`);
		} else {
			out.push(`<a class="btn btn-xs btn-default" href="${link}" ${a.reason ? `title="${esc(a.reason)}"` : ""}>${esc(__(a.label || "Open"))}</a>`);
		}
		out.push(`<button class="btn btn-xs btn-default" data-act="acknowledge">${__("Acknowledge")}</button>`);
		out.push(`<button class="btn btn-xs btn-default" data-act="assign">${__("Assign")}</button>`);
		out.push(`<button class="btn btn-xs btn-default" data-act="note">${__("Note")}</button>`);
		return out.join(" ");
	}

	act($btn) {
		const key = $btn.closest("tr").data("key");
		const action = $btn.data("act");
		const send = (extra) =>
			frappe
				.call({ method: "factory_ops.decisions.actions.run_decision_action", args: { entry_key: key, action, ...(extra || {}) }, freeze: true })
				.then((r) => {
					const m = r.message || {};
					frappe.show_alert({ message: esc(m.message || ""), indicator: m.ok ? "green" : "red" }, 7);
					this.refresh(true);
				});
		if (action === "assign") {
			frappe.prompt(
				[{ fieldname: "assignee", label: __("Assign to"), fieldtype: "Link", options: "User", reqd: 1 }, { fieldname: "note", label: __("Message"), fieldtype: "Small Text" }],
				(v) => send({ assignee: v.assignee, note: v.note }),
				__("Assign"),
				__("Assign")
			);
		} else if (action === "note") {
			frappe.prompt([{ fieldname: "note", label: __("Note"), fieldtype: "Small Text", reqd: 1 }], (v) => send({ note: v.note }), __("Add a note"), __("Add"));
		} else if (action === "approve_po" || action === "submit") {
			frappe.confirm(__("Approve this now? It is recorded under your name."), () => send());
		} else {
			send();
		}
	}
}
