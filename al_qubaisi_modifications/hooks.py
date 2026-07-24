app_name = "al_qubaisi_modifications"
app_title = "Al-Qubaisi Modifications"
app_publisher = "Acube Innoivations Pvt Limited"
app_description = "Modifications in Al-Qubaisi"
app_email = "admin@acube.co"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "al_qubaisi_modifications",
# 		"logo": "/assets/al_qubaisi_modifications/logo.png",
# 		"title": "Al-Qubaisi Modifications",
# 		"route": "/al_qubaisi_modifications",
# 		"has_permission": "al_qubaisi_modifications.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/al_qubaisi_modifications/css/al_qubaisi_modifications.css"
# app_include_js = "/assets/al_qubaisi_modifications/js/al_qubaisi_modifications.js"

# include js, css files in header of web template
# web_include_css = "/assets/al_qubaisi_modifications/css/al_qubaisi_modifications.css"
# web_include_js = "/assets/al_qubaisi_modifications/js/al_qubaisi_modifications.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "al_qubaisi_modifications/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Sales Invoice": "public/js/sales_invoice.js",
	"Material Request": "public/js/material_request.js",
	"Sales Order": "public/js/sales_order.js",
}
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "al_qubaisi_modifications/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "al_qubaisi_modifications.utils.jinja_methods",
# 	"filters": "al_qubaisi_modifications.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "al_qubaisi_modifications.install.before_install"
# after_install = "al_qubaisi_modifications.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "al_qubaisi_modifications.uninstall.before_uninstall"
# after_uninstall = "al_qubaisi_modifications.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "al_qubaisi_modifications.utils.before_app_install"
# after_app_install = "al_qubaisi_modifications.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "al_qubaisi_modifications.utils.before_app_uninstall"
# after_app_uninstall = "al_qubaisi_modifications.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "al_qubaisi_modifications.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"BOM": "al_qubaisi_modifications.overrides.bom.CustomBOM",
	"Material Request": "al_qubaisi_modifications.overrides.material_request.CustomMaterialRequest",
}

# Accounting Dimensions
# ---------------------
# Register the Material Request header so the Branch accounting dimension is
# managed on it like on other transaction doctypes. The child (Material Request
# Item) is already registered by ERPNext.
accounting_dimension_doctypes = ["Material Request"]

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"al_qubaisi_modifications.al_qubaisi_modifications.doctype.employee_documents.employee_documents.update_document_statuses",
		"al_qubaisi_modifications.al_qubaisi_modifications.doctype.employee_documents.employee_documents.send_expiry_digest",
	],
	"cron": {
		# 02:00 Asia/Dubai - late enough for tills trading past midnight to have closed out.
		"0 2 * * *": [
			"al_qubaisi_modifications.lithos.sync_yesterday",
		],
	},
}

# Testing
# -------

# before_tests = "al_qubaisi_modifications.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "al_qubaisi_modifications.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "al_qubaisi_modifications.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["al_qubaisi_modifications.utils.before_request"]
# after_request = ["al_qubaisi_modifications.utils.after_request"]

# Job Events
# ----------
# before_job = ["al_qubaisi_modifications.utils.before_job"]
# after_job = ["al_qubaisi_modifications.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"al_qubaisi_modifications.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

fixtures = [
    {
        "dt": "Accounting Dimension",
        "filters": [
            ["document_type", "=", "Branch"]
        ]
    }
]