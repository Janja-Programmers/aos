// Copyright (c) 2026, Africa Online Stores and contributors
// For license information, please see license.txt

(() => {
  "use strict";

  const POLICY = Object.freeze({
    purpose: "category_icon",
    maxSizeBytes: 5 * 1024 * 1024,
    allowedMimeTypes: Object.freeze(["image/jpeg", "image/png", "image/webp"]),
    allowedExtensions: Object.freeze([".jpg", ".jpeg", ".png", ".webp"]),
  });
  const API = Object.freeze({
    initUpload: "aos.api.v1.media.init_upload",
    confirmUpload: "aos.api.v1.media.confirm_upload",
    getMediaUrl: "aos.api.v1.media.get_media_url",
    deleteMedia: "aos.api.v1.media.delete_media",
  });
  const stateByForm = new WeakMap();

  function stateFor(frm) {
    if (!stateByForm.has(frm)) {
      stateByForm.set(frm, { busy: false, status: "", previewFor: "", previewUrl: "" });
    }
    return stateByForm.get(frm);
  }

  function canWrite(frm) {
    const levelZero = frm && frm.perm && frm.perm[0];
    return Boolean(levelZero && levelZero.write);
  }

  function categoryAttributeIds(frm, { dependentOnly = false, exclude = "" } = {}) {
    return Array.from(frm.doc.attributes || [])
      .filter((row) => row && row.attribute && Number(row.is_active || 0) === 1)
      .filter((row) => !dependentOnly || Boolean(String(row.depends_on_attribute || "").trim()))
      .map((row) => String(row.attribute || "").trim())
      .filter((attribute) => attribute && attribute !== exclude);
  }

  function selectAttributeQuery(attributeIds) {
    const names = attributeIds.length ? attributeIds : ["__aos_no_select_attribute__"];
    return { filters: { name: ["in", names], field_type: "Select", is_active: 1 } };
  }

  function configureDependencyQueries(frm) {
    frm.set_query("depends_on_attribute", "attributes", (_doc, cdt, cdn) => {
      const row = (globalThis.locals && globalThis.locals[cdt] && globalThis.locals[cdt][cdn]) || {};
      return selectAttributeQuery(categoryAttributeIds(frm, { exclude: String(row.attribute || "").trim() }));
    });
    frm.set_query("child_attribute", "attribute_dependencies", () => (
      selectAttributeQuery(categoryAttributeIds(frm, { dependentOnly: true }))
    ));
  }

  function escapeHtml(value) {
    if (frappe.utils && typeof frappe.utils.escape_html === "function") {
      return frappe.utils.escape_html(String(value || ""));
    }
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function unwrap(response, fallback) {
    const payload = response && Object.prototype.hasOwnProperty.call(response, "message")
      ? response.message
      : response;
    if (!payload || payload.ok !== true) {
      throw new Error((payload && payload.message) || fallback);
    }
    return payload.data || {};
  }

  async function callAos(method, args, fallback, type = "POST") {
    return unwrap(await frappe.call({ method, type, args }), fallback);
  }

  function errorMessage(error, fallback) {
    const responseMessage = error && error.responseJSON && error.responseJSON.message;
    if (responseMessage && typeof responseMessage === "object" && responseMessage.message) {
      return String(responseMessage.message);
    }
    return String((error && error.message) || fallback);
  }

  function renderUploader(frm) {
    const field = frm.get_field("icon_preview");
    if (!field || !field.$wrapper) {
      return;
    }
    const state = stateFor(frm);
    const mediaId = String(frm.doc.image_media || "").trim();
    const canManage = canWrite(frm) && !frm.is_new();
    const preview = state.previewFor === mediaId ? state.previewUrl : "";
    const image = preview
      ? `<img src="${escapeHtml(preview)}" alt="${escapeHtml(__("Category image"))}" style="max-width:180px;max-height:180px;object-fit:contain;border-radius:8px;">`
      : `<div class="text-muted">${escapeHtml(mediaId ? __("Image preview unavailable") : __("No category image"))}</div>`;
    const status = state.status
      ? `<div class="small text-muted" style="margin-top:.5rem">${escapeHtml(state.status)}</div>`
      : "";
    const remove = mediaId
      ? `<button type="button" class="btn btn-default btn-sm aos-category-image-remove" ${state.busy || !canManage ? "disabled" : ""}>${escapeHtml(__("Remove image"))}</button>`
      : "";

    field.$wrapper.html(`
      <div class="aos-category-image-uploader" style="padding:1rem;border:1px solid var(--border-color,#d1d8dd);border-radius:8px;">
        <div style="min-height:120px;display:flex;align-items:center;justify-content:center">${image}</div>
        <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.75rem">
          <button type="button" class="btn btn-primary btn-sm aos-category-image-upload" ${state.busy || !canManage ? "disabled" : ""}>
            ${escapeHtml(mediaId ? __("Replace image") : __("Upload image"))}
          </button>
          ${remove}
        </div>
        ${status}
      </div>
    `);

    field.$wrapper.find(".aos-category-image-upload").on("click", () => selectFile(frm));
    field.$wrapper.find(".aos-category-image-remove").on("click", () => confirmRemoval(frm));
  }

  function setBusy(frm, busy, status = "") {
    const state = stateFor(frm);
    state.busy = Boolean(busy);
    state.status = status;
    renderUploader(frm);
  }

  async function resolvePreview(frm) {
    const mediaId = String(frm.doc.image_media || "").trim();
    const state = stateFor(frm);
    if (!mediaId) {
      state.previewFor = "";
      state.previewUrl = "";
      renderUploader(frm);
      return;
    }
    if (state.previewFor === mediaId && state.previewUrl) {
      return;
    }
    try {
      const data = await callAos(
        API.getMediaUrl,
        { media_id: mediaId },
        __("Could not resolve the category image."),
        "GET"
      );
      if (String(frm.doc.image_media || "").trim() !== mediaId) {
        return;
      }
      state.previewFor = mediaId;
      state.previewUrl = String(data.url || "").trim();
    } catch (_error) {
      if (String(frm.doc.image_media || "").trim() === mediaId) {
        state.previewFor = mediaId;
        state.previewUrl = "";
      }
    }
    renderUploader(frm);
  }

  function extensionFor(filename) {
    const match = String(filename || "").toLowerCase().match(/(\.[a-z0-9]+)$/);
    return match ? match[1] : "";
  }

  function contentTypeFor(file) {
    const declared = String(file.type || "").trim().toLowerCase();
    if (POLICY.allowedMimeTypes.includes(declared)) {
      return declared;
    }
    return ({
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".png": "image/png",
      ".webp": "image/webp",
    })[extensionFor(file.name)] || "";
  }

  function validateFileBasics(file) {
    if (!(file instanceof File) || file.size <= 0) {
      throw new Error(__("Choose a valid image file."));
    }
    if (file.size > POLICY.maxSizeBytes) {
      throw new Error(__("Category images must not exceed 5 MB."));
    }
    const extension = extensionFor(file.name);
    const contentType = contentTypeFor(file);
    if (!POLICY.allowedExtensions.includes(extension) || !contentType) {
      throw new Error(__("Choose a JPG, PNG, or WebP image."));
    }
    return contentType;
  }

  async function sha256Hex(file) {
    try {
      const digest = await globalThis.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    } catch (_error) {
      return "";
    }
  }

  function idempotencyKey(frm, file, checksum) {
    const identity = `${frm.docname}:${file.name}:${file.size}:${file.lastModified}:${checksum}`;
    let hash = 0x811c9dc5;
    for (const character of identity) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 0x01000193);
    }
    return `category-image:${(hash >>> 0).toString(16)}`;
  }

  function directPut(uploadUrl, headers, file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", uploadUrl, true);
      xhr.withCredentials = false;
      xhr.timeout = 120000;
      Object.entries(headers || {}).forEach(([name, value]) => xhr.setRequestHeader(name, String(value)));
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve();
        } else {
          reject(new Error(__("The Media upload was rejected. Please retry.")));
        }
      });
      xhr.addEventListener("error", () => reject(new Error(__("The Media upload could not be reached."))));
      xhr.addEventListener("timeout", () => reject(new Error(__("The Media upload timed out."))));
      xhr.send(file);
    });
  }

  async function cleanupUnattached(mediaId) {
    if (!mediaId) {
      return;
    }
    try {
      await callAos(API.deleteMedia, { media_id: mediaId }, __("Temporary Media cleanup failed."));
    } catch (_error) {
      // Central Media orphan reconciliation remains the fail-safe.
    }
  }

  function selectFile(frm) {
    if (stateFor(frm).busy || frm.is_new() || !canWrite(frm)) {
      return;
    }
    const input = document.createElement("input");
    input.type = "file";
    input.accept = POLICY.allowedMimeTypes.join(",");
    input.multiple = false;
    input.addEventListener("change", () => {
      const file = Array.from(input.files || [])[0];
      if (file) {
        uploadCategoryImage(frm, file);
      }
    }, { once: true });
    input.click();
  }

  async function uploadCategoryImage(frm, file) {
    let mediaId = "";
    const previousId = String(frm.doc.image_media || "").trim();
    const state = stateFor(frm);
    const previousPreview = { for: state.previewFor, url: state.previewUrl };
    setBusy(frm, true, __("Validating image…"));
    try {
      if (frm.is_dirty()) {
        await frm.save();
      }
      const contentType = validateFileBasics(file);
      const checksum = await sha256Hex(file);
      setBusy(frm, true, __("Preparing Media upload…"));
      const initialized = await callAos(API.initUpload, {
        purpose: POLICY.purpose,
        filename: file.name,
        content_type: contentType,
        size_bytes: file.size,
        checksum_sha256: checksum || undefined,
        idempotency_key: idempotencyKey(frm, file, checksum),
      }, __("Could not initialize the category image upload."));
      mediaId = String(initialized.media_id || "").trim();
      if (!mediaId || !initialized.upload_url) {
        throw new Error(__("Media returned an incomplete upload contract."));
      }
      setBusy(frm, true, __("Uploading image…"));
      await directPut(initialized.upload_url, initialized.upload_headers, file);
      setBusy(frm, true, __("Confirming Media…"));
      const confirmed = await callAos(
        API.confirmUpload,
        { media_id: mediaId },
        __("Could not confirm the category image upload.")
      );
      state.previewFor = mediaId;
      state.previewUrl = String(confirmed.url || (confirmed.media && confirmed.media.url) || "").trim();
      setBusy(frm, true, __("Saving category…"));
      await frm.set_value("image_media", mediaId);
      await frm.save();
      frappe.show_alert({ message: __("Category image uploaded."), indicator: "green" });
    } catch (error) {
      await frm.set_value("image_media", previousId);
      state.previewFor = previousPreview.for;
      state.previewUrl = previousPreview.url;
      await cleanupUnattached(mediaId);
      frappe.msgprint({
        title: __("Category image"),
        indicator: "red",
        message: escapeHtml(errorMessage(error, __("The category image could not be uploaded."))),
      });
    } finally {
      setBusy(frm, false, "");
      await resolvePreview(frm);
    }
  }

  function confirmRemoval(frm) {
    if (stateFor(frm).busy || !canWrite(frm) || !frm.doc.image_media) {
      return;
    }
    frappe.confirm(__("Remove this category image?"), () => removeCategoryImage(frm));
  }

  async function removeCategoryImage(frm) {
    const previousId = String(frm.doc.image_media || "").trim();
    setBusy(frm, true, __("Removing image…"));
    try {
      await frm.set_value("image_media", "");
      await frm.save();
      const state = stateFor(frm);
      state.previewFor = "";
      state.previewUrl = "";
      frappe.show_alert({ message: __("Category image removed."), indicator: "green" });
    } catch (error) {
      await frm.set_value("image_media", previousId);
      frappe.msgprint({
        title: __("Category image"),
        indicator: "red",
        message: escapeHtml(errorMessage(error, __("The category image could not be removed."))),
      });
    } finally {
      setBusy(frm, false, "");
      await resolvePreview(frm);
    }
  }

  frappe.ui.form.on("AOS Category", {
    setup(frm) {
      configureDependencyQueries(frm);
    },
    refresh(frm) {
      renderUploader(frm);
      resolvePreview(frm);
    },
    image_media(frm) {
      renderUploader(frm);
      resolvePreview(frm);
    },
  });
})();
