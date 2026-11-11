// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.pages["lithos-control-tower"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Lithos Control Tower"),
		single_column: true,
	});
	wrapper.lithos_tower = new LithosControlTower(page);
};

frappe.pages["lithos-control-tower"].on_page_show = function (wrapper) {
	wrapper.lithos_tower.refresh();
};

const LITHOS_TOWER_API = "al_qubaisi_modifications.lithos_tower.";

// Categorical slots in fixed order, validated against the desk's card surface in each
// theme (#ffffff light, #171717 dark). Past the last slot, companies fold into "Other".
const LITHOS_TOWER_SERIES = {
	light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"],
	dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"],
};
const LITHOS_TOWER_OTHER = { light: "#8a8a8a", dark: "#7a7a7a" };

const LITHOS_TOWER_REPORTS = {
	reconciliation: "Lithos to ERPNext Reconciliation",
	coverage: "Lithos Item and BOM Coverage",
	movement: "Outlet Stock Movement",
	gap: "Lithos Sync Gap",
};

class LithosControlTower {
	constructor(page) {
		this.page = page;
		// A company keeps its colour while it stays on the chart, whatever the filters do.
		this.slots = new Map();
		this.lithos_user = frappe.user.has_role(["System Manager", "Lithos Manager"]);
		this.make_filters();
		this.make_menu();
		this.$body = $(`<div class="lithos-tower"></div>`).appendTo(page.main);
		this.$body.on("click", ".lithos-tower-card[data-card]", (e) => {
			const card = this.cards[$(e.currentTarget).attr("data-card")];
			card && card.open && card.open();
		});
		// Every store job announces itself; refresh once the run's last store is done.
		frappe.realtime.on("lithos_sync_progress", (data) => {
			if (!data || data.done >= data.total) this.refresh_soon();
		});
	}

	make_filters() {
		const yesterday = frappe.datetime.add_days(frappe.datetime.get_today(), -1);
		const change = () => this.refresh_soon(300);
		this.filters = {
			companies: this.page.add_field({
				fieldname: "companies",
				label: __("Companies"),
				fieldtype: "MultiSelectList",
				get_data: (txt) => frappe.db.get_link_options("Company", txt),
				change,
			}),
			brands: this.page.add_field({
				fieldname: "brands",
				label: __("Brands"),
				fieldtype: "MultiSelectList",
				get_data: (txt) => frappe.xcall(LITHOS_TOWER_API + "brand_options", { txt }),
				change,
			}),
			stores: this.page.add_field({
				fieldname: "stores",
				label: __("Stores"),
				fieldtype: "MultiSelectList",
				get_data: (txt) =>
					frappe.xcall(LITHOS_TOWER_API + "store_options", {
						txt,
						companies: this.filters.companies.get_value(),
					}),
				change,
			}),
			from_date: this.page.add_field({
				fieldname: "from_date",
				label: __("From Date"),
				fieldtype: "Date",
				default: frappe.datetime.add_days(yesterday, -29),
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

	make_menu() {
		this.page.set_secondary_action(__("Refresh"), () => this.refresh(), "refresh");
		if (this.lithos_user) {
			this.page.add_menu_item(__("Lithos Sync Console"), () => frappe.set_route("lithos-sync-console"));
			this.page.add_menu_item(__("Lithos Item Workbench"), () =>
				frappe.set_route("lithos-item-workbench")
			);
		}
		for (const name of Object.values(LITHOS_TOWER_REPORTS)) {
			this.page.add_menu_item(__(name), () => this.open_report(name));
		}
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
		return frappe.call({ method: LITHOS_TOWER_API + "get_tower", args }).then((r) => {
			this.data = r.message;
			this.render();
		});
	}

	refresh_soon(wait = 1500) {
		clearTimeout(this.timer);
		this.timer = setTimeout(() => this.refresh(), wait);
	}

	// The report with this page's scope, plus any filters of its own.
	open_report(name, extra = {}) {
		const v = this.values();
		frappe.route_options = Object.assign(
			{ from_date: v.from_date, to_date: v.to_date },
			v.companies && v.companies.length ? { company: v.companies } : {},
			v.stores && v.stores.length ? { store: v.stores } : {},
			extra
		);
		frappe.set_route("query-report", name);
	}

	render() {
		const d = this.data;
		this.cards = {};
		if (!d.stores) {
			this.$body.html(
				`<div class="lithos-tower-empty text-muted">${__(
					"No Lithos Store in view. Change the filters, or ask for access to the outlet's company."
				)}</div>`
			);
			return;
		}
		this.$body.html(`
			${this.section_html(__("Sync"), __("Yesterday and the range"), this.sync_cards(d.sync))}
			${this.section_html(
				__("Sales"),
				`${frappe.datetime.str_to_user(d.from_date)} – ${frappe.datetime.str_to_user(d.to_date)}`,
				this.amount_cards(d.amounts)
			)}
			${this.section_html(__("Stock"), __("Outlet warehouses"), this.material_cards(d.material))}
			${d.material.ledger_stores ? "" : this.ledger_note_html()}
			<div class="lithos-tower-charts">
				<div class="lithos-tower-panel lithos-tower-wide">
					<h5>${__("Net Sales by Day")}</h5>
					<div class="lithos-tower-net-chart"></div>
					<div class="lithos-tower-net-table"></div>
				</div>
				<div class="lithos-tower-panel">
					<h5>${__("Top {0} Stores", [10])}</h5>
					${this.top_stores_html(d.charts.top_stores)}
				</div>
				<div class="lithos-tower-panel">
					<h5>${__("Lithos against ERPNext")}</h5>
					<div class="lithos-tower-variance-chart"></div>
				</div>
			</div>
		`);
		this.render_net_chart(d.charts, d.sync.missing);
		this.render_variance_chart(d.charts);
	}

	section_html(title, subtitle, cards) {
		return `<div class="lithos-tower-section">
			<div class="lithos-tower-section-head"><h5>${title}</h5><span class="text-muted">${subtitle}</span></div>
			<div class="lithos-tower-cards">${cards.map((card) => this.card_html(card)).join("")}</div>
		</div>`;
	}

	card_html(card) {
		const key = `card-${Object.keys(this.cards).length}`;
		this.cards[key] = card;
		return `<div class="lithos-tower-card ${card.tone ? "lithos-tower-" + card.tone : ""} ${
			card.open ? "lithos-tower-clickable" : ""
		}" data-card="${key}" title="${frappe.utils.escape_html(card.hint || "")}">
			<div class="lithos-tower-card-label">${card.label}</div>
			<div class="lithos-tower-card-value">${card.value}</div>
			${card.sub ? `<div class="lithos-tower-card-sub">${card.sub}</div>` : ""}
		</div>`;
	}

	sync_cards(s) {
		const behind = s.furthest_behind;
		const gap = (reason) => () => this.open_report(LITHOS_TOWER_REPORTS.gap, { reason });
		const cards = [
			{
				label: __("Synced Yesterday"),
				value: `${s.synced_yesterday}<span class="lithos-tower-of"> / ${s.live}</span>`,
				sub: __("Live stores posted or closed"),
				tone: s.synced_yesterday < s.live ? "red" : "",
				open: this.lithos_user ? () => frappe.set_route("lithos-sync-console") : null,
			},
			{
				label: __("Failed Runs"),
				value: s.failed_runs,
				sub: __("Last 24 hours"),
				tone: s.failed_runs ? "red" : "",
				open: this.lithos_user ? () => frappe.set_route("List", "Lithos Sync Run") : null,
			},
			{
				label: __("Last Successful Sync"),
				value: s.last_success ? frappe.datetime.comment_when(s.last_success) : __("Never"),
				sub: behind
					? __("Furthest behind: {0} at {1}", [
							frappe.utils.escape_html(behind.name),
							frappe.datetime.str_to_user(behind.last_synced_date),
					  ])
					: "",
				hint: s.last_success || "",
			},
			{
				label: __("Held Days"),
				value: s.held,
				tone: s.held ? "yellow" : "",
				open: gap("Held"),
			},
			{
				label: __("Failed Days"),
				value: s.failed,
				tone: s.failed ? "red" : "",
				open: gap("Failed"),
			},
			{
				label: __("Missing Days"),
				value: s.missing,
				sub: __("Live, but never synced"),
				tone: s.missing ? "red" : "",
				open: gap("Not synced"),
			},
		];
		if (s.unmapped_items !== null) {
			cards.push({
				label: __("Unmapped Items"),
				value: s.unmapped_items,
				sub: __("All stores"),
				tone: s.unmapped_items ? "yellow" : "",
				open: () => frappe.set_route("lithos-item-workbench"),
			});
		}
		cards.push({
			label: __("Items without a BOM"),
			value: s.items_without_bom,
			sub: __("Sold in the range"),
			hint: __(
				"Sold items with no default BOM that are not stock items: nothing leaves stock for them."
			),
			tone: s.items_without_bom ? "orange" : "",
			open: () => this.open_report(LITHOS_TOWER_REPORTS.coverage, { coverage: "No BOM" }),
		});
		return cards;
	}

	amount_cards(a) {
		const money = (value) => format_currency(value, "AED", 0);
		return [
			{ label: __("Net Sales"), value: money(a.net), sub: __("{0} store-days posted", [a.days]) },
			{ label: __("VAT"), value: money(a.vat) },
			{ label: __("Gross Sales"), value: money(a.gross), sub: __("Net plus VAT") },
			{
				label: __("Voids"),
				value: money(a.voids),
				sub: __("Net of voided sales, not in Net Sales"),
			},
			{
				label: __("Variance"),
				value: money(a.variance),
				sub: a.variance_days
					? __("{0} day(s) differ from Lithos", [a.variance_days])
					: __("Every day reconciles"),
				tone: a.variance_days ? "red" : "",
				open: () => this.open_report(LITHOS_TOWER_REPORTS.reconciliation, { only_variances: 1 }),
			},
		];
	}

	material_cards(m) {
		const money = (value) => format_currency(value, "AED", 0);
		const qty = (value) => format_number(value, null, 2);
		const movement = () => this.open_report(LITHOS_TOWER_REPORTS.movement);
		return [
			{
				label: __("Stock In from the Group"),
				value: money(m.stock_in.value),
				sub: __("Qty {0} · {1} invoice(s)", [qty(m.stock_in.qty), m.stock_in.documents]),
				open: movement,
			},
			{
				label: __("Stock Out by Recipe"),
				value: money(m.stock_out.value),
				sub: __("Qty {0} · {1} issue(s)", [qty(m.stock_out.qty), m.stock_out.documents]),
				open: movement,
			},
			{
				label: __("Not Yet Received"),
				value: money(m.not_received.value),
				sub: __("{0} supply invoice(s) not counted in", [m.not_received.documents]),
				tone: m.not_received.documents ? "orange" : "",
				open: () =>
					frappe.set_route("List", "Purchase Invoice", {
						docstatus: 0,
						is_internal_supplier: 1,
					}),
			},
			{
				label: __("Materials at Zero"),
				value: m.at_zero,
				sub: __("Used in the range, none left"),
				tone: m.at_zero ? "red" : "",
				open: () => this.open_report(LITHOS_TOWER_REPORTS.movement, { hide_no_movement: 0 }),
			},
			{
				label: __("Consumption Pending"),
				value: m.consumption_pending,
				sub: __("Days waiting for stock"),
				tone: m.consumption_pending ? "orange" : "",
				open: () => this.open_report(LITHOS_TOWER_REPORTS.gap, { reason: "Consumption pending" }),
			},
		];
	}

	ledger_note_html() {
		return `<div class="lithos-tower-note text-muted">${__(
			"No store in view posts to the ledger yet, so nothing leaves stock by recipe. That starts at each store's Ledger From Date."
		)}</div>`;
	}

	mode() {
		return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
	}

	// The companies on the chart, the rest folded into one series once slots run out.
	net_series(by_company) {
		const slots = LITHOS_TOWER_SERIES.light.length;
		const shown = by_company.length > slots ? by_company.slice(0, slots - 1) : by_company;
		const rest = by_company.slice(shown.length);

		const names = new Set(shown.map((row) => row.company));
		for (const company of [...this.slots.keys()]) {
			if (!names.has(company)) this.slots.delete(company);
		}
		const taken = new Set(this.slots.values());
		for (const row of shown) {
			if (this.slots.has(row.company)) continue;
			let slot = 0;
			while (taken.has(slot)) slot++;
			this.slots.set(row.company, slot);
			taken.add(slot);
		}

		const palette = LITHOS_TOWER_SERIES[this.mode()];
		const series = shown.map((row) => ({
			name: row.company,
			values: row.values,
			total: row.total,
			colour: palette[this.slots.get(row.company)],
		}));
		if (rest.length) {
			series.push({
				name: __("Other companies ({0})", [rest.length]),
				values: rest[0].values.map((_v, i) => rest.reduce((sum, row) => sum + row.values[i], 0)),
				total: rest.reduce((sum, row) => sum + row.total, 0),
				colour: LITHOS_TOWER_OTHER[this.mode()],
			});
		}
		return series;
	}

	render_net_chart(charts, missing) {
		const $chart = this.$body.find(".lithos-tower-net-chart");
		const series = this.net_series(charts.by_company);
		if (!series.length) {
			$chart.html(
				`<div class="lithos-tower-empty text-muted">${__("No sales posted in the range.")}</div>`
			);
			return;
		}
		new frappe.Chart($chart[0], {
			type: "line",
			height: 260,
			data: {
				labels: charts.days.map((day) => moment(day).format("D MMM")),
				datasets: series.map((s) => ({ name: s.name, values: s.values })),
			},
			colors: series.map((s) => s.colour),
			axisOptions: { xIsSeries: 1, xAxisMode: "tick", yAxisMode: "span" },
			lineOptions: { hideDots: charts.days.length > 31 ? 1 : 0, dotSize: 4, regionFill: 0 },
			tooltipOptions: { formatTooltipY: (value) => format_currency(value, "AED", 0) },
			// The table below is the legend; the chart's own truncates long company names.
			showLegend: 0,
		});

		// The table view: the chart's companies with their totals, so no colour stands alone.
		const total = series.reduce((sum, s) => sum + s.total, 0);
		const esc = frappe.utils.escape_html;
		this.$body.find(".lithos-tower-net-table").html(`
			${
				missing
					? `<div class="text-muted small">${__(
							"{0} store-day(s) in the range were never synced and show as zero. See Missing Days.",
							[missing]
					  )}</div>`
					: ""
			}
			<table class="table table-sm lithos-tower-table">
				<thead><tr><th>${__("Company")}</th><th class="text-right">${__("Net Sales")}</th>
					<th class="text-right">${__("Share")}</th></tr></thead>
				<tbody>${series
					.map(
						(s) => `<tr>
						<td><span class="lithos-tower-key" style="background:${s.colour}"></span>${esc(s.name)}</td>
						<td class="text-right">${format_currency(s.total, "AED", 0)}</td>
						<td class="text-right">${total ? format_number((s.total / total) * 100, null, 1) : 0}%</td>
					</tr>`
					)
					.join("")}</tbody>
			</table>`);
	}

	top_stores_html(rows) {
		if (!rows.length) {
			return `<div class="lithos-tower-empty text-muted">${__("No sales posted in the range.")}</div>`;
		}
		const esc = frappe.utils.escape_html;
		const max = Math.max(...rows.map((row) => row.net)) || 1;
		const colour = LITHOS_TOWER_SERIES[this.mode()][0];
		return `<div class="lithos-tower-bars">${rows
			.map(
				(row) => `<div class="lithos-tower-bar-row" title="${esc(
					`${row.store} ${row.outlet_name || ""} · ${format_currency(row.net, "AED", 2)} · ${__(
						"{0} days",
						[row.days]
					)}`
				)}">
					<div class="lithos-tower-bar-label">
						<span>${esc(row.store)}</span>
						<span class="text-muted">${esc(row.outlet_name || "")}</span>
					</div>
					<div class="lithos-tower-bar-track">
						<div class="lithos-tower-bar" style="width:${(row.net / max) * 100}%;background:${colour}"></div>
					</div>
					<div class="lithos-tower-bar-value">${format_currency(row.net, "AED", 0)}</div>
				</div>`
			)
			.join("")}</div>`;
	}

	render_variance_chart(charts) {
		const $chart = this.$body.find(".lithos-tower-variance-chart");
		if (!charts.variance.some((value) => Math.abs(value) >= 0.005)) {
			$chart.html(`<div class="lithos-tower-reconciled">
				<div class="lithos-tower-card-value">${format_currency(0, "AED", 2)}</div>
				<div class="text-muted">${__(
					"Every synced day in the range reconciles: Lithos net equals ERPNext net plus ignored lines."
				)}</div>
			</div>`);
			return;
		}
		new frappe.Chart($chart[0], {
			type: "bar",
			height: 220,
			data: {
				labels: charts.days.map((day) => moment(day).format("D MMM")),
				datasets: [{ name: __("Lithos minus ERPNext"), values: charts.variance }],
			},
			colors: [LITHOS_TOWER_SERIES[this.mode()][0]],
			axisOptions: { xIsSeries: 1, xAxisMode: "tick" },
			barOptions: { spaceRatio: 0.4 },
			tooltipOptions: { formatTooltipY: (value) => format_currency(value, "AED", 2) },
			showLegend: 0,
		});
		$chart.append(
			`<div class="text-muted small">${__(
				"Open the reconciliation from the Variance card for each day's detail."
			)}</div>`
		);
	}
}
