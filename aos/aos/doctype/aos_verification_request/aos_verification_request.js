// Copyright (c) 2026, Africa Online Stores and contributors
// For license information, please see license.txt

(() => {
  "use strict";

  const REVIEW_METHOD =
    "aos.aos.doctype.aos_verification_request.review.review_verification_request";
  const REVIEW_GROUP = __("Review");
  const stateByForm = new WeakMap();

  function stateFor(frm) {
    if (!stateByForm.has(frm)) {
      stateByForm.set(frm, { busy: false });
    }
    return stateByForm.get(frm);
  }

  function canWrite(frm) {
    const levelZero = frm && frm.perm && frm.perm[0];
    return Boolean(levelZero && levelZero.write);
  }

  function errorMessage(error, fallback) {
    const responseMessage = error && error.responseJSON && error.responseJSON.message;
    if (responseMessage && typeof responseMessage === "object" && responseMessage.message) {
      return String(responseMessage.message);
    }
    if (typeof responseMessage === "string" && responseMessage.trim()) {
      return responseMessage;
    }
    return String((error && error.message) || fallback);
  }

  function removeReviewButtons(frm) {
    frm.remove_custom_button(__("Start Review"), REVIEW_GROUP);
    frm.remove_custom_button(__("Approve"), REVIEW_GROUP);
    frm.remove_custom_button(__("Reject"), REVIEW_GROUP);
    frm.remove_custom_button(__("Revoke"), REVIEW_GROUP);
  }

  function ensureSavedState(frm) {
    if (!frm.is_dirty()) {
      return true;
    }
    frappe.msgprint({
      title: __("Unsaved changes"),
      message: __("Save or reload this verification request before reviewing it."),
      indicator: "orange",
    });
    return false;
  }

  async function submitReviewAction(frm, action, reason = "") {
    const state = stateFor(frm);
    if (state.busy || !ensureSavedState(frm)) {
      return;
    }

    const requestName = String(frm.doc.name || "").trim();
    const version = String(frm.doc.modified || "").trim();
    if (!requestName || !version) {
      frappe.msgprint({
        title: __("Review unavailable"),
        message: __("Reload this verification request before reviewing it."),
        indicator: "orange",
      });
      return;
    }

    const labels = {
      start_review: __("Starting review..."),
      approve: __("Approving verification..."),
      reject: __("Rejecting verification..."),
      revoke: __("Revoking verification..."),
    };

    state.busy = true;
    removeReviewButtons(frm);
    try {
      await frappe.call({
        method: REVIEW_METHOD,
        type: "POST",
        freeze: true,
        freeze_message: labels[action] || __("Updating verification..."),
        args: {
          name: requestName,
          action,
          reason,
          version,
        },
      });
      await frm.reload_doc();
      const messages = {
        start_review: __("Verification review started."),
        approve: __("Verification approved."),
        reject: __("Verification rejected."),
        revoke: __("Verification revoked."),
      };
      frappe.show_alert({
        message: messages[action] || __("Verification updated."),
        indicator: "green",
      });
    } catch (error) {
      frappe.msgprint({
        title: __("Review failed"),
        message: errorMessage(error, __("The verification request could not be reviewed.")),
        indicator: "red",
      });
    } finally {
      state.busy = false;
      renderReviewButtons(frm);
    }
  }

  function startReview(frm) {
    frappe.confirm(
      __("Start reviewing this verification request?"),
      () => submitReviewAction(frm, "start_review")
    );
  }

  function approve(frm) {
    frappe.confirm(
      __("Approve this verification request and mark the account as verified?"),
      () => submitReviewAction(frm, "approve")
    );
  }

  function reject(frm) {
    frappe.prompt(
      [
        {
          fieldname: "reason",
          fieldtype: "Small Text",
          label: __("Rejection reason"),
          reqd: 1,
        },
      ],
      (values) => submitReviewAction(frm, "reject", String(values.reason || "").trim()),
      __("Reject Verification"),
      __("Reject")
    );
  }

  function revoke(frm) {
    frappe.confirm(
      __("Revoke this approved verification? The account will no longer be marked as verified."),
      () => submitReviewAction(frm, "revoke")
    );
  }

  function renderReviewButtons(frm) {
    removeReviewButtons(frm);
    const state = stateFor(frm);
    if (frm.is_new() || state.busy || !canWrite(frm)) {
      return;
    }

    const status = String(frm.doc.status || "").trim();
    if (status === "Pending") {
      frm.add_custom_button(__("Start Review"), () => startReview(frm), REVIEW_GROUP);
      frm.add_custom_button(__("Approve"), () => approve(frm), REVIEW_GROUP);
      frm.add_custom_button(__("Reject"), () => reject(frm), REVIEW_GROUP);
      return;
    }
    if (status === "Reviewing") {
      frm.add_custom_button(__("Approve"), () => approve(frm), REVIEW_GROUP);
      frm.add_custom_button(__("Reject"), () => reject(frm), REVIEW_GROUP);
      return;
    }
    if (status === "Approved") {
      frm.add_custom_button(__("Revoke"), () => revoke(frm), REVIEW_GROUP);
    }
  }

  frappe.ui.form.on("AOS Verification Request", {
    refresh(frm) {
      renderReviewButtons(frm);
    },
  });
})();
