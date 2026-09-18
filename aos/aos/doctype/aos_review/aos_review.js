// Copyright (c) 2026, Africa Online Stores and contributors
// For license information, please see license.txt

(() => {
  "use strict";

  const REVIEW_METHOD = "aos.aos.doctype.aos_review.review.review_review";
  const REVIEW_GROUP = __("Review");
  const APPROVE_STATUSES = new Set(["Pending", "Rejected", "Hidden"]);
  const REJECT_STATUSES = new Set(["Pending", "Approved", "Hidden"]);
  const stateByForm = new WeakMap();

  function stateFor(frm) {
    if (!stateByForm.has(frm)) stateByForm.set(frm, { busy: false });
    return stateByForm.get(frm);
  }

  function canWrite(frm) {
    const permission = frm && frm.perm && frm.perm[0];
    return Boolean(permission && permission.write);
  }

  function removeButtons(frm) {
    frm.remove_custom_button(__("Approve Review"), REVIEW_GROUP);
    frm.remove_custom_button(__("Reject Review"), REVIEW_GROUP);
  }

  function ensureSaved(frm) {
    if (!frm.is_dirty()) return true;
    frappe.msgprint({
      title: __("Unsaved changes"),
      message: __("Save or reload this Review before moderating it."),
      indicator: "orange",
    });
    return false;
  }

  async function submit(frm, decision, reason = "") {
    const state = stateFor(frm);
    if (state.busy || !ensureSaved(frm)) return;
    const reviewId = String(frm.doc.public_id || "").trim();
    const version = String(frm.doc.modified || "").trim();
    if (!reviewId || !version) return;

    state.busy = true;
    removeButtons(frm);
    try {
      await frappe.call({
        method: REVIEW_METHOD,
        type: "POST",
        freeze: true,
        freeze_message: decision === "approve" ? __("Approving Review...") : __("Rejecting Review..."),
        args: { review_id: reviewId, decision, reason, version },
      });
      await frm.reload_doc();
      frappe.show_alert({
        message: decision === "approve" ? __("Review approved.") : __("Review rejected."),
        indicator: "green",
      });
    } catch (error) {
      const message = error && error.responseJSON && error.responseJSON.message;
      frappe.msgprint({
        title: __("Review failed"),
        message: (message && message.message) || error.message || __("The Review could not be moderated."),
        indicator: "red",
      });
    } finally {
      state.busy = false;
      renderButtons(frm);
    }
  }

  function renderButtons(frm) {
    removeButtons(frm);
    const state = stateFor(frm);
    if (frm.is_new() || state.busy || !canWrite(frm)) return;
    const status = String(frm.doc.status || "").trim();
    if (APPROVE_STATUSES.has(status)) {
      frm.add_custom_button(__("Approve Review"), () => {
        if (ensureSaved(frm)) frappe.confirm(__("Approve and publish this Review?"), () => submit(frm, "approve"));
      }, REVIEW_GROUP);
    }
    if (REJECT_STATUSES.has(status)) {
      frm.add_custom_button(__("Reject Review"), () => {
        if (!ensureSaved(frm)) return;
        frappe.prompt(
          [{ fieldname: "reason", fieldtype: "Small Text", label: __("Rejection reason"), reqd: 1 }],
          (values) => submit(frm, "reject", String(values.reason || "").trim()),
          __("Reject Review"),
          __("Reject")
        );
      }, REVIEW_GROUP);
    }
  }

  frappe.ui.form.on("AOS Review", {
    refresh(frm) { renderButtons(frm); },
  });
})();
