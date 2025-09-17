import frappe
from frappe.utils import cint

from webshop.webshop.product_data_engine.filters import ProductFiltersBuilder

sitemap = 1


def get_context(context):
	# Add homepage as parent
	context.body_class = "product-page"
	context.parents = [{"name": frappe._("Home"), "route": "/"}]

	filter_engine = ProductFiltersBuilder()
	context.field_filters = filter_engine.get_field_filters()
	context.attribute_filters = filter_engine.get_attribute_filters()

	context.page_length = (
		cint(frappe.db.get_single_value("Webshop Settings", "products_per_page")) or 20
	)

    # Add slideshow
	settings = frappe.get_cached_doc("Webshop Settings")
	if settings.slideshow:
		context.slideshow = get_slideshow(settings.slideshow)

	context.no_cache = 1

def get_slideshow(slideshow):
	values = {"show_indicators": 1, "show_controls": 1, "rounded": 1, "slider_name": "Categories"}
	slideshow = frappe.get_cached_doc("Website Slideshow", slideshow)
	slides = slideshow.get({"doctype": "Website Slideshow Item"})
	for index, slide in enumerate(slides, start=1):
		values[f"slide_{index}_image"] = slide.image
		values[f"slide_{index}_title"] = slide.heading
		values[f"slide_{index}_subtitle"] = slide.description
		values[f"slide_{index}_theme"] = slide.get("theme") or "Light"
		values[f"slide_{index}_content_align"] = slide.get("content_align") or "Centre"
		values[f"slide_{index}_primary_action"] = slide.url

	return values
