from __future__ import annotations
import hashlib
from frappe.model.document import Document
class AOSShortFeedback(Document):
    def before_insert(self):
        parts=[str(getattr(self,'short','') or ''),str(getattr(self,'media','') or getattr(self,'mode','') or getattr(self,'hashtag','') or getattr(self,'ad','') or getattr(self,'user','') or ''),str(getattr(self,'feedback_type','') or '')]
        if hasattr(self,'unique_key'): self.unique_key=hashlib.sha256('|'.join(parts).encode()).hexdigest()
