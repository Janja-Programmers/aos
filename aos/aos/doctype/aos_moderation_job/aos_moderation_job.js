// Staff-only Frappe Desk moderation review controls.
(() => {
  "use strict";
  const METHOD = "aos.api.internal.moderation.review";
  const GROUP = __("Moderation");

  function canReview(frm) {
    return !frm.is_new() && frm.doc.status === "Review Required" && frm.perm && frm.perm[0] && frm.perm[0].write;
  }

  async function decide(frm, decision, reason = "") {
    if (frm.is_dirty()) {
      frappe.msgprint(__("Reload this moderation case before reviewing it."));
      return;
    }
    await frappe.call({
      method: METHOD,
      type: "POST",
      freeze: true,
      args: { job_id: frm.doc.name, decision, reason, version: frm.doc.modified },
    });
    await frm.reload_doc();
  }

  frappe.ui.form.on("AOS Moderation Job", {
    refresh(frm) {
      frm.remove_custom_button(__("Approve"), GROUP);
      frm.remove_custom_button(__("Reject"), GROUP);
      if (!canReview(frm)) return;
      frm.add_custom_button(__("Approve"), () => decide(frm, "approve"), GROUP);
      frm.add_custom_button(__("Reject"), () => {
        frappe.prompt(
          [{ fieldname: "reason", fieldtype: "Small Text", label: __("Rejection reason"), reqd: 1 }],
          (values) => decide(frm, "reject", String(values.reason || "").trim()),
          __("Reject content"),
          __("Reject")
        );
      }, GROUP);
    },
  });
})();
