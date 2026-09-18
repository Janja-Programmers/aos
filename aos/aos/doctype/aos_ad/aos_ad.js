// Copyright (c) 2026, Africa Online Stores and contributors
// For license information, please see license.txt

(() => {
  "use strict";

  const REVIEW_API = "aos.api.v1.ads.review_ad";
  const REVIEW_GROUP = __("Review");
  const APPROVE_STATUSES = new Set(["Reviewing", "Declined"]);
  const REJECT_STATUSES = new Set(["Reviewing", "Active"]);
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

  function unwrap(response, fallback) {
    const payload = response && Object.prototype.hasOwnProperty.call(response, "message")
      ? response.message
      : response;
    if (!payload || payload.ok !== true) {
      throw new Error((payload && payload.message) || fallback);
    }
    return payload.data || {};
  }

  function errorMessage(error, fallback) {
    const responseMessage = error && error.responseJSON && error.responseJSON.message;
    if (responseMessage && typeof responseMessage === "object" && responseMessage.message) {
      return String(responseMessage.message);
    }
    return String((error && error.message) || fallback);
  }

  function removeReviewButtons(frm) {
    frm.remove_custom_button(__("Approve Ad"), REVIEW_GROUP);
    frm.remove_custom_button(__("Reject Ad"), REVIEW_GROUP);
  }

  function ensureSavedState(frm) {
    if (!frm.is_dirty()) {
      return true;
    }
    frappe.msgprint({
      title: __("Unsaved changes"),
      message: __("Save or reload this Ad before reviewing it."),
      indicator: "orange",
    });
    return false;
  }

  async function submitReview(frm, decision, reason = "") {
    const state = stateFor(frm);
    if (state.busy || !ensureSavedState(frm)) {
      return;
    }

    const publicId = String(frm.doc.public_id || "").trim();
    const version = String(frm.doc.modified || "").trim();
    if (!publicId || !version) {
      frappe.msgprint({
        title: __("Review unavailable"),
        message: __("Reload this Ad before reviewing it."),
        indicator: "orange",
      });
      return;
    }

    state.busy = true;
    removeReviewButtons(frm);
    try {
      const response = await frappe.call({
        method: REVIEW_API,
        type: "POST",
        freeze: true,
        freeze_message: decision === "approve" ? __("Approving Ad...") : __("Rejecting Ad..."),
        args: {
          ad_id: publicId,
          decision,
          reason,
          version,
        },
      });
      const data = unwrap(response, __("The Ad could not be reviewed."));
      await frm.reload_doc();
      frappe.show_alert({
        message: decision === "approve" ? __("Ad approved.") : __("Ad rejected."),
        indicator: "green",
      });
      return data;
    } catch (error) {
      frappe.msgprint({
        title: __("Review failed"),
        message: errorMessage(error, __("The Ad could not be reviewed.")),
        indicator: "red",
      });
    } finally {
      state.busy = false;
      renderReviewButtons(frm);
    }
  }

  function approveAd(frm) {
    if (!ensureSavedState(frm)) {
      return;
    }
    frappe.confirm(
      __("Approve this Ad and make it active in the marketplace?"),
      () => submitReview(frm, "approve")
    );
  }

  function rejectAd(frm) {
    if (!ensureSavedState(frm)) {
      return;
    }
    frappe.prompt(
      [
        {
          fieldname: "reason",
          fieldtype: "Small Text",
          label: __("Rejection reason"),
          reqd: 1,
        },
      ],
      (values) => submitReview(frm, "reject", String(values.reason || "").trim()),
      __("Reject Ad"),
      __("Reject")
    );
  }

  function renderReviewButtons(frm) {
    removeReviewButtons(frm);
    const state = stateFor(frm);
    if (frm.is_new() || state.busy || !canWrite(frm)) {
      return;
    }

    const status = String(frm.doc.status || "").trim();
    if (APPROVE_STATUSES.has(status)) {
      frm.add_custom_button(__("Approve Ad"), () => approveAd(frm), REVIEW_GROUP);
    }
    if (REJECT_STATUSES.has(status)) {
      frm.add_custom_button(__("Reject Ad"), () => rejectAd(frm), REVIEW_GROUP);
    }
  }

  frappe.ui.form.on("AOS Ad", {
    refresh(frm) {
      renderReviewButtons(frm);
    },
  });
})();
