from __future__ import annotations
import frappe
from frappe.model.document import Document
from aos.services.shorts.policy import can_view
class AOSShortCommentLike(Document):
    def before_insert(self):
        if not self.user: self.user=frappe.session.user
        if not self.user or self.user=='Guest': frappe.throw('Authentication required')
    def validate(self):
        c=frappe.db.get_value('AOS Short Comment',self.comment,['short','status'],as_dict=True)
        if not c or c.status!='active': frappe.throw('Comment unavailable')
        s=frappe.db.get_value('AOS Short',c.short,['name','owner','lifecycle_status','processing_status','moderation_status','audience'],as_dict=True)
        if not s or not can_view(s,viewer=self.user): frappe.throw('Short unavailable')
    def after_insert(self): frappe.db.sql('UPDATE `tabAOS Short Comment` SET like_count=COALESCE(like_count,0)+1 WHERE name=%s',(self.comment,))
    def on_trash(self): frappe.db.sql('UPDATE `tabAOS Short Comment` SET like_count=GREATEST(COALESCE(like_count,0)-1,0) WHERE name=%s',(self.comment,))
