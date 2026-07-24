// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Company Documents", {
	refresh(frm) {
		render_document_cards(frm);
	},
	company(frm) {
		render_document_cards(frm);
	},
});

frappe.ui.form.on("Company Documents Detail", {
	document_type(frm) {
		render_document_cards(frm);
	},
	reference_no(frm) {
		render_document_cards(frm);
	},
	issue_date(frm) {
		render_document_cards(frm);
	},
	expiry_date(frm) {
		render_document_cards(frm);
	},
	status(frm) {
		render_document_cards(frm);
	},
	attachments(frm) {
		render_document_cards(frm);
	},
	documents_add(frm) {
		render_document_cards(frm);
	},
	documents_remove(frm) {
		render_document_cards(frm);
	},
});

const CARD_STYLES = {
	green: { bg: "rgba(34, 197, 94, 0.08)", border: "rgba(34, 197, 94, 0.45)", accent: "#16a34a" },
	yellow: { bg: "rgba(234, 179, 8, 0.10)", border: "rgba(234, 179, 8, 0.55)", accent: "#b45309" },
	red: { bg: "rgba(239, 68, 68, 0.08)", border: "rgba(239, 68, 68, 0.45)", accent: "#dc2626" },
	blue: { bg: "rgba(59, 130, 246, 0.08)", border: "rgba(59, 130, 246, 0.45)", accent: "#2563eb" },
	gray: { bg: "rgba(148, 163, 184, 0.10)", border: "rgba(148, 163, 184, 0.45)", accent: "#64748b" },
};

const STATUS_COLOR = {
	Active: "green",
	"Expiring Soon": "yellow",
	Expired: "red",
	"Renewal In Progress": "blue",
	"Not Applicable": "gray",
};

function render_document_cards(frm) {
	const field = frm.get_field("html_cards");
	if (!field) return;

	inject_card_styles();

	const rows = (frm.doc.documents || []).filter((r) => r.document_type);

	// hide an expired card when a non-expired row of the same document type exists
	// (i.e. it has already been renewed)
	const active_types = new Set(
		rows.filter((r) => !is_row_expired(r)).map((r) => r.document_type)
	);
	const visible_rows = rows.filter(
		(r) => !(is_row_expired(r) && active_types.has(r.document_type))
	);

	if (!visible_rows.length) {
		field.$wrapper.html(
			`<div class="cmp-doc-empty text-muted">${__("No documents added yet")}</div>`
		);
		return;
	}

	const cards = visible_rows.map((row) => build_card(frm, row)).join("");
	field.$wrapper.html(`<div class="cmp-doc-cards">${cards}</div>`);

	field.$wrapper.find(".cmp-doc-renew").on("click", function () {
		const row_name = $(this).closest(".cmp-doc-card").data("row");
		const old_row = locals["Company Documents Detail"][row_name];
		if (!old_row) return;

		frm.add_child("documents", {
			document_type: old_row.document_type,
			issuing_authority: old_row.issuing_authority,
			issue_date: frappe.datetime.get_today(),
			status: "Active",
		});
		frm.refresh_field("documents");
		render_document_cards(frm);

		frappe.show_alert({
			message: __("Renewal row added for {0}. Enter the new Reference No and Expiry Date, then save.", [
				old_row.document_type,
			]),
			indicator: "green",
		});
	});
}

function is_row_expired(row) {
	if (row.status === "Expired") return true;
	if (!row.expiry_date) return false;
	return frappe.datetime.get_day_diff(row.expiry_date, frappe.datetime.get_today()) < 0;
}

function build_card(frm, row) {
	const esc = frappe.utils.escape_html;
	const today = frappe.datetime.get_today();
	const days_left = row.expiry_date ? frappe.datetime.get_day_diff(row.expiry_date, today) : null;

	const color = STATUS_COLOR[row.status] || "gray";
	const style = CARD_STYLES[color];

	// top-right chip
	let chip;
	if (!row.expiry_date) {
		chip = __("No Expiry");
	} else if (days_left < 0) {
		chip = __("Expired");
	} else if (days_left <= 60) {
		chip = __("<60 days left");
	} else {
		chip = __(">60 days left");
	}

	// expiry line
	let expiry_line = "";
	if (row.expiry_date) {
		const label = days_left < 0 ? __("Expired") : __("Expires");
		expiry_line = `<div class="cmp-doc-line">${label}: ${esc(
			frappe.datetime.str_to_user(row.expiry_date)
		)}</div>`;
	}

	// bottom-right: renew button when expired, otherwise days left
	let footer_right;
	if (row.status === "Expired") {
		footer_right = `<button type="button" class="btn btn-xs btn-default cmp-doc-renew">${__(
			"Renew Now"
		)}</button>`;
	} else if (days_left !== null && days_left >= 0) {
		footer_right = `<span class="cmp-doc-days">${days_left} ${__("Days Left")}</span>`;
	} else {
		footer_right = "";
	}

	const attachment = row.attachments
		? `<a href="${esc(row.attachments)}" target="_blank" rel="noopener" title="${__(
				"View Attachment"
		  )}">${frappe.utils.icon("attachment", "sm")}</a>`
		: "";

	const subtitle = [row.reference_no, row.issuing_authority].filter(Boolean).map(esc).join(" · ");

	return `
		<div class="cmp-doc-card" data-row="${esc(row.name || "")}"
			style="background: ${style.bg}; border-color: ${style.border};">
			<div class="cmp-doc-head">
				<span class="cmp-doc-icon" style="background: ${style.accent};">
					${esc((row.document_type || "?").charAt(0).toUpperCase())}
				</span>
				<span class="cmp-doc-title">${esc(row.document_type)}</span>
				<span class="cmp-doc-chip" style="border-color: ${style.border};">${chip}</span>
			</div>
			<div class="cmp-doc-body">
				<div class="cmp-doc-name">${esc(frm.doc.company || "")}</div>
				${subtitle ? `<div class="cmp-doc-line cmp-doc-ref">${subtitle} ${attachment}</div>` : attachment}
				${expiry_line}
			</div>
			<div class="cmp-doc-foot">
				<span class="cmp-doc-status" style="color: ${style.accent};">
					<span class="cmp-doc-dot" style="background: ${style.accent};"></span>${__(row.status || "Active")}
				</span>
				${footer_right}
			</div>
		</div>`;
}

function inject_card_styles() {
	if (document.getElementById("cmp-doc-card-styles")) return;
	const css = `
		.cmp-doc-cards {
			display: grid;
			grid-template-columns: repeat(3, minmax(0, 1fr));
			gap: 12px;
			margin: 8px 0;
		}
		@media (max-width: 1100px) { .cmp-doc-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
		@media (max-width: 700px) { .cmp-doc-cards { grid-template-columns: 1fr; } }
		.cmp-doc-card {
			border: 1.5px solid;
			border-radius: 10px;
			padding: 12px 14px;
			display: flex;
			flex-direction: column;
			gap: 8px;
		}
		.cmp-doc-head { display: flex; align-items: center; gap: 8px; }
		.cmp-doc-icon {
			width: 22px; height: 22px; border-radius: 6px;
			color: #fff; font-weight: 700; font-size: 12px;
			display: inline-flex; align-items: center; justify-content: center;
			flex-shrink: 0;
		}
		.cmp-doc-title { font-weight: 600; font-size: 14px; color: var(--text-color); }
		.cmp-doc-chip {
			margin-left: auto;
			font-size: 11px;
			border: 1px solid;
			border-radius: 999px;
			padding: 1px 8px;
			color: var(--text-muted);
			background: var(--card-bg);
			white-space: nowrap;
		}
		.cmp-doc-body { display: flex; flex-direction: column; gap: 2px; }
		.cmp-doc-name { font-weight: 600; font-size: 13px; color: var(--text-color); }
		.cmp-doc-line { font-size: 12px; color: var(--text-muted); }
		.cmp-doc-ref { font-family: var(--font-family-monospace, monospace); }
		.cmp-doc-foot {
			display: flex; align-items: center; justify-content: space-between;
			margin-top: auto; padding-top: 4px;
		}
		.cmp-doc-status { font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; gap: 5px; }
		.cmp-doc-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
		.cmp-doc-days { font-size: 12px; font-weight: 600; color: var(--text-color); }
		.cmp-doc-empty { padding: 12px 0; }
	`;
	const style = document.createElement("style");
	style.id = "cmp-doc-card-styles";
	style.textContent = css;
	document.head.appendChild(style);
}
