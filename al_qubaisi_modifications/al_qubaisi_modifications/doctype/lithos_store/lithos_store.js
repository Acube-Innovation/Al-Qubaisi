// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Lithos Store", {
	setup(frm) {
		const by_company = (extra) => () => ({
			filters: Object.assign({ company: frm.doc.company }, extra),
		});
		frm.set_query("warehouse", by_company({ is_group: 0 }));
		frm.set_query("cost_center", by_company({ is_group: 0 }));
		frm.set_query("consumption_account", by_company({ is_group: 0, root_type: "Expense" }));
		frm.set_query("taxes_and_charges", by_company());
	},

	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Sync Console"), () =>
			frappe.set_route("lithos-sync-console", { store: frm.doc.store_id })
		);

		frm.add_custom_button(__("Ledger Readiness"), () => lithos_store_readiness(frm), __("Ledger"));
		if (frm.doc.ledger_from_date) {
			frm.add_custom_button(
				__("Post Ledger"),
				() =>
					frappe
						.call({
							method: "al_qubaisi_modifications.lithos_console.post_ledger",
							args: { store: frm.doc.store_id },
							freeze: true,
						})
						.then((r) =>
							lithos_store_queued(
								r.message,
								__("Days without a Sales Invoice are posted; pending consumption is retried.")
							)
						),
				__("Ledger")
			);
		}

		frm.add_custom_button(__("Sync Date Range"), () => {
			frappe.prompt(
				[
					{ fieldname: "from_date", fieldtype: "Date", label: __("From Date"), reqd: 1 },
					{
						fieldname: "to_date",
						fieldtype: "Date",
						label: __("To Date"),
						reqd: 1,
						default: frappe.datetime.add_days(frappe.datetime.get_today(), -1),
					},
				],
				(values) => {
					frappe.call({
						method: "al_qubaisi_modifications.lithos.enqueue_sync_range",
						args: Object.assign({ store_id: frm.doc.store_id }, values),
						callback: (r) =>
							lithos_store_queued(
								r.message,
								__("Days already posted are re-checked, not posted again.")
							),
					});
				},
				__("Pull from LithosPOS"),
				__("Queue")
			);
		});

		if (frm.doc.move_to_company) {
			frm.add_custom_button(__("Re-post into {0}", [frm.doc.move_to_company]), () =>
				lithos_store_confirm_move(frm)
			);
			frm.dashboard.set_headline_alert(
				__("This store's history is to be re-posted into {0} (D3). Until then it posts into {1}.", [
					frm.doc.move_to_company.bold(),
					frm.doc.company.bold(),
				]),
				"orange"
			);
		}
	},
});

function lithos_store_queued(run, note) {
	frappe.show_alert({
		message:
			__("Queued {0}.", [`<a href="/app/lithos-sync-run/${run}">${run}</a>`]) +
			(note ? " " + note : ""),
		indicator: "blue",
	});
}

function lithos_store_confirm_move(frm) {
	frappe
		.call({
			method: "al_qubaisi_modifications.lithos_repost.preview_move",
			args: { store_id: frm.doc.store_id },
			freeze: true,
		})
		.then((r) => {
			const p = r.message;
			const esc = frappe.utils.escape_html;
			const changes = p.item_changes.length
				? `<p>${__("{0} line(s) move to another item through the Lithos Item Map:", [
						p.lines_changing_item,
				  ])}</p><ul>${p.item_changes
						.map((c) => `<li>${esc(c.from)} → ${esc(c.to)} (${c.lines})</li>`)
						.join("")}</ul>`
				: `<p>${__("Every line keeps its item.")}</p>`;
			const warnings = p.warnings.length
				? `<div class="alert alert-warning">${p.warnings.map(esc).join("<br>")}</div>`
				: "";
			const defaults = Object.entries(p.defaults)
				.map(
					([key, value]) =>
						`<li>${frappe.meta.get_label("Lithos Store", key)}: ${esc(value || "–")}</li>`
				)
				.join("");

			const dialog = new frappe.ui.Dialog({
				title: __("Re-post store {0} into {1}", [p.store, p.to_company]),
				fields: [{ fieldtype: "HTML", fieldname: "summary" }],
				primary_action_label: __("Re-post {0} Day(s)", [p.days]),
				primary_action: () => {
					dialog.hide();
					frappe
						.call({
							method: "al_qubaisi_modifications.lithos_repost.enqueue_move",
							args: { store_id: frm.doc.store_id },
							freeze: true,
						})
						.then((res) =>
							lithos_store_queued(
								res.message,
								__("Each day is cancelled and amended into the new company.")
							)
						);
				},
			});
			dialog.fields_dict.summary.$wrapper.html(`
				${warnings}
				<p>${__("{0} day(s) from {1} to {2} ({3} submitted, {4} draft), net {5}, now in {6}.", [
					p.days,
					p.first ? frappe.datetime.str_to_user(p.first) : "–",
					p.last ? frappe.datetime.str_to_user(p.last) : "–",
					p.submitted,
					p.drafts,
					format_currency(p.net, "AED"),
					esc(p.from_companies.join(", ") || p.store_company),
				])}</p>
				<p>${__(
					"Each submitted day is cancelled and amended in {0}. Totals must match to the fil, or the day is left as it was. The store takes these defaults:",
					[esc(p.to_company)]
				)}</p>
				<ul>${defaults}</ul>
				${changes}`);
			if (p.blocked) dialog.get_primary_btn().prop("disabled", true);
			dialog.show();
		});
}

function lithos_store_readiness(frm) {
	frappe
		.call({
			method: "al_qubaisi_modifications.lithos_ledger.ledger_readiness",
			args: { store_id: frm.doc.store_id },
			freeze: true,
		})
		.then((r) => {
			const result = r.message;
			const esc = frappe.utils.escape_html;
			const problems = result.problems.length
				? `<p>${__("Before a Ledger From Date can be set:")}</p><ul>${result.problems
						.map((p) => `<li>${esc(p)}</li>`)
						.join("")}</ul>`
				: `<p class="text-success">${__("The company and store are ready for the ledger.")}</p>`;
			const items = result.without_bom.length
				? `<p>${__(
						"{0} of {1} items sold in the last 30 days have no BOM and are not stock items, so nothing would be issued for them (D9):",
						[result.without_bom.length, result.sold_items]
				  )}</p><table class="table table-sm"><thead><tr><th>${__(
						"Item"
				  )}</th><th class="text-right">${__("Qty")}</th><th class="text-right">${__(
						"Net"
				  )}</th></tr></thead><tbody>${result.without_bom
						.map(
							(i) =>
								`<tr><td><a href="/app/item/${encodeURIComponent(i.item_code)}">${esc(
									i.item_code
								)}</a> ${esc(i.item_name || "")}</td><td class="text-right">${format_number(
									i.qty,
									null,
									0
								)}</td><td class="text-right" style="white-space: nowrap">${format_currency(
									i.net,
									"AED"
								)}</td></tr>`
						)
						.join("")}</tbody></table>`
				: `<p>${__("Every item sold in the last 30 days has a BOM or is issued directly.")}</p>`;
			frappe.msgprint({
				title: __("Ledger readiness: store {0}", [frm.doc.store_id]),
				message: problems + items,
				wide: true,
				indicator: result.problems.length ? "orange" : "green",
			});
		});
}
