// Copyright (c) 2026, Africa Online Stores and contributors
// For license information, please see license.txt

(() => {
  "use strict";

  const CATEGORY_ICON_POLICY = Object.freeze({
    purpose: "category_icon",
    maxSizeBytes: 5 * 1024 * 1024,
    minWidth: 16,
    minHeight: 16,
    maxWidth: 4096,
    maxHeight: 4096,
    allowedMimeTypes: Object.freeze(["image/jpeg", "image/png", "image/webp"]),
    allowedExtensions: Object.freeze([".jpg", ".jpeg", ".png", ".webp"]),
  });

  const API = Object.freeze({
    initUpload: "aos.api.v1.media.init_upload",
    confirmUpload: "aos.api.v1.media.confirm_upload",
    deleteMedia: "aos.api.v1.media.delete_media",
  });

  const stateByForm = new WeakMap();
  const styleId = "aos-category-icon-uploader-style";

  function stateFor(frm) {
    if (!stateByForm.has(frm)) {
      stateByForm.set(frm, { busy: false, status: "" });
    }
    return stateByForm.get(frm);
  }

  function canManageCategoryImages() {
    return Array.isArray(frappe.user_roles) && frappe.user_roles.includes("System Manager");
  }

  function ensureStyles() {
    if (document.getElementById(styleId)) {
      return;
    }

    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      .aos-category-icon-uploader {
        display: grid;
        grid-template-columns: minmax(140px, 180px) minmax(0, 1fr);
        gap: 1rem;
        align-items: center;
        padding: 1rem;
        border: 1px solid var(--border-color, #d1d8dd);
        border-radius: var(--border-radius-md, 8px);
        background: var(--fg-color, #fff);
      }
      .aos-category-icon-preview {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        aspect-ratio: 1;
        overflow: hidden;
        border: 1px dashed var(--border-color, #d1d8dd);
        border-radius: var(--border-radius-md, 8px);
        background: var(--subtle-fg, #f8f9fa);
      }
      .aos-category-icon-preview img {
        width: 100%;
        height: 100%;
        object-fit: contain;
      }
      .aos-category-icon-placeholder {
        padding: 1rem;
        color: var(--text-muted, #6c7680);
        text-align: center;
      }
      .aos-category-icon-actions {
        display: flex;
        flex-wrap: wrap;
        gap: .5rem;
        margin-top: .75rem;
      }
      .aos-category-icon-meta {
        margin-top: .5rem;
        color: var(--text-muted, #6c7680);
        overflow-wrap: anywhere;
      }
      .aos-category-icon-status {
        margin-top: .5rem;
        color: var(--primary, #2490ef);
      }
      @media (max-width: 767px) {
        .aos-category-icon-uploader {
          grid-template-columns: 1fr;
        }
        .aos-category-icon-preview {
          max-width: 180px;
        }
      }
    `;
    document.head.appendChild(style);
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

  function errorMessage(error, fallback) {
    const responseMessage = error && error.responseJSON && error.responseJSON.message;
    if (responseMessage && typeof responseMessage === "object" && responseMessage.message) {
      return String(responseMessage.message);
    }
    if (error && error.message) {
      return String(error.message);
    }
    return fallback;
  }

  function showError(error, fallback) {
    frappe.msgprint({
      title: __("Category image upload failed"),
      indicator: "red",
      message: escapeHtml(errorMessage(error, fallback)),
    });
  }

  function setBusy(frm, busy, status = "") {
    const state = stateFor(frm);
    state.busy = Boolean(busy);
    state.status = status;
    renderUploader(frm);
  }

  function renderUploader(frm) {
    ensureStyles();
    const field = frm.get_field("icon_preview");
    if (!field || !field.$wrapper) {
      return;
    }

    const state = stateFor(frm);
    const canManage = canManageCategoryImages();
    const isNew = frm.is_new();
    const mediaId = String(frm.doc.icon_media || "").trim();
    const iconUrl = String(frm.doc.icon || "").trim();
    const hasImage = Boolean(mediaId || iconUrl);

    const $wrapper = field.$wrapper;
    $wrapper.off(".aosCategoryIcon");
    $wrapper.empty();

    const $panel = $("<div>", { class: "aos-category-icon-uploader" });
    const $preview = $("<div>", { class: "aos-category-icon-preview" });
    if (iconUrl) {
      $("<img>", {
        src: iconUrl,
        alt: __("Category image preview"),
        loading: "lazy",
      }).appendTo($preview);
    } else {
      $("<div>", {
        class: "aos-category-icon-placeholder",
        text: __("No category image uploaded"),
      }).appendTo($preview);
    }

    const $content = $("<div>");
    $("<div>", {
      class: "text-muted",
      text: __(
        "Upload JPG, PNG, or WebP through the secured AOS Media pipeline. The backend validates type, size, checksum, and image dimensions before attachment."
      ),
    }).appendTo($content);

    if (mediaId) {
      $("<div>", {
        class: "aos-category-icon-meta",
        text: `${__("Media ID")}: ${mediaId}`,
      }).appendTo($content);
    }

    if (!canManage) {
      $("<div>", {
        class: "aos-category-icon-status text-danger",
        text: __("Only System Managers can manage category images."),
      }).appendTo($content);
    } else if (isNew) {
      $("<div>", {
        class: "aos-category-icon-status",
        text: __("Save the category before uploading its image."),
      }).appendTo($content);
    } else if (state.status) {
      $("<div>", {
        class: "aos-category-icon-status",
        text: state.status,
      }).appendTo($content);
    }

    const $actions = $("<div>", { class: "aos-category-icon-actions" });
    $("<button>", {
      type: "button",
      class: "btn btn-primary btn-sm",
      text: hasImage ? __("Replace image") : __("Upload image"),
      disabled: state.busy || isNew || !canManage,
      "data-action": "upload",
    }).appendTo($actions);

    if (hasImage) {
      $("<button>", {
        type: "button",
        class: "btn btn-default btn-sm",
        text: __("Remove image"),
        disabled: state.busy || !canManage,
        "data-action": "remove",
      }).appendTo($actions);

      if (iconUrl) {
        $("<a>", {
          class: "btn btn-default btn-sm",
          text: __("Open image"),
          href: iconUrl,
          target: "_blank",
          rel: "noopener noreferrer",
        }).appendTo($actions);
      }
    }

    $actions.appendTo($content);
    $preview.appendTo($panel);
    $content.appendTo($panel);
    $panel.appendTo($wrapper);

    $wrapper.on("click.aosCategoryIcon", '[data-action="upload"]', () => selectFile(frm));
    $wrapper.on("click.aosCategoryIcon", '[data-action="remove"]', () => confirmRemoval(frm));
  }

  function extensionFor(filename) {
    const normalized = String(filename || "").trim().toLowerCase();
    const index = normalized.lastIndexOf(".");
    return index >= 0 ? normalized.slice(index) : "";
  }

  function contentTypeFor(file) {
    const declared = String(file.type || "").trim().toLowerCase();
    if (CATEGORY_ICON_POLICY.allowedMimeTypes.includes(declared)) {
      return declared;
    }
    const extension = extensionFor(file.name);
    const byExtension = {
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".png": "image/png",
      ".webp": "image/webp",
    };
    return byExtension[extension] || "";
  }

  function validateFileBasics(file) {
    if (!(file instanceof File)) {
      throw new Error(__("Choose a valid image file."));
    }
    if (file.size <= 0) {
      throw new Error(__("The selected image is empty."));
    }
    if (file.size > CATEGORY_ICON_POLICY.maxSizeBytes) {
      throw new Error(__("Category images must not exceed 5 MB."));
    }
    const extension = extensionFor(file.name);
    const contentType = contentTypeFor(file);
    if (!CATEGORY_ICON_POLICY.allowedExtensions.includes(extension) || !contentType) {
      throw new Error(__("Choose a JPG, PNG, or WebP image."));
    }
    return contentType;
  }

  function readImageDimensions(file) {
    return new Promise((resolve, reject) => {
      const objectUrl = URL.createObjectURL(file);
      const image = new Image();
      image.onload = () => {
        URL.revokeObjectURL(objectUrl);
        resolve({ width: image.naturalWidth, height: image.naturalHeight });
      };
      image.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        reject(new Error(__("The selected file is not a readable image.")));
      };
      image.src = objectUrl;
    });
  }

  function validateDimensions(dimensions) {
    const { width, height } = dimensions;
    if (
      width < CATEGORY_ICON_POLICY.minWidth ||
      height < CATEGORY_ICON_POLICY.minHeight ||
      width > CATEGORY_ICON_POLICY.maxWidth ||
      height > CATEGORY_ICON_POLICY.maxHeight
    ) {
      throw new Error(
        __("Category images must be between 16×16 and 4096×4096 pixels.")
      );
    }
  }

  async function sha256Hex(file) {
    const cryptoApi = globalThis.crypto;
    if (!cryptoApi || !cryptoApi.subtle || typeof cryptoApi.subtle.digest !== "function") {
      return "";
    }
    try {
      const digest = await cryptoApi.subtle.digest("SHA-256", await file.arrayBuffer());
      return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    } catch (_error) {
      // Checksum is optional at initialization; confirmation still performs authoritative content checks.
      return "";
    }
  }

  function stableStringHash(value) {
    let hash = 0x811c9dc5;
    for (const character of String(value || "")) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  function uploadIdempotencyKey(frm, file, checksum) {
    const categoryHash = stableStringHash(frm.docname);
    const fileHash = checksum ? checksum.slice(0, 32) : stableStringHash(file.name);
    return `category-icon:${categoryHash}:${fileHash}:${file.size}:${file.lastModified}`;
  }

  function unwrapAosResponse(response, fallback) {
    const payload = response && Object.prototype.hasOwnProperty.call(response, "message")
      ? response.message
      : response;
    if (!payload || payload.ok !== true) {
      throw new Error((payload && payload.message) || fallback);
    }
    return payload.data || {};
  }

  async function callAos(method, args, fallback) {
    const response = await frappe.call({ method, type: "POST", args });
    return unwrapAosResponse(response, fallback);
  }

  function directPut(uploadUrl, headers, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", uploadUrl, true);
      xhr.withCredentials = false;
      xhr.timeout = 120000;

      Object.entries(headers || {}).forEach(([name, value]) => {
        xhr.setRequestHeader(name, String(value));
      });

      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable && typeof onProgress === "function") {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      });
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve();
          return;
        }
        reject(new Error(__("The object-storage upload was rejected. Please retry.")));
      });
      xhr.addEventListener("error", () => {
        reject(new Error(__("The image upload could not reach object storage.")));
      });
      xhr.addEventListener("timeout", () => {
        reject(new Error(__("The image upload timed out. Please retry.")));
      });
      xhr.send(file);
    });
  }

  async function deleteUnattachedMedia(mediaId) {
    if (!mediaId) {
      return;
    }
    try {
      await callAos(
        API.deleteMedia,
        { media_id: mediaId },
        __("Temporary media cleanup failed.")
      );
    } catch (_error) {
      // Confirmed but unattached objects remain covered by centralized orphan cleanup.
    }
  }

  function selectFile(frm) {
    if (stateFor(frm).busy || frm.is_new() || !canManageCategoryImages()) {
      return;
    }

    const input = document.createElement("input");
    input.type = "file";
    input.accept = CATEGORY_ICON_POLICY.allowedMimeTypes.join(",");
    input.multiple = false;
    input.addEventListener("change", () => {
      const [file] = Array.from(input.files || []);
      if (file) {
        uploadCategoryImage(frm, file);
      }
    }, { once: true });
    input.click();
  }

  async function uploadCategoryImage(frm, file) {
    let mediaId = "";
    const previous = {
      mediaId: String(frm.doc.icon_media || ""),
      iconUrl: String(frm.doc.icon || ""),
    };

    setBusy(frm, true, __("Validating image…"));
    frappe.show_progress(__("Uploading category image"), 5, 100, __("Validating image"));

    try {
      if (frm.is_dirty()) {
        await frm.save();
      }

      const contentType = validateFileBasics(file);
      validateDimensions(await readImageDimensions(file));
      const checksum = await sha256Hex(file);
      const idempotencyKey = uploadIdempotencyKey(frm, file, checksum);

      setBusy(frm, true, __("Preparing secure upload…"));
      frappe.show_progress(__("Uploading category image"), 15, 100, __("Preparing secure upload"));
      const initialized = await callAos(
        API.initUpload,
        {
          purpose: CATEGORY_ICON_POLICY.purpose,
          filename: file.name,
          content_type: contentType,
          size_bytes: file.size,
          checksum_sha256: checksum || undefined,
          idempotency_key: idempotencyKey,
        },
        __("Could not initialize the category image upload.")
      );

      mediaId = String(initialized.media_id || "").trim();
      if (!mediaId || !initialized.upload_url) {
        throw new Error(__("The upload service returned an incomplete response."));
      }

      setBusy(frm, true, __("Uploading image…"));
      await directPut(initialized.upload_url, initialized.upload_headers, file, (percent) => {
        frappe.show_progress(
          __("Uploading category image"),
          15 + Math.round(percent * 0.6),
          100,
          `${__("Uploading image")}: ${percent}%`
        );
      });

      setBusy(frm, true, __("Verifying image…"));
      frappe.show_progress(__("Uploading category image"), 82, 100, __("Verifying image"));
      const confirmed = await callAos(
        API.confirmUpload,
        { media_id: mediaId },
        __("Could not verify the uploaded category image.")
      );
      const confirmedUrl = String(
        confirmed.url || (confirmed.media && confirmed.media.url) || ""
      ).trim();

      setBusy(frm, true, __("Saving category image…"));
      frappe.show_progress(__("Uploading category image"), 94, 100, __("Saving category"));
      await frm.set_value("icon_media", mediaId);
      if (confirmedUrl) {
        await frm.set_value("icon", confirmedUrl);
      }
      await frm.save();

      frappe.show_progress(__("Uploading category image"), 100, 100, __("Complete"));
      frappe.show_alert({ message: __("Category image uploaded."), indicator: "green" });
    } catch (error) {
      await frm.set_value("icon_media", previous.mediaId);
      await frm.set_value("icon", previous.iconUrl);
      frm.refresh_field("icon_media");
      frm.refresh_field("icon");
      await deleteUnattachedMedia(mediaId);
      showError(error, __("The category image could not be uploaded."));
    } finally {
      frappe.hide_progress();
      setBusy(frm, false, "");
    }
  }

  function confirmRemoval(frm) {
    if (
      stateFor(frm).busy ||
      !canManageCategoryImages() ||
      (!frm.doc.icon_media && !frm.doc.icon)
    ) {
      return;
    }

    frappe.confirm(
      __("Remove this category image? The Media lifecycle will safely release it after the category is saved."),
      () => removeCategoryImage(frm)
    );
  }

  async function removeCategoryImage(frm) {
    const previous = {
      mediaId: String(frm.doc.icon_media || ""),
      iconUrl: String(frm.doc.icon || ""),
    };
    setBusy(frm, true, __("Removing image…"));

    try {
      await frm.set_value("icon_media", "");
      await frm.set_value("icon", "");
      await frm.save();
      frappe.show_alert({ message: __("Category image removed."), indicator: "green" });
    } catch (error) {
      await frm.set_value("icon_media", previous.mediaId);
      await frm.set_value("icon", previous.iconUrl);
      frm.refresh_field("icon_media");
      frm.refresh_field("icon");
      showError(error, __("The category image could not be removed."));
    } finally {
      setBusy(frm, false, "");
    }
  }

  frappe.ui.form.on("AOS Category", {
    refresh(frm) {
      renderUploader(frm);
    },
    icon(frm) {
      renderUploader(frm);
    },
    icon_media(frm) {
      renderUploader(frm);
    },
  });
})();
