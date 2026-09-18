from frappe.model.document import Document

from aos.services.marketplace_discovery.ids import ensure_public_id
from aos.utils.identifiers import new_prefixed_name


class AOSAdDraft(Document):
    def autoname(self):
        self.name = new_prefixed_name("DRAFT")

    def before_insert(self):
        ensure_public_id(self)
