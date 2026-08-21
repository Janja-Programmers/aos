__version__ = "2.0.0"

# Install secret-safe traceback/telemetry rendering as early as the app is imported.
# Request/job hooks retry this idempotently if Frappe is not fully initialized yet.
from aos.utils.secure_logging import install_frappe_traceback_redaction as _install_secure_logging

_install_secure_logging()
del _install_secure_logging
