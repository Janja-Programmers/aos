from frappe.model.document import Document
from aos.services.marketplace_discovery.ids import ensure_public_id

class AOSAdDraft(Document):
    def before_insert(self):
        ensure_public_id(self)
