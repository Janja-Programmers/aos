from __future__ import annotations
import hashlib, frappe
from frappe.model.document import Document
class AOSShortMention(Document):
    def before_insert(self):
        if not self.mentioned_by: self.mentioned_by=frappe.session.user
        self.unique_key=hashlib.sha256(f'{self.short}|{self.comment or ""}|{self.mentioned_account}|{self.source_type}'.encode()).hexdigest()
    def validate(self):
        if self.source_type not in {'caption','comment','reply'}: frappe.throw('Invalid mention source type')
        if not frappe.db.exists('AOS Profile',self.mentioned_account): frappe.throw('Invalid mentioned account')
        if self.comment and frappe.db.get_value('AOS Short Comment',self.comment,'short')!=self.short: frappe.throw('Invalid mention comment')
