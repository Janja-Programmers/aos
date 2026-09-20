// Copyright (c) 2026, Africa Online Stores and contributors
(() => {
  "use strict";
  const GROUP = __("Review");
  const API = "aos.api.internal.shorts.review";
  function canWrite(frm) { return Boolean(frm && frm.perm && frm.perm[0] && frm.perm[0].write); }
  function clear(frm) {
    for (const label of [__("Approve Short"), __("Reject Short"), __("Hide Short")]) frm.remove_custom_button(label, GROUP);
  }
  async function review(frm, decision, reason = "") {
    if (frm.is_dirty()) { frappe.msgprint(__("Save or reload this Short before reviewing it.")); return; }
    await frappe.call({ method: API, type: "POST", freeze: true, args: { short_id: frm.doc.name, decision, reason, version: frm.doc.modified } });
    await frm.reload_doc();
    frappe.show_alert({ message: __("Short review decision saved."), indicator: "green" });
  }
  frappe.ui.form.on("AOS Short", {
    refresh(frm) {
      clear(frm);
      if (frm.is_new() || !canWrite(frm) || frm.doc.lifecycle_status === "Deleted") return;
      if (["Pending", "Rejected", "Hidden"].includes(String(frm.doc.moderation_status || "")) && ["Ready", "Not Required"].includes(String(frm.doc.processing_status || ""))) {
        frm.add_custom_button(__("Approve Short"), () => frappe.confirm(__("Approve and publish this Short?"), () => review(frm, "approve")), GROUP);
      }
      if (["Pending", "Approved"].includes(String(frm.doc.moderation_status || ""))) {
        frm.add_custom_button(__("Reject Short"), () => frappe.prompt([{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }], v => review(frm, "reject", String(v.reason || "").trim()), __("Reject Short"), __("Reject")), GROUP);
        frm.add_custom_button(__("Hide Short"), () => frappe.prompt([{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }], v => review(frm, "hide", String(v.reason || "").trim()), __("Hide Short"), __("Hide")), GROUP);
      }
    },
  });
})();
