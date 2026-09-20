from __future__ import annotations
import frappe
from frappe.model.document import Document
from aos.services.shorts.policy import can_view

class AOSShortLike(Document):
    counter_field='like_count'
    def before_insert(self):
        if not self.user: self.user=frappe.session.user
        if not self.user or self.user=='Guest': frappe.throw('Authentication required')
    def validate(self):
        short=frappe.db.get_value('AOS Short',self.short,['name','owner','lifecycle_status','processing_status','moderation_status','audience'],as_dict=True)
        if not short or not can_view(short,viewer=self.user): frappe.throw('Short is unavailable')
    def after_insert(self):
        frappe.db.sql(f'UPDATE `tabAOS Short` SET {self.counter_field}=COALESCE({self.counter_field},0)+1,last_engagement_at=NOW() WHERE name=%s',(self.short,))
    def on_trash(self):
        frappe.db.sql(f'UPDATE `tabAOS Short` SET {self.counter_field}=GREATEST(COALESCE({self.counter_field},0)-1,0) WHERE name=%s',(self.short,))
