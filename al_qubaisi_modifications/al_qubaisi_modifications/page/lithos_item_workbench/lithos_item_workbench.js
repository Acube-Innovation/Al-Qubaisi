// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.pages["lithos-item-workbench"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Lithos Item Workbench"),
		single_column: true,
	});
	wrapper.workbench = new LithosItemWorkbench(page);
};

frappe.pages["lithos-item-workbench"].on_page_show = function (wrapper) {
	wrapper.workbench.apply_route_options();
	wrapper.workbench.refresh();
};

const LITHOS_WORKBENCH_API = "al_qubaisi_modifications.lithos_workbench.";

const LITHOS_VIEWS = [
	{ name: "Unmapped", help: __("No ERPNext item yet. Under the Hold policy these hold their days.") },
	{ name: "To review", help: __("Guessed by the sync: auto-created POS- items and name matches.") },
	{ name: "No BOM", help: __("Mapped to an item that has no default BOM yet (D9).") },
	{ name: "Ignored", help: __("Left out of Branch Sales on purpose.") },
	{ name: "All", help: __("Every Lithos name.") },
];

const LITHOS_MAP_COLOURS = {
	Unmapped: "red",
	"Matched by barcode": "blue",
	"Matched by name": "orange",
	"Auto-created": "orange",
	Manual: "green",
	Ignored: "gray",
};

class LithosItemWorkbench {
	constructor(page) {
		this.page = page;
		this.view = "Unmapped";
		this.selected = new Set();
		this.make_filters();
		this.make_actions();
		this.$body = $(`<div class="lithos-workbench"></div>`).appendTo(page.main);
		this.bind_events();
	}

	make_filters() {
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
			search: this.page.add_field({
				fieldname: "search",
				label: __("Search Lithos name or item"),
				fieldtype: "Data",
				change,
			}),
		};
	}

	apply_route_options() {
		if (!frappe.route_options) return;
		this.applying = true;
		const { view, ...filters } = frappe.route_options;
		if (view) this.view = view;
		for (const [key, value] of Object.entries(filters)) {
			this.filters[key] && this.filters[key].set_value(value);
		}
		frappe.route_options = null;
		this.applying = false;
	}

	make_actions() {
		this.page.set_primary_action(__("Re-post Held Days"), () => this.repost_held(), "refresh");
		this.page.add_inner_button(__("Map Selected"), () => this.bulk_map());
		this.page.add_inner_button(__("Ignore Selected"), () => this.bulk_ignore());
		this.page.add_menu_item(__("Lithos Sync Console"), () => frappe.set_route("lithos-sync-console"));
		this.page.add_menu_item(__("Lithos Item Map List"), () =>
			frappe.set_route("List", "Lithos Item Map"),
		);
		this.page.add_menu_item(__("LithosPOS Settings"), () =>
			frappe.set_route("Form", "LithosPOS Settings"),
		);
	}

	bind_events() {
		this.$body.on("click", ".lithos-view", (e) => {
			this.view = $(e.currentTarget).attr("data-view");
			this.selected.clear();
			this.refresh();
		});
		this.$body.on("click", ".lithos-fix", (e) => this.open_row($(e.currentTarget).attr("data-name")));
		this.$body.on("change", ".lithos-select", (e) => {
			const name = $(e.currentTarget).attr("data-name");
			e.currentTarget.checked ? this.selected.add(name) : this.selected.delete(name);
		});
		this.$body.on("change", ".lithos-select-all", (e) => {
			const checked = e.currentTarget.checked;
			this.$body.find(".lithos-select").each((_i, box) => {
				box.checked = checked;
				checked ? this.selected.add(box.dataset.name) : this.selected.delete(box.dataset.name);
			});
		});
	}

	values() {
		return {
			company: this.filters.company.get_value() || null,
			store: this.filters.store.get_value() || null,
			search: this.filters.search.get_value() || null,
		};
	}

	refresh() {
		return frappe
			.call({ method: LITHOS_WORKBENCH_API + "get_queue", args: { view: this.view, ...this.values() } })
			.then((r) => {
				this.data = r.message;
				const visible = new Set(this.data.rows.map((row) => row.name));
				this.selected = new Set([...this.selected].filter((name) => visible.has(name)));
				this.render();
			});
	}

	render() {
		const { rows, counts, total, held_days, sales_days } = this.data;
		const tabs = LITHOS_VIEWS.map(
			(view) => `<button class="btn btn-sm lithos-view ${
				view.name === this.view ? "btn-primary" : "btn-default"
			}"
				data-view="${view.name}" title="${view.help}">${__(view.name)}
				<span class="lithos-count">${counts[view.name] || 0}</span></button>`,
		).join("");
		const help = LITHOS_VIEWS.find((view) => view.name === this.view).help;
		const held = held_days
			? `<div class="lithos-held-note">${__(
					"{0} held day(s) are waiting. Map their items, then use Re-post Held Days.",
					[held_days],
			  )}</div>`
			: "";

		this.$body.html(`
			<div class="lithos-views">${tabs}</div>
			<p class="text-muted small">${help} ${
			total > rows.length ? __("Showing the first {0} of {1}.", [rows.length, total]) : ""
		}</p>
			${held}
			${
				rows.length
					? this.table_html(rows, sales_days)
					: `<div class="lithos-empty text-muted">${__("Nothing here.")}</div>`
			}
		`);
	}

	table_html(rows, sales_days) {
		const esc = frappe.utils.escape_html;
		const body = rows
			.map((row) => {
				const item = row.item_code
					? `<a href="/app/item/${encodeURIComponent(row.item_code)}">${esc(row.item_code)}</a>
						<div class="text-muted small">${esc(row.item_name || "")}${
							row.item_disabled ? " · " + __("disabled") : ""
					  }</div>`
					: `<span class="text-muted">${__("None")}</span>`;
				const bom = !row.item_code
					? ""
					: row.default_bom
					? `<a href="/app/bom/${encodeURIComponent(row.default_bom)}">${esc(row.default_bom)}</a>`
					: `<span class="indicator-pill gray">${__("No BOM")}</span>`;
				const sold = row.qty
					? `${format_number(row.qty, null, 0)} · ${format_currency(row.net, "AED", 0)}
						<div class="text-muted small">${__("{0} day(s)", [row.days])}</div>`
					: `<span class="text-muted">–</span>`;
				const held = row.held_days
					? `<span class="indicator-pill yellow">${__("{0} day(s)", [row.held_days])}</span>
						<div class="text-muted small">${format_currency(row.held_net, "AED", 0)}</div>`
					: "";
				return `<tr>
					<td><input type="checkbox" class="lithos-select" data-name="${row.name}"
						${this.selected.has(row.name) ? "checked" : ""}></td>
					<td><div>${esc(row.lithos_item_name)}</div>
						<div class="text-muted small">${row.barcode ? __("Barcode {0}", [esc(row.barcode)]) : ""}</div></td>
					<td>${row.store ? esc(row.store) : `<span class="text-muted">${__("All stores")}</span>`}
						<div class="text-muted small">${row.store ? "" : esc(row.stores.join(", "))}</div></td>
					<td><span class="indicator-pill ${LITHOS_MAP_COLOURS[row.status] || "gray"}">${__(row.status)}</span></td>
					<td>${item}</td>
					<td>${bom}</td>
					<td class="text-right">${sold}</td>
					<td>${held}</td>
					<td>${row.last_sold ? frappe.datetime.str_to_user(row.last_sold) : ""}</td>
					<td><button class="btn btn-xs btn-default lithos-fix" data-name="${row.name}">${__("Fix")}</button></td>
				</tr>`;
			})
			.join("");
		return `<div class="lithos-table-wrap"><table class="table table-sm lithos-table">
			<thead><tr>
				<th><input type="checkbox" class="lithos-select-all"></th>
				<th>${__("Lithos Name")}</th><th>${__("Scope")}</th><th>${__("Status")}</th>
				<th>${__("ERPNext Item")}</th><th>${__("BOM")}</th>
				<th class="text-right">${__("Sold, last {0} days", [sales_days])}</th>
				<th>${__("Held")}</th><th>${__("Last Sold")}</th><th></th>
			</tr></thead>
			<tbody>${body}</tbody>
		</table></div>`;
	}

	row(name) {
		return this.data.rows.find((row) => row.name === name);
	}

	open_row(name) {
		frappe.call({ method: LITHOS_WORKBENCH_API + "get_suggestions", args: { name } }).then((r) => {
			this.fix_dialog(this.row(name), r.message.suggestions);
		});
	}

	fix_dialog(row, suggestions) {
		const esc = frappe.utils.escape_html;
		const store = this.values().store;
		const dialog = new frappe.ui.Dialog({
			title: row.lithos_item_name,
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "info" },
				{ fieldtype: "Section Break", label: __("Suggestions") },
				{ fieldtype: "HTML", fieldname: "suggestions" },
				{ fieldtype: "Section Break", label: __("Map to an item") },
				{
					fieldname: "item_code",
					fieldtype: "Link",
					label: __("ERPNext Item"),
					options: "Item",
					default: row.item_code,
					get_query: () => ({ filters: { disabled: 0, is_sales_item: 1 } }),
				},
				{
					fieldname: "store_only",
					fieldtype: "Check",
					label: __("Only for store {0}", [store]),
					description: __("Leaves the all-stores mapping as it is."),
					hidden: !(store && !row.store),
				},
			],
			primary_action_label: __("Map"),
			primary_action: (values) => {
				if (!values.item_code) {
					frappe.msgprint(__("Choose an item, or use Create Item."));
					return;
				}
				dialog.hide();
				this.map([row.name], values.item_code, values.store_only ? store : null);
			},
			secondary_action_label: __("Ignore"),
			secondary_action: () => {
				dialog.hide();
				this.ignore([row.name]);
			},
		});

		const scope = row.store ? __("store {0}", [esc(row.store)]) : __("all stores");
		dialog.fields_dict.info.$wrapper.html(`
			<div class="lithos-fix-info">
				<span class="indicator-pill ${LITHOS_MAP_COLOURS[row.status] || "gray"}">${__(row.status)}</span>
				${__("Applies to {0}.", [scope])}
				${row.barcode ? __("Lithos barcode {0}.", [esc(row.barcode)]) : ""}
				${row.item_code ? __("Now mapped to {0}.", [esc(row.item_code + " " + (row.item_name || ""))]) : ""}
				${row.held_days ? __("Holds {0} day(s).", [row.held_days]) : ""}
			</div>`);

		const $suggestions = dialog.fields_dict.suggestions.$wrapper;
		$suggestions.html(
			suggestions.length
				? `<table class="table table-sm">${suggestions
						.map(
							(s) => `<tr>
								<td><a href="/app/item/${encodeURIComponent(s.item_code)}" target="_blank">${esc(s.item_code)}</a>
									<div class="text-muted small">${esc(s.item_name)}</div></td>
								<td class="small">${esc(s.reason)}${s.is_pos_item ? " · " + __("auto-created") : ""}</td>
								<td class="small">${s.default_bom ? __("Has BOM") : `<span class="text-muted">${__("No BOM")}</span>`}</td>
								<td class="text-right"><button class="btn btn-xs btn-default" data-item="${esc(s.item_code)}">${__(
								"Map",
							)}</button></td>
							</tr>`,
						)
						.join("")}</table>`
				: `<p class="text-muted small">${__(
						"No barcode or name match. Pick an item below, or create one.",
				  )}</p>`,
		);
		$suggestions.on("click", "button[data-item]", (e) => {
			dialog.hide();
			this.map(
				[row.name],
				$(e.currentTarget).attr("data-item"),
				dialog.get_value("store_only") ? store : null,
			);
		});

		dialog.add_custom_action(__("Create Item"), () => {
			dialog.hide();
			this.create_item(row, dialog.get_value("store_only") ? store : null);
		});
		if (row.item_code) {
			dialog.add_custom_action(row.default_bom ? __("Open BOM") : __("New BOM"), () => {
				dialog.hide();
				row.default_bom
					? frappe.set_route("Form", "BOM", row.default_bom)
					: frappe.new_doc("BOM", { item: row.item_code, company: this.data.bom_company });
			});
			dialog.add_custom_action(__("Merge Item"), () => {
				dialog.hide();
				this.merge(row);
			});
		}
		dialog.show();
	}

	create_item(row, store) {
		const doc = frappe.model.get_new_doc("Item");
		Object.assign(doc, { item_name: row.lithos_item_name, is_stock_item: 0, is_sales_item: 1 });
		frappe.ui.form.make_quick_entry("Item", (item) => this.map([row.name], item.name, store), null, doc);
	}

	merge(row) {
		frappe.prompt(
			[
				{
					fieldname: "target",
					fieldtype: "Link",
					label: __("Merge {0} into", [row.item_code]),
					options: "Item",
					reqd: 1,
					get_query: () => ({ filters: { disabled: 0, name: ["!=", row.item_code] } }),
					description: __(
						"Every Lithos name mapped to {0} moves to this item. An auto-created POS- item is then disabled. Posted days keep {0} until they are re-posted.",
						[row.item_code],
					),
				},
			],
			(values) =>
				frappe
					.call({
						method: LITHOS_WORKBENCH_API + "merge_item",
						args: { source: row.item_code, target: values.target },
						freeze: true,
					})
					.then((r) => {
						const result = r.message;
						frappe.show_alert({
							message:
								__("{0} Lithos name(s) moved to {1}.", [result.maps, values.target]) +
								(result.disabled ? " " + __("{0} is disabled.", [row.item_code]) : ""),
							indicator: "green",
						});
						this.refresh();
					}),
			__("Merge Item"),
			__("Merge"),
		);
	}

	map(names, item_code, store) {
		return frappe
			.call({
				method: LITHOS_WORKBENCH_API + "map_rows",
				args: { names, item_code, store },
				freeze: true,
			})
			.then(() => {
				frappe.show_alert({
					message: __("Mapped {0} Lithos name(s) to {1}.", [names.length, item_code]),
					indicator: "green",
				});
				this.selected.clear();
				this.refresh();
			});
	}

	ignore(names) {
		return frappe
			.call({ method: LITHOS_WORKBENCH_API + "ignore_rows", args: { names }, freeze: true })
			.then(() => {
				frappe.show_alert({
					message: __("Ignored {0} Lithos name(s).", [names.length]),
					indicator: "gray",
				});
				this.selected.clear();
				this.refresh();
			});
	}

	bulk_map() {
		if (!this.selected.size) return frappe.msgprint(__("Tick the Lithos names to map first."));
		const store = this.values().store;
		const names = [...this.selected];
		frappe.prompt(
			[
				{
					fieldname: "item_code",
					fieldtype: "Link",
					label: __("ERPNext Item"),
					options: "Item",
					reqd: 1,
					get_query: () => ({ filters: { disabled: 0, is_sales_item: 1 } }),
				},
				{
					fieldname: "store_only",
					fieldtype: "Check",
					label: __("Only for store {0}", [store]),
					hidden: !store,
				},
			],
			(values) => this.map(names, values.item_code, values.store_only ? store : null),
			__("Map {0} Lithos name(s)", [names.length]),
			__("Map"),
		);
	}

	bulk_ignore() {
		if (!this.selected.size) return frappe.msgprint(__("Tick the Lithos names to ignore first."));
		const names = [...this.selected];
		frappe.confirm(
			__("Leave {0} Lithos name(s) out of Branch Sales? Their value will show as variance.", [
				names.length,
			]),
			() => this.ignore(names),
		);
	}

	repost_held() {
		const { company, store } = this.values();
		frappe.confirm(__("Re-post every held day in this view, using the mappings as they are now?"), () =>
			frappe
				.call({
					method: "al_qubaisi_modifications.lithos_console.repost_held",
					args: { company, store },
					freeze: true,
				})
				.then((r) =>
					frappe.show_alert({
						message: __("Queued {0}. Follow it in the Lithos Sync Console.", [
							`<a href="/app/lithos-sync-run/${r.message}">${r.message}</a>`,
						]),
						indicator: "blue",
					}),
				),
		);
	}
}
