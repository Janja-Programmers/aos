from __future__ import annotations

import io
import os

import frappe
from PIL import Image
from rembg import remove

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import REMOVE_BG_LIMIT_PER_MINUTE_PER_USER


def remove_background_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:files:remove_bg:user:{current_user}",
        ttl_seconds=60,
        limit=REMOVE_BG_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    file_id = kwargs.get("file_id")

    if not file_id:
        return fail("File id is required.", code="VALIDATION_ERROR")

    file_doc = frappe.db.get_value(
        "File",
        file_id,
        ["name", "owner", "file_url", "file_name", "is_private"],
        as_dict=True,
    )

    if not file_doc:
        return fail("File not found.", code="NOT_FOUND")

    if file_doc.owner != current_user:
        return fail("You cannot edit this file.", code="PERMISSION_DENIED")

    try:
        filename = os.path.basename(file_doc.file_url)

        # Resolve real file path
        if file_doc.is_private:
            file_path = frappe.get_site_path("private", "files", filename)
        else:
            file_path = frappe.get_site_path("public", "files", filename)

        if not os.path.exists(file_path):
            return fail("File not found on disk.", code="FILE_MISSING")

        # Open image
        input_image = Image.open(file_path).convert("RGBA")

        # Remove background
        output_image = remove(input_image)

        # Auto crop transparent borders
        bbox = output_image.getbbox()
        if bbox:
            output_image = output_image.crop(bbox)

        # Save to memory
        buffer = io.BytesIO()
        output_image.save(buffer, format="PNG")
        buffer.seek(0)

        new_filename = f"{os.path.splitext(file_doc.file_name)[0]}_no_bg.png"

        # Create new File document
        new_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": new_filename,
                "content": buffer.getvalue(),
                "is_private": file_doc.is_private,
            }
        )

        new_file.insert(ignore_permissions=True)

        return ok(
            "Background removed successfully.",
            data={
                "file_id": new_file.name,
                "file_url": new_file.file_url,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Remove Background Failed")
        return fail("Failed to remove background.", code="INTERNAL_ERROR")
