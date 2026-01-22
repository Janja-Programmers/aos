def ok(message: str, data: dict | None = None):
    out = {"ok": True, "message": message}
    if data is not None:
        out["data"] = data
    return out


def fail(message: str, code: str | None = None, data: dict | None = None):
    out = {"ok": False, "message": message}
    if code:
        out["code"] = code
    if data is not None:
        out["data"] = data
    return out
