// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.pages["lithos-sync-console"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Lithos Sync Console"),
		single_column: true,
	});
	wrapper.lithos_console = new LithosSyncConsole(page);
};

frappe.pages["lithos-sync-console"].on_page_show = function (wrapper) {
	wrapper.lithos_console.apply_route_options();
	wrapper.lithos_console.refresh();
};

const LITHOS_CONSOLE_API = "al_qubaisi_modifications.lithos_console.";

// Grid colours follow the Lithos Sync Log list view.
const LITHOS_CELL_STATUS = {
	Posted: { colour: "green", mark: null },
	Draft: { colour: "orange", mark: null },
	Held: { colour: "yellow", mark: null },
	Changed: { colour: "purple", mark: null },
	"No sales": { colour: "light-gray", mark: "·" },
	Failed: { colour: "red", mark: "✕" },
	Missing: { colour: "missing", mark: "?" },
	"Not live": { colour: "none", mark: "" },
	Open: { colour: "none", mark: "…" },
};

// Ledger outcome of a posted day (Branch Sales ledger_status), shown as a corner dot.
const LITHOS_LEDGER_DOTS = {
	Posted: "green",
	"No BOM": "gray",
	"Consumption pending": "orange",
	Failed: "red",
	Missing: "red",
};

const LITHOS_RUN_COLOURS = {
	Queued: "gray",
	Running: "blue",
	Success: "green",
	Partial: "orange",
	Failed: "red",
};

class LithosSyncConsole {
	constructor(page) {
		this.page = page;
		this.make_filters();
		this.make_actions();
		this.$body = $(`<div class="lithos-console"></div>`).appendTo(page.main);
		this.$body.on("click", ".lithos-cell[data-store]", (e) => {
			const $cell = $(e.currentTarget);
			this.show_cell($cell.attr("data-store"), $cell.attr("data-day"));
		});
		// Every store job announces itself when it finishes; one refresh covers a burst.
		frappe.realtime.on("lithos_sync_progress", () => this.refresh_soon());
	}

	make_filters() {
		const yesterday = frappe.datetime.add_days(frappe.datetime.get_today(), -1);
		const change = () => !this.applying && this.refresh();
		this.filters = {
			company: this.page.add_field({
				fieldname: "company",
				label: __("Company"),
				fieldtype: "Link",
				options: "Company",
				change,
			}),
			store: this.page.add_field({
				fieldname: "store",
				label: __("Store"),
				fieldtype: "Link",
				options: "Lithos Store",
				get_query: () => {
					const company = this.filters.company.get_value();
					return { filters: company ? { company } : {} };
				},
				change,
			}),
			brand: this.page.add_field({ fieldname: "brand", label: __("Brand"), fieldtype: "Data", change }),
			from_date: this.page.add_field({
				fieldname: "from_date",
				label: __("From Date"),
				fieldtype: "Date",
				default: frappe.datetime.add_days(yesterday, -13),
				change,
			}),
			to_date: this.page.add_field({
				fieldname: "to_date",
				label: __("To Date"),
				fieldtype: "Date",
				default: yesterday,
				change,
			}),
		};
	}

	apply_route_options() {
		if (!frappe.route_options) return;
		this.applying = true;
		for (const [key, value] of Object.entries(frappe.route_options)) {
			this.filters[key] && this.filters[key].set_value(value);
		}
		frappe.route_options = null;
		this.applying = false;
	}

	make_actions() {
		this.page.set_primary_action(__("Quick Sync"), () => this.quick_sync(), "refresh");
		this.page.add_inner_button(__("Retry Failed"), () =>
			this.run_action("retry_failed", __("Pull the failed days in this view again?"))
		);
		this.page.add_inner_button(__("Re-post Held"), () =>
			this.run_action(
				"repost_held",
				__("Re-post the held days in this view? Map their items in the Lithos Item Workbench first.")
			)
		);
		this.page.add_inner_button(__("Post Ledger"), () =>
			this.run_action(
				"post_ledger",
				__(
					"Finish the ledger for the stores in view? Days without a Sales Invoice are posted, pending consumption is retried, and days whose items have since got a BOM are rebuilt."
				)
			)
		);
		this.page.add_menu_item(__("Lithos Control Tower"), () => frappe.set_route("lithos-control-tower"));
		this.page.add_menu_item(__("Lithos Item Workbench"), () => frappe.set_route("lithos-item-workbench"));
		this.page.add_menu_item(__("Sync Runs"), () => frappe.set_route("List", "Lithos Sync Run"));
		this.page.add_menu_item(__("Sync Logs"), () => frappe.set_route("List", "Lithos Sync Log"));
		this.page.add_menu_item(__("Lithos Stores"), () => frappe.set_route("List", "Lithos Store"));
		this.page.add_menu_item(__("LithosPOS Settings"), () =>
			frappe.set_route("Form", "LithosPOS Settings")
		);
	}

	values() {
		const values = {};
		for (const [key, field] of Object.entries(this.filters)) {
			values[key] = field.get_value() || null;
		}
		return values;
	}

	refresh() {
		const args = this.values();
		if (!args.from_date || !args.to_date) return;
		return frappe.call({ method: LITHOS_CONSOLE_API + "get_console", args }).then((r) => {
			this.data = r.message;
			this.render();
		});
	}

	refresh_soon() {
		clearTimeout(this.timer);
		this.timer = setTimeout(() => this.refresh(), 1500);
	}

	render() {
		const { days, stores, summary, runs } = this.data;
		this.$body.html(`
			${this.summary_html(summary)}
			${this.legend_html(summary)}
			<div class="lithos-grid-wrap">${stores.length ? this.grid_html(days, stores) : this.empty_html()}</div>
			${this.runs_html(runs)}
		`);
	}

	summary_html(s) {
		const card = (label, value, of, colour, hint) => `
			<div class="lithos-card ${colour ? "lithos-card-" + colour : ""}" title="${hint || ""}">
				<div class="lithos-card-label">${label}</div>
				<div class="lithos-card-value">${value}${
			of !== undefined ? `<span class="lithos-card-of"> / ${of}</span>` : ""
		}</div>
			</div>`;
		return `<div class="lithos-cards">
			${card(__("Synced Yesterday"), s.synced_yesterday, s.live, s.synced_yesterday < s.live ? "red" : "")}
			${card(
				__("{0}-day Gate", [s.gate_days]),
				s.gate,
				s.live,
				"",
				__("Live stores with {0} days in a row reconciled and no unmapped line.", [s.gate_days])
			)}
			${card(__("Held Days"), s.held, undefined, s.held ? "yellow" : "")}
			${card(__("Failed Days"), s.failed, undefined, s.failed ? "red" : "")}
			${card(__("Changed in Lithos"), s.changed, undefined, s.changed ? "purple" : "")}
			${card(__("Missing Days"), s.missing, undefined, s.missing ? "red" : "")}
			${card(
				`<a href="/app/lithos-item-workbench">${__("Unmapped Items")}</a>`,
				s.unmapped_items,
				undefined,
				s.unmapped_items ? "yellow" : ""
			)}
			${card(
				__("Pending Re-post"),
				s.pending_repost,
				undefined,
				s.pending_repost ? "orange" : "",
				__("Stores whose history is still in another company (D3).")
			)}
			${
				s.ledger_stores
					? card(
							__("Ledger Missing"),
							s.ledger_due,
							undefined,
							s.ledger_due ? "red" : "",
							__("Posted days past the Ledger From Date with no Sales Invoice yet.")
					  ) +
					  card(
							__("Consumption Pending"),
							s.consumption_pending,
							undefined,
							s.consumption_pending ? "orange" : "",
							__("Days whose Material Issue waits, usually for raw-material stock.")
					  )
					: ""
			}
		</div>`;
	}

	legend_html(summary) {
		const items = ["Posted", "Draft", "Held", "Changed", "No sales", "Failed", "Missing", "Not live"];
		return `<div class="lithos-legend">${items
			.map(
				(status) =>
					`<span><span class="lithos-swatch lithos-${
						LITHOS_CELL_STATUS[status].colour
					}"></span>${__(status)}</span>`
			)
			.join("")}<span><span class="lithos-swatch lithos-green lithos-other-company"></span>${__(
			"In another company (re-post pending)"
		)}</span>${(summary.ledger_stores ? Object.entries(LITHOS_LEDGER_DOTS) : [])
			.map(
				([status, colour]) =>
					`<span><span class="lithos-ledger-dot lithos-dot-${colour}"></span>${__("Ledger: {0}", [
						__(status),
					])}</span>`
			)
			.join("")}</div>`;
	}

	grid_html(days, stores) {
		const esc = frappe.utils.escape_html;
		const head = days
			.map((day) => {
				const m = moment(day);
				return `<th class="lithos-day ${
					[0, 6].includes(m.day()) ? "lithos-weekend" : ""
				}" title="${day}">
					<div>${m.format("DD")}</div><div class="text-muted">${m.format("ddd")}</div></th>`;
			})
			.join("");

		const rows = stores
			.map((store) => {
				const gate = store.gate
					? `<span class="indicator-pill green">${__("Gate met")}</span>`
					: `<span class="indicator-pill gray" title="${__(
							"Days in a row reconciled with no unmapped line"
					  )}">${store.streak} / ${this.data.summary.gate_days}</span>`;
				const cells = days.map((day) => this.cell_html(store, day, store.cells[day])).join("");
				return `<tr>
					<td class="lithos-store-col">
						<div><a href="/app/lithos-store/${encodeURIComponent(store.name)}">${esc(store.name)}</a>
							<span class="lithos-outlet">${esc(store.outlet_name || "")}</span></div>
						<div class="text-muted small">${esc(store.company)}${store.sync_enabled ? "" : " · " + __("sync off")}${
					store.move_to_company ? " · " + __("moves to {0}", [esc(store.move_to_company)]) : ""
				}</div>
						<div>${gate}</div>
					</td>${cells}</tr>`;
			})
			.join("");

		return `<table class="lithos-grid"><thead><tr><th class="lithos-store-col">${__(
			"Store"
		)}</th>${head}</tr></thead><tbody>${rows}</tbody></table>`;
	}

	cell_html(store, day, cell) {
		const style = LITHOS_CELL_STATUS[cell.status] || LITHOS_CELL_STATUS.Missing;
		const amount = cell.net ? format_number(cell.net, null, 0) : "";
		const label = style.mark === null ? amount : style.mark;
		const title = [day, __(cell.status), amount, cell.hold_reason || cell.message || ""]
			.filter(Boolean)
			.join(" · ");
		const clickable = !["Not live", "Open"].includes(cell.status);
		// A dot in the corner: what the ledger made of the day (M6).
		const ledger = cell.ledger_due ? "Missing" : cell.ledger_status;
		const dot = LITHOS_LEDGER_DOTS[ledger]
			? `<span class="lithos-ledger-dot lithos-dot-${LITHOS_LEDGER_DOTS[ledger]}"></span>`
			: "";
		return `<td class="lithos-cell lithos-${style.colour} ${
			cell.other_company ? "lithos-other-company" : ""
		}"
			${clickable ? `data-store="${frappe.utils.escape_html(store.name)}" data-day="${day}"` : ""}
			title="${frappe.utils.escape_html(title)}">${label}${dot}</td>`;
	}

	empty_html() {
		return `<div class="lithos-empty text-muted">${__(
			"No Lithos Store matches these filters. Stores are created by the workbook import or by hand."
		)}</div>`;
	}

	runs_html(runs) {
		if (!runs.length) return "";
		const esc = frappe.utils.escape_html;
		const rows = runs
			.map(
				(run) => `<tr>
					<td><a href="/app/lithos-sync-run/${run.name}">${run.name}</a></td>
					<td>${__(run.trigger)}</td>
					<td><span class="indicator-pill ${LITHOS_RUN_COLOURS[run.status] || "gray"}">${__(run.status)}</span></td>
					<td>${frappe.datetime.str_to_user(run.from_date)} – ${frappe.datetime.str_to_user(run.to_date)}</td>
					<td>${run.stores_done || 0} / ${run.store_count || 0}</td>
					<td>${run.days_posted || 0}</td>
					<td>${run.days_held || 0}</td>
					<td>${run.days_failed || 0}</td>
					<td>${esc(run.requested_by || "")}</td>
					<td title="${run.creation}">${frappe.datetime.comment_when(run.creation)}</td>
				</tr>`
			)
			.join("");
		return `<div class="lithos-runs">
			<h5>${__("Latest Runs")}</h5>
			<table class="table table-sm">
				<thead><tr>
					<th>${__("Run")}</th><th>${__("Trigger")}</th><th>${__("Status")}</th><th>${__("Dates")}</th>
					<th>${__("Stores")}</th><th>${__("Posted")}</th><th>${__("Held")}</th><th>${__("Failed")}</th>
					<th>${__("By")}</th><th>${__("Queued")}</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table>
		</div>`;
	}

	show_cell(store_id, day) {
		const store = this.data.stores.find((s) => s.name === store_id);
		const cell = store.cells[day];
		const esc = frappe.utils.escape_html;
		const link = (doctype, name) =>
			name
				? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${esc(name)}</a>`
				: "–";
		const money = (value) => format_currency(value || 0, "AED");
		const rows = [
			[__("Status"), __(cell.status)],
			[__("Company"), esc(cell.company || store.company)],
			[__("Branch Sales"), link("Branch Sales", cell.branch_sales)],
			[__("Lithos Net"), cell.log ? money(cell.lithos_net) : "–"],
			[__("ERPNext Net"), money(cell.erpnext_net)],
			[__("Variance"), cell.log ? money(cell.variance) : "–"],
			[__("Unmapped Lines"), cell.unmapped_lines || 0],
			[__("Hold Reason"), esc(cell.hold_reason || "–").replace(/\n/g, "<br>")],
			[__("Message"), esc(cell.message || "–").replace(/\n/g, "<br>")],
			[__("Failed Attempts"), cell.retry_count || 0],
			[__("Last Sync Run"), link("Lithos Sync Run", cell.sync_run)],
			[__("Sync Log"), link("Lithos Sync Log", cell.log)],
			[__("Error Log"), link("Error Log", cell.error_log)],
		];
		if (cell.ledger_status || cell.ledger_due) {
			rows.splice(
				3,
				0,
				[
					__("Ledger"),
					cell.ledger_due ? __("Not posted yet: use Post Ledger") : __(cell.ledger_status),
				],
				[__("Sales Invoice"), link("Sales Invoice", cell.sales_invoice)],
				[__("Consumption"), link("Stock Entry", cell.consumption_entry)],
				[__("Ledger Message"), esc(cell.ledger_message || "–").replace(/\n/g, "<br>")]
			);
		}
		if (cell.other_company) {
			rows.splice(2, 0, [
				__("Re-post"),
				__("Posted in {0}. The store's company is {1}: use Re-post on the store.", [
					esc(cell.company),
					esc(store.company),
				]),
			]);
		}

		const dialog = new frappe.ui.Dialog({
			title: `${store.name} ${store.outlet_name || ""} · ${frappe.datetime.str_to_user(day)}`,
			fields: [{ fieldtype: "HTML", fieldname: "details" }],
		});
		dialog.fields_dict.details.$wrapper.html(
			`<table class="table table-sm lithos-cell-details">${rows
				.map(([label, value]) => `<tr><th>${label}</th><td>${value}</td></tr>`)
				.join("")}</table>`
		);

		const args = { store: store.name, from_date: day, to_date: day };
		if (cell.status === "Held") {
			dialog.set_primary_action(__("Re-post Day"), () => {
				dialog.hide();
				this.call("repost_held", args);
			});
			dialog.set_secondary_action_label(__("Open Workbench"));
			dialog.set_secondary_action(() =>
				frappe.set_route("lithos-item-workbench", { store: store.name, view: "Unmapped" })
			);
		} else if (["Failed", "Missing", "No sales"].includes(cell.status)) {
			dialog.set_primary_action(__("Pull Day Again"), () => {
				dialog.hide();
				this.call("quick_sync", { stores: [store.name], from_date: day, to_date: day });
			});
		}
		dialog.show();
	}

	quick_sync() {
		const values = this.values();
		const yesterday = frappe.datetime.add_days(frappe.datetime.get_today(), -1);
		const dialog = new frappe.ui.Dialog({
			title: __("Quick Sync"),
			fields: [
				{
					fieldname: "stores",
					fieldtype: "MultiSelectList",
					label: __("Stores"),
					get_data: (txt) => frappe.db.get_link_options("Lithos Store", txt),
				},
				{
					fieldname: "companies",
					fieldtype: "MultiSelectList",
					label: __("Or Companies"),
					description: __("Every store with sync enabled in these companies."),
					get_data: (txt) => frappe.db.get_link_options("Company", txt),
				},
				{ fieldtype: "Section Break" },
				{
					fieldname: "from_date",
					fieldtype: "Date",
					label: __("From Date"),
					reqd: 1,
					default: yesterday,
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "to_date",
					fieldtype: "Date",
					label: __("To Date"),
					reqd: 1,
					default: yesterday,
				},
				{ fieldtype: "Section Break" },
				{
					fieldtype: "HTML",
					options: `<p class="text-muted small">${__(
						"Days already posted are re-checked against Lithos, never posted twice. Today is pulled once it has closed."
					)}</p>`,
				},
			],
			primary_action_label: __("Queue"),
			primary_action: (args) => {
				dialog.hide();
				this.call("quick_sync", args);
			},
		});
		dialog.show();
		if (values.store) dialog.set_value("stores", [values.store]);
		else if (values.company) dialog.set_value("companies", [values.company]);
	}

	run_action(method, question) {
		frappe.confirm(question, () => this.call(method, this.values()));
	}

	call(method, args) {
		return frappe.call({ method: LITHOS_CONSOLE_API + method, args, freeze: true }).then((r) => {
			frappe.show_alert({
				message: __("Queued {0}. The grid updates as each store finishes.", [
					`<a href="/app/lithos-sync-run/${r.message}">${r.message}</a>`,
				]),
				indicator: "blue",
			});
			this.refresh_soon();
		});
	}
}
