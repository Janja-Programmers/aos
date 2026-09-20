from __future__ import annotations
import frappe
from frappe.model.document import Document
from aos.services.shorts.identity import generate_comment_id
from aos.services.shorts.policy import can_comment

class AOSShortComment(Document):
    def autoname(self): self.name=generate_comment_id()
    def before_insert(self):
        if not self.user: self.user=frappe.session.user
        self.status=self.status or 'active'
        if self.parent_comment:
            parent=frappe.db.get_value('AOS Short Comment',self.parent_comment,['short','root_comment','status'],as_dict=True)
            if not parent or parent.short!=self.short or parent.status!='active': frappe.throw('Invalid parent comment')
            self.root_comment=parent.root_comment or self.parent_comment
    def validate(self):
        if not self.user or self.user=='Guest': frappe.throw('Login required to comment')
        body=str(self.comment or '').strip()
        if not body or len(body)>500: frappe.throw('Invalid comment')
        self.comment=body
        short=frappe.db.get_value('AOS Short',self.short,['name','owner','lifecycle_status','processing_status','moderation_status','audience','allow_comments'],as_dict=True)
        if not short or not can_comment(short,viewer=self.user): frappe.throw('Commenting is not allowed')
    def after_insert(self):
        if not self.parent_comment: frappe.db.set_value(self.doctype,self.name,'root_comment',self.name,update_modified=False)
        frappe.db.sql('UPDATE `tabAOS Short` SET comment_count=COALESCE(comment_count,0)+1,last_engagement_at=NOW() WHERE name=%s',(self.short,))
        if self.parent_comment: frappe.db.sql('UPDATE `tabAOS Short Comment` SET reply_count=COALESCE(reply_count,0)+1 WHERE name=%s',(self.root_comment or self.parent_comment,))
    def soft_delete(self):
        if self.status=='deleted': return
        self.db_set('status','deleted',update_modified=False)
        frappe.db.sql('UPDATE `tabAOS Short` SET comment_count=GREATEST(COALESCE(comment_count,0)-1,0) WHERE name=%s',(self.short,))
        if self.parent_comment: frappe.db.sql('UPDATE `tabAOS Short Comment` SET reply_count=GREATEST(COALESCE(reply_count,0)-1,0) WHERE name=%s',(self.root_comment or self.parent_comment,))
