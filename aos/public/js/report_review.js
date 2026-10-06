// Copyright (c) 2026, Africa Online Stores and contributors
(() => {
  "use strict";

  const API = "aos.api.internal.reports.review";
  const GROUP = __("Review");
  const TYPES = {
    "AOS User Report": "user",
    "AOS Ad Report": "ad",
    "AOS Short Report": "short",
    "AOS Review Report": "review",
  };
  const busy = new WeakSet();

  function canWrite(frm) {
    return Boolean(frm && frm.perm && frm.perm[0] && frm.perm[0].write);
  }

  function clear(frm) {
    frm.remove_custom_button(__("Resolve Report"), GROUP);
    frm.remove_custom_button(__("Reject Report"), GROUP);
  }

  function ensureSaved(frm) {
    if (!frm.is_dirty()) return true;
    frappe.msgprint({
      title: __("Unsaved changes"),
      message: __("Reload this Report before reviewing it."),
      indicator: "orange",
    });
    return false;
  }

  async function submit(frm, decision, note = "") {
    if (busy.has(frm) || !ensureSaved(frm)) return;
    const reportType = TYPES[frm.doctype];
    const reportId = String(frm.doc.name || "").trim();
    const version = String(frm.doc.modified || "").trim();
    if (!reportType || !reportId || !version) {
      frappe.msgprint(__("Reload this Report before reviewing it."));
      return;
    }

    busy.add(frm);
    clear(frm);
    try {
      const response = await frappe.call({
        method: API,
        type: "POST",
        freeze: true,
        freeze_message: decision === "resolve" ? __("Resolving Report...") : __("Rejecting Report..."),
        args: { report_type: reportType, report_id: reportId, decision, note, version },
      });
      const payload = response && Object.prototype.hasOwnProperty.call(response, "message") ? response.message : response;
      if (!payload || payload.ok !== true) throw new Error((payload && payload.message) || __("Report review failed."));
      await frm.reload_doc();
      frappe.show_alert({
        message: decision === "resolve" ? __("Report resolved.") : __("Report rejected."),
        indicator: "green",
      });
    } catch (error) {
      frappe.msgprint({
        title: __("Review failed"),
        message: String((error && error.message) || __("The Report could not be reviewed.")),
        indicator: "red",
      });
    } finally {
      busy.delete(frm);
      render(frm);
    }
  }

  function resolve(frm) {
    frappe.confirm(
      __("Resolve this Report as a valid reviewed complaint? This decision is terminal."),
      () => submit(frm, "resolve")
    );
  }

  function reject(frm) {
    frappe.prompt(
      [{ fieldname: "note", fieldtype: "Small Text", label: __("Rejection note"), reqd: 1 }],
      values => submit(frm, "reject", String(values.note || "").trim()),
      __("Reject Report"),
      __("Reject")
    );
  }

  function render(frm) {
    clear(frm);
    if (frm.is_new() || busy.has(frm) || !canWrite(frm) || String(frm.doc.status || "") !== "Reviewing") return;
    frm.add_custom_button(__("Resolve Report"), () => resolve(frm), GROUP);
    frm.add_custom_button(__("Reject Report"), () => reject(frm), GROUP);
  }

  Object.keys(TYPES).forEach(doctype => {
    frappe.ui.form.on(doctype, { refresh(frm) { render(frm); } });
  });
})();
