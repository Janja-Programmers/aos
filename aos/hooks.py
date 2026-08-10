app_name = "aos"
app_title = "AOS"
app_publisher = "Africa Online Stores"
app_description = "A multi-vendor marketplace platform enabling users to buy, sell, go live, and communicate via chat and in-app calls across multiple countries."
app_email = "info@africaonlinestores.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "aos",
# 		"logo": "/assets/aos/logo.png",
# 		"title": "AOS",
# 		"route": "/aos",
# 		"has_permission": "aos.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/aos/css/aos.css"
# app_include_js = "/assets/aos/js/aos.js"

# include js, css files in header of web template
# web_include_css = "/assets/aos/css/aos.css"
# web_include_js = "/assets/aos/js/aos.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "aos/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "aos/public/icons.svg"

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

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "aos.utils.jinja_methods",
# 	"filters": "aos.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "aos.install.before_install"
# after_install = "aos.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "aos.uninstall.before_uninstall"
# after_uninstall = "aos.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "aos.utils.before_app_install"
# after_app_install = "aos.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "aos.utils.before_app_uninstall"
# after_app_uninstall = "aos.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "aos.notifications.get_notification_config"

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

doc_events = {
	"Country": {
		"after_insert": "aos.services.localization_service.localization_master_changed",
		"on_update": "aos.services.localization_service.localization_master_changed",
		"on_trash": "aos.services.localization_service.localization_master_changed",
	},
	"Currency": {
		"after_insert": "aos.services.localization_service.localization_master_changed",
		"on_update": "aos.services.localization_service.localization_master_changed",
		"on_trash": "aos.services.localization_service.localization_master_changed",
	},
	"Language": {
		"after_insert": "aos.services.localization_service.localization_master_changed",
		"on_update": "aos.services.localization_service.localization_master_changed",
		"on_trash": "aos.services.localization_service.localization_master_changed",
	},
}


# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/1 * * * *": [
			"aos.tasks.calls.handle_missed_calls",
			"aos.tasks.outbox.publish_transactional_outbox",
		],
		"*/5 * * * *": [
			"aos.tasks.shorts.recover_pending_audio_mixes",
			"aos.tasks.live.reconcile_live_state",
			"aos.tasks.calls.reconcile_call_rooms",
			"aos.tasks.calls.reconcile_active_call_state",
		],
	},
	"hourly": [
		"aos.tasks.ads.expire_ads",
		"aos.tasks.shorts.update_short_ranking",
		"aos.tasks.shorts.aggregate_short_metrics",
		"aos.tasks.sellers.refresh_recent_seller_response_metrics",
		"aos.tasks.media.cleanup_media_objects",
		"aos.tasks.search_ranking.refresh_search_indexes",
	],
	"daily": [
		"aos.tasks.fx.update_exchange_rates",
		"aos.tasks.service_hardening.cleanup_external_service_jobs",
		"aos.tasks.shorts.maintain_short_integrity",
		"aos.tasks.live.cleanup_live_webhook_events",
	],
}

# Testing
# -------

# before_tests = "aos.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "aos.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "aos.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "aos.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
before_request = ["aos.utils.metrics.before_request"]
after_request = ["aos.utils.metrics.after_request"]
on_error = ["aos.utils.metrics.on_error"]

# Job Events
# ----------
# before_job = ["aos.utils.before_job"]
# after_job = ["aos.utils.after_job"]

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
# 	"aos.auth.validate"
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
	{"dt": "AOS Category", "filters": [["is_active", "=", 1]]},
	{"dt": "AOS Ad Attribute", "filters": [["is_active", "=", 1]]},
	{"dt": "AOS Report Reason", "filters": [["is_active", "=", 1]]},
]
