// Copyright (c) 2026, Africa Online Stores and contributors
// For license information, please see license.txt

(() => {
  "use strict";

  const SOUND_POLICY = Object.freeze({
    purpose: "sound_upload",
    maxSizeBytes: 50 * 1024 * 1024,
    maxDurationSeconds: 600,
    allowedMimeTypes: Object.freeze([
      "audio/mpeg",
      "audio/mp4",
      "audio/aac",
      "audio/wav",
      "audio/ogg",
    ]),
    allowedExtensions: Object.freeze([".mp3", ".m4a", ".aac", ".wav", ".ogg"]),
  });

  const API = Object.freeze({
    initUpload: "aos.api.v1.media.init_upload",
    confirmUpload: "aos.api.v1.media.confirm_upload",
    deleteMedia: "aos.api.v1.media.delete_media",
  });

  const stateByForm = new WeakMap();
  const styleId = "aos-sound-uploader-style";

  function stateFor(frm) {
    if (!stateByForm.has(frm)) {
      stateByForm.set(frm, { busy: false, status: "" });
    }
    return stateByForm.get(frm);
  }

  function canManageSounds() {
    return Array.isArray(frappe.user_roles) && frappe.user_roles.includes("System Manager");
  }

  function ensureStyles() {
    if (document.getElementById(styleId)) {
      return;
    }

    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      .aos-sound-uploader {
        display: grid;
        grid-template-columns: minmax(220px, 320px) minmax(0, 1fr);
        gap: 1.25rem;
        align-items: center;
        padding: 1rem;
        border: 1px solid var(--border-color, #d1d8dd);
        border-radius: var(--border-radius-md, 8px);
        background: var(--fg-color, #fff);
      }
      .aos-sound-player {
        display: grid;
        min-height: 150px;
        place-items: center;
        gap: .75rem;
        padding: 1rem;
        border: 1px dashed var(--border-color, #d1d8dd);
        border-radius: var(--border-radius-md, 8px);
        background: var(--subtle-fg, #f8f9fa);
      }
      .aos-sound-player audio {
        width: 100%;
      }
      .aos-sound-placeholder {
        color: var(--text-muted, #6c7680);
        text-align: center;
      }
      .aos-sound-actions {
        display: flex;
        flex-wrap: wrap;
        gap: .5rem;
        margin-top: .75rem;
      }
      .aos-sound-meta,
      .aos-sound-status {
        margin-top: .5rem;
        overflow-wrap: anywhere;
      }
      .aos-sound-meta {
        color: var(--text-muted, #6c7680);
      }
      .aos-sound-status {
        color: var(--primary, #2490ef);
      }
      @media (max-width: 767px) {
        .aos-sound-uploader {
          grid-template-columns: 1fr;
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
      title: __("Sound upload failed"),
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

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "";
    }
    if (bytes < 1024 * 1024) {
      return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatDuration(value) {
    const seconds = Math.max(0, Math.round(Number(value || 0)));
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    return `${minutes}:${String(remainder).padStart(2, "0")}`;
  }

  function renderUploader(frm) {
    ensureStyles();
    const field = frm.get_field("sound_upload_preview");
    if (!field || !field.$wrapper) {
      return;
    }

    const state = stateFor(frm);
    const canManage = canManageSounds();
    const mediaId = String(frm.doc.sound_media || "").trim();
    const soundUrl = String(frm.doc.file_url || "").trim();
    const hasAudio = Boolean(mediaId || soundUrl);

    const $wrapper = field.$wrapper;
    $wrapper.off(".aosSoundUpload");
    $wrapper.empty();

    const $panel = $("<div>", { class: "aos-sound-uploader" });
    const $player = $("<div>", { class: "aos-sound-player" });
    if (soundUrl) {
      $("<audio>", {
        src: soundUrl,
        controls: true,
        preload: "metadata",
      }).appendTo($player);
    } else {
      $("<div>", {
        class: "aos-sound-placeholder",
        text: __("No audio uploaded"),
      }).appendTo($player);
    }

    const $content = $("<div>");
    $("<div>", {
      class: "text-muted",
      text: hasAudio
        ? __("This audio is ready for Shorts.")
        : __("Upload an MP3, M4A, AAC, WAV, or OGG file."),
    }).appendTo($content);

    if (mediaId) {
      $("<div>", {
        class: "aos-sound-meta",
        text: `${__("Media ID")}: ${mediaId}`,
      }).appendTo($content);
    }
    if (frm.doc.duration_seconds) {
      $("<div>", {
        class: "aos-sound-meta",
        text: `${__("Duration")}: ${formatDuration(frm.doc.duration_seconds)}`,
      }).appendTo($content);
    }

    if (!canManage) {
      $("<div>", {
        class: "aos-sound-status text-danger",
        text: __("Only System Managers can upload sounds here."),
      }).appendTo($content);
    } else if (state.status) {
      $("<div>", {
        class: "aos-sound-status",
        text: state.status,
      }).appendTo($content);
    } else if (hasAudio && !frm.is_new()) {
      $("<div>", {
        class: "aos-sound-meta",
        text: __("To use different audio, create a new Sound."),
      }).appendTo($content);
    }

    const $actions = $("<div>", { class: "aos-sound-actions" });
    if (!hasAudio) {
      $("<button>", {
        type: "button",
        class: "btn btn-primary btn-sm",
        text: __("Upload audio"),
        disabled: state.busy || !canManage,
        "data-action": "upload",
      }).appendTo($actions);
    }

    if (soundUrl) {
      $("<a>", {
        class: "btn btn-default btn-sm",
        text: __("Open audio"),
        href: soundUrl,
        target: "_blank",
        rel: "noopener noreferrer",
      }).appendTo($actions);
    }

    $actions.appendTo($content);
    $player.appendTo($panel);
    $content.appendTo($panel);
    $panel.appendTo($wrapper);

    $wrapper.on("click.aosSoundUpload", '[data-action="upload"]', () => selectFile(frm));
  }

  function extensionFor(filename) {
    const normalized = String(filename || "").trim().toLowerCase();
    const index = normalized.lastIndexOf(".");
    return index >= 0 ? normalized.slice(index) : "";
  }

  function contentTypeFor(file) {
    const declared = String(file.type || "").trim().toLowerCase();
    if (SOUND_POLICY.allowedMimeTypes.includes(declared)) {
      return declared;
    }
    const byExtension = {
      ".mp3": "audio/mpeg",
      ".m4a": "audio/mp4",
      ".aac": "audio/aac",
      ".wav": "audio/wav",
      ".ogg": "audio/ogg",
    };
    return byExtension[extensionFor(file.name)] || "";
  }

  function validateFileBasics(file) {
    if (!(file instanceof File)) {
      throw new Error(__("Choose a valid audio file."));
    }
    if (file.size <= 0) {
      throw new Error(__("The selected audio file is empty."));
    }
    if (file.size > SOUND_POLICY.maxSizeBytes) {
      throw new Error(__("Audio files must not exceed 50 MB."));
    }
    const extension = extensionFor(file.name);
    const contentType = contentTypeFor(file);
    if (!SOUND_POLICY.allowedExtensions.includes(extension) || !contentType) {
      throw new Error(__("Choose an MP3, M4A, AAC, WAV, or OGG file."));
    }
    return contentType;
  }

  function readAudioDuration(file) {
    return new Promise((resolve, reject) => {
      const objectUrl = URL.createObjectURL(file);
      const audio = document.createElement("audio");
      let settled = false;
      const finish = (callback, value) => {
        if (settled) {
          return;
        }
        settled = true;
        URL.revokeObjectURL(objectUrl);
        audio.removeAttribute("src");
        callback(value);
      };
      const timeout = globalThis.setTimeout(() => {
        finish(reject, new Error(__("The selected audio could not be read.")));
      }, 15000);

      audio.preload = "metadata";
      audio.addEventListener("loadedmetadata", () => {
        globalThis.clearTimeout(timeout);
        const duration = Number(audio.duration);
        if (!Number.isFinite(duration) || duration <= 0) {
          finish(reject, new Error(__("The selected audio has an invalid duration.")));
          return;
        }
        if (duration > SOUND_POLICY.maxDurationSeconds) {
          finish(reject, new Error(__("Audio must be 10 minutes or shorter.")));
          return;
        }
        finish(resolve, duration);
      }, { once: true });
      audio.addEventListener("error", () => {
        globalThis.clearTimeout(timeout);
        finish(reject, new Error(__("The selected file is not readable audio.")));
      }, { once: true });
      audio.src = objectUrl;
    });
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
    const formHash = stableStringHash(frm.docname);
    const fileHash = checksum ? checksum.slice(0, 32) : stableStringHash(file.name);
    return `sound-upload:${formHash}:${fileHash}:${file.size}:${file.lastModified}`;
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
      xhr.timeout = 10 * 60 * 1000;

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
        reject(new Error(__("The audio upload was not accepted. Please retry.")));
      });
      xhr.addEventListener("error", () => {
        reject(new Error(__("The audio upload could not connect. Please retry.")));
      });
      xhr.addEventListener("timeout", () => {
        reject(new Error(__("The audio upload timed out. Please retry.")));
      });
      xhr.send(file);
    });
  }

  async function deleteUnattachedMedia(mediaId) {
    if (!mediaId) {
      return;
    }
    try {
      await callAos(API.deleteMedia, { media_id: mediaId }, __("Temporary upload cleanup failed."));
    } catch (_error) {
      // Central orphan cleanup remains the final safety net.
    }
  }

  function titleFromFilename(filename) {
    const extension = extensionFor(filename);
    const base = extension ? String(filename).slice(0, -extension.length) : String(filename || "");
    return (base.replaceAll("_", " ").replaceAll("-", " ").trim() || __("Uploaded sound")).slice(0, 140);
  }

  function selectFile(frm) {
    if (stateFor(frm).busy || frm.doc.sound_media || !canManageSounds()) {
      return;
    }
    const input = document.createElement("input");
    input.type = "file";
    input.accept = [...SOUND_POLICY.allowedMimeTypes, ...SOUND_POLICY.allowedExtensions].join(",");
    input.multiple = false;
    input.addEventListener("change", () => {
      const [file] = Array.from(input.files || []);
      if (file) {
        void uploadSound(frm, file);
      }
    }, { once: true });
    input.click();
  }

  async function uploadSound(frm, file) {
    let mediaId = "";
    const previous = {
      title: String(frm.doc.title || ""),
      mediaId: String(frm.doc.sound_media || ""),
      fileUrl: String(frm.doc.file_url || ""),
      fileKey: String(frm.doc.file_key || ""),
      duration: Number(frm.doc.duration_seconds || 0),
    };

    setBusy(frm, true, __("Checking audio…"));
    frappe.show_progress(__("Uploading sound"), 5, 100, __("Checking audio"));

    try {
      const contentType = validateFileBasics(file);
      const duration = await readAudioDuration(file);
      const checksum = await sha256Hex(file);

      setBusy(frm, true, __("Preparing upload…"));
      frappe.show_progress(__("Uploading sound"), 15, 100, __("Preparing upload"));
      const initialized = await callAos(
        API.initUpload,
        {
          purpose: SOUND_POLICY.purpose,
          filename: file.name,
          content_type: contentType,
          size_bytes: file.size,
          checksum_sha256: checksum || undefined,
          idempotency_key: uploadIdempotencyKey(frm, file, checksum),
        },
        __("Could not start the sound upload.")
      );

      mediaId = String(initialized.media_id || "").trim();
      if (!mediaId || !initialized.upload_url) {
        throw new Error(__("The upload could not be started. Please retry."));
      }

      setBusy(frm, true, __("Uploading audio…"));
      await directPut(initialized.upload_url, initialized.upload_headers, file, (percent) => {
        frappe.show_progress(
          __("Uploading sound"),
          15 + Math.round(percent * 0.62),
          100,
          `${__("Uploading audio")}: ${percent}%`
        );
      });

      setBusy(frm, true, __("Checking upload…"));
      frappe.show_progress(__("Uploading sound"), 82, 100, __("Checking upload"));
      const confirmed = await callAos(
        API.confirmUpload,
        { media_id: mediaId },
        __("Could not confirm the sound upload.")
      );
      const confirmedUrl = String(
        confirmed.url || (confirmed.media && confirmed.media.url) || ""
      ).trim();

      setBusy(frm, true, __("Saving sound…"));
      frappe.show_progress(__("Uploading sound"), 94, 100, __("Saving sound"));
      if (!String(frm.doc.title || "").trim()) {
        await frm.set_value("title", titleFromFilename(file.name));
      }
      await frm.set_value("sound_media", mediaId);
      await frm.set_value("duration_seconds", Number(duration.toFixed(3)));
      if (confirmedUrl) {
        await frm.set_value("file_url", confirmedUrl);
      }
      await frm.save();

      frappe.show_progress(__("Uploading sound"), 100, 100, __("Complete"));
      frappe.show_alert({
        message: `${__("Sound uploaded")}: ${formatBytes(file.size)}`,
        indicator: "green",
      });
    } catch (error) {
      await frm.set_value("title", previous.title);
      await frm.set_value("sound_media", previous.mediaId);
      await frm.set_value("file_url", previous.fileUrl);
      await frm.set_value("file_key", previous.fileKey);
      await frm.set_value("duration_seconds", previous.duration);
      ["title", "sound_media", "file_url", "file_key", "duration_seconds"].forEach((fieldname) => {
        frm.refresh_field(fieldname);
      });
      await deleteUnattachedMedia(mediaId);
      showError(error, __("The sound could not be uploaded."));
    } finally {
      frappe.hide_progress();
      setBusy(frm, false, "");
    }
  }

  function applySourceTypeRules(frm) {
    const sourceType = String(frm.doc.source_type || "").trim().toLowerCase();
    if (sourceType === "commercial" && !frm.doc.is_commercial_safe) {
      void frm.set_value("is_commercial_safe", 1);
    }
    frm.set_df_property("is_commercial_safe", "read_only", sourceType === "commercial" ? 1 : 0);
  }

  frappe.ui.form.on("AOS Sound", {
    refresh(frm) {
      applySourceTypeRules(frm);
      renderUploader(frm);
    },
    source_type(frm) {
      applySourceTypeRules(frm);
    },
    sound_media(frm) {
      renderUploader(frm);
    },
    file_url(frm) {
      renderUploader(frm);
    },
    duration_seconds(frm) {
      renderUploader(frm);
    },
  });
})();
