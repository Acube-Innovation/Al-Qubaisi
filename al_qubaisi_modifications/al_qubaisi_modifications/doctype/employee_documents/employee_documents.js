// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Employee Documents", {
	refresh(frm) {
		render_document_cards(frm);
	},
});

frappe.ui.form.on("Employee Documents Detail", {
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
	const field = frm.get_field("html_cuvm");
	if (!field) return;

	inject_card_styles();

	const rows = (frm.doc.documents || []).filter((r) => r.document_type);
	if (!rows.length) {
		field.$wrapper.html(
			`<div class="emp-doc-empty text-muted">${__("No documents added yet")}</div>`
		);
		return;
	}

	const cards = rows.map((row) => build_card(frm, row)).join("");
	field.$wrapper.html(`<div class="emp-doc-cards">${cards}</div>`);

	field.$wrapper.find(".emp-doc-renew").on("click", function () {
		const row_name = $(this).closest(".emp-doc-card").data("row");
		frappe.model.set_value("Employee Documents Detail", row_name, "status", "Renewal In Progress");
	});
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
		expiry_line = `<div class="emp-doc-line">${label}: ${esc(
			frappe.datetime.str_to_user(row.expiry_date)
		)}</div>`;
	}

	// bottom-right: renew button when expired, otherwise days left
	let footer_right;
	if (row.status === "Expired") {
		footer_right = `<button type="button" class="btn btn-xs btn-default emp-doc-renew">${__(
			"Renew Now"
		)}</button>`;
	} else if (days_left !== null && days_left >= 0) {
		footer_right = `<span class="emp-doc-days">${days_left} ${__("Days Left")}</span>`;
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
		<div class="emp-doc-card" data-row="${esc(row.name || "")}"
			style="background: ${style.bg}; border-color: ${style.border};">
			<div class="emp-doc-head">
				<span class="emp-doc-icon" style="background: ${style.accent};">
					${esc((row.document_type || "?").charAt(0).toUpperCase())}
				</span>
				<span class="emp-doc-title">${esc(row.document_type)}</span>
				<span class="emp-doc-chip" style="border-color: ${style.border};">${chip}</span>
			</div>
			<div class="emp-doc-body">
				<div class="emp-doc-name">${esc(frm.doc.employee_name || "")}</div>
				${subtitle ? `<div class="emp-doc-line emp-doc-ref">${subtitle} ${attachment}</div>` : attachment}
				${expiry_line}
			</div>
			<div class="emp-doc-foot">
				<span class="emp-doc-status" style="color: ${style.accent};">
					<span class="emp-doc-dot" style="background: ${style.accent};"></span>${__(row.status || "Active")}
				</span>
				${footer_right}
			</div>
		</div>`;
}

function inject_card_styles() {
	if (document.getElementById("emp-doc-card-styles")) return;
	const css = `
		.emp-doc-cards {
			display: grid;
			grid-template-columns: repeat(3, minmax(0, 1fr));
			gap: 12px;
			margin: 8px 0;
		}
		@media (max-width: 1100px) { .emp-doc-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
		@media (max-width: 700px) { .emp-doc-cards { grid-template-columns: 1fr; } }
		.emp-doc-card {
			border: 1.5px solid;
			border-radius: 10px;
			padding: 12px 14px;
			display: flex;
			flex-direction: column;
			gap: 8px;
		}
		.emp-doc-head { display: flex; align-items: center; gap: 8px; }
		.emp-doc-icon {
			width: 22px; height: 22px; border-radius: 6px;
			color: #fff; font-weight: 700; font-size: 12px;
			display: inline-flex; align-items: center; justify-content: center;
			flex-shrink: 0;
		}
		.emp-doc-title { font-weight: 600; font-size: 14px; color: var(--text-color); }
		.emp-doc-chip {
			margin-left: auto;
			font-size: 11px;
			border: 1px solid;
			border-radius: 999px;
			padding: 1px 8px;
			color: var(--text-muted);
			background: var(--card-bg);
			white-space: nowrap;
		}
		.emp-doc-body { display: flex; flex-direction: column; gap: 2px; }
		.emp-doc-name { font-weight: 600; font-size: 13px; color: var(--text-color); }
		.emp-doc-line { font-size: 12px; color: var(--text-muted); }
		.emp-doc-ref { font-family: var(--font-family-monospace, monospace); }
		.emp-doc-foot {
			display: flex; align-items: center; justify-content: space-between;
			margin-top: auto; padding-top: 4px;
		}
		.emp-doc-status { font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; gap: 5px; }
		.emp-doc-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
		.emp-doc-days { font-size: 12px; font-weight: 600; color: var(--text-color); }
		.emp-doc-empty { padding: 12px 0; }
	`;
	const style = document.createElement("style");
	style.id = "emp-doc-card-styles";
	style.textContent = css;
	document.head.appendChild(style);
}
