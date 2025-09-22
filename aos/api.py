import frappe
from frappe import _
from frappe.exceptions import DoesNotExistError


@frappe.whitelist(allow_guest=True)
def get_product_detail(item_code):
    try:
        website_item = frappe.get_doc("Website Item", {"item_code": item_code})
        item = frappe.get_doc("Item", item_code)

        # Slideshow images
        images = []
        if website_item.slideshow:
            try:
                slideshow = frappe.get_doc("Website Slideshow", website_item.slideshow)
                if hasattr(slideshow, "slides"):
                    images = [slide.image for slide in slideshow.slides if slide.image]
            except Exception as e:
                frappe.logger().error(f"Error loading slideshow for {item_code}: {e}")

        if not images and website_item.website_image:
            images = [website_item.website_image]

        # Price
        price = frappe.db.get_value("Item Price", {"item_code": item_code}, "price_list_rate")

        # Stock
        stock_info = get_item_stock(item_code=item_code, warehouse=website_item.website_warehouse)
        in_stock = stock_info.get("in_stock", False)

        # Specs
        specifications = []
        for spec in website_item.website_specifications:
            specifications.append({
                "label": spec.label,
                "value": frappe.utils.strip_html(spec.description)
            })

        # Reviews
        reviews = frappe.get_all(
            "Item Review",
            filters={"item": item_code, "docstatus": 0},
            fields=["review_title", "published_on", "customer", "rating", "comment"],
            order_by="published_on desc",
            limit_page_length=5  # Optional: only fetch recent 5 reviews
        )

        return {
		    "item_code": item_code,
            "web_item_id": website_item.name,
		    "name": website_item.web_item_name or item.item_name,
		    "owner": item.custom_vendor,
		    "category": item.item_group,
		    "price": price,
		    "in_stock": in_stock,
		    "short_description": website_item.short_description,
		    "long_description": website_item.web_long_description,
		    "images": images,
		    "demo_video": website_item.custom_demo_video,
		    "specifications": specifications,
		    "reviews": reviews,
		}

    except DoesNotExistError:
        frappe.local.response.http_status_code = 404
        return {
            "error": "Product not found",
            "item_code": item_code
        }

    except Exception as e:
        frappe.local.response.http_status_code = 500
        return {
            "error": "An unexpected error occurred",
            "message": str(e)
        }


@frappe.whitelist(allow_guest=True)
def get_item_stock(item_code, warehouse=None):
    if not warehouse:
        warehouse = frappe.db.get_value("Item Default", {"parent": item_code}, "default_warehouse")

    if not warehouse:
        return {"error": "No default warehouse found for this item."}

    actual_qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty") or 0

    return {
        "in_stock": actual_qty > 0,
        "actual_qty": actual_qty
    }


@frappe.whitelist(allow_guest=True)
def get_slider(name):
    doc = frappe.get_doc("Website Slideshow", name)
    return {
        "name": doc.name,
        "items": [
            {
                "image": i.image
            } for i in doc.slideshow_items
        ]
    }


@frappe.whitelist()
def delete_account():
    try:
        user = frappe.session.user
        if user == "Guest":
            frappe.throw(_("You must be logged in to delete your account."))

        # Ensure uniqueness for email
        random_suffix = frappe.generate_hash(length=6)

        frappe.db.set_value("User", user, {
            "enabled": 0,
            "email": f"deleted_{user}_{random_suffix}",
            "first_name": "Deleted",
            "last_name": "User"
        })
        frappe.db.commit()

        # Logout after deletion
        frappe.local.login_manager.logout()

        return {
            "status": "success",
            "message": _("Your account has been deleted.")
        }

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Delete Account Error")
        return {
            "status": "error",
            "message": _("Something went wrong while deleting your account. Please try again later.")
        }
