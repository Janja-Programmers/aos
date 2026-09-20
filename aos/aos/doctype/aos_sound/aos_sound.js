// Copyright (c) 2026, Africa Online Stores and contributors
// Desk helper for the canonical hardened Media upload contract.
(() => {
  "use strict";

  const PURPOSE = "sound_upload";
  const ALLOWED = new Set([".mp3", ".m4a", ".aac", ".wav", ".ogg"]);
  const MAX_BYTES = 50 * 1024 * 1024;

  function extension(name) {
    const value = String(name || "").toLowerCase();
    const idx = value.lastIndexOf(".");
    return idx >= 0 ? value.slice(idx) : "";
  }

  function render(frm) {
    const field = frm.get_field("sound_upload_preview");
    if (!field || !field.$wrapper) return;
    const media = String(frm.doc.sound_media || "").trim();
    const disabled = !(frm.perm && frm.perm[0] && (frm.is_new() ? frm.perm[0].create : frm.perm[0].write));
    field.$wrapper.empty();
    const $root = $("<div>", { class: "aos-sound-upload" });
    $("<p>", { class: "text-muted", text: media ? `${__("Media ID")}: ${media}` : __("Upload audio through hardened AOS Media.") }).appendTo($root);
    if (!media) {
      $("<button>", { type: "button", class: "btn btn-primary btn-sm", text: __("Upload audio"), disabled })
        .on("click", () => choose(frm))
        .appendTo($root);
    }
    field.$wrapper.append($root);
  }

  function choose(frm) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".mp3,.m4a,.aac,.wav,.ogg,audio/*";
    input.onchange = async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      if (!ALLOWED.has(extension(file.name)) || file.size <= 0 || file.size > MAX_BYTES) {
        frappe.msgprint({ title: __("Invalid sound"), indicator: "red", message: __("Choose an MP3, M4A, AAC, WAV, or OGG file up to 50 MB.") });
        return;
      }
      try {
        const init = await frappe.call({ method: "aos.api.v1.media.init_upload", args: { purpose: PURPOSE, filename: file.name, content_type: file.type || "audio/mpeg", size_bytes: file.size } });
        const data = init.message && init.message.data ? init.message.data : init.message;
        if (!data || !data.media_id || !data.upload_url) throw new Error(__("Upload initialization failed."));
        const response = await fetch(data.upload_url, { method: "PUT", body: file, headers: data.upload_headers || { "Content-Type": file.type || "audio/mpeg" } });
        if (!response.ok) throw new Error(__("Object upload failed."));
        const confirmed = await frappe.call({ method: "aos.api.v1.media.confirm_upload", args: { media_id: data.media_id } });
        const confirmedData = confirmed.message && confirmed.message.data ? confirmed.message.data : confirmed.message;
        await frm.set_value("sound_media", data.media_id);
        if (!String(frm.doc.title || "").trim()) await frm.set_value("title", file.name.replace(/\.[^.]+$/, ""));
        if (confirmedData && confirmedData.duration_seconds) await frm.set_value("duration_seconds", confirmedData.duration_seconds);
        await frm.save();
      } catch (error) {
        frappe.msgprint({ title: __("Sound upload failed"), indicator: "red", message: String(error && error.message ? error.message : error) });
      }
    };
    input.click();
  }

  frappe.ui.form.on("AOS Sound", {
    refresh: render,
    sound_media: render,
    duration_seconds: render,
  });
})();
