import frappe

def _resolve_user_docname(user_id_value: str) -> str | None:
    v = (user_id_value or "").strip()
    if not v:
        return None

    if frappe.db.exists("User", v):
        return v

    user = frappe.db.get_value("User", {"username": v}, "name")
    if user:
        return user

    user = frappe.db.get_value("User", {"email": v}, "name")
    if user:
        return user

    return None


def disable_user_on_employee_left(doc, method=None):
    # Only when new status is Left
    if (doc.status or "").strip() != "Left":
        return

    if not doc.user_id:
        return

    # Detect change BEFORE save
    prev = doc.get_doc_before_save()
    old_status = (prev.status or "").strip() if prev else None
    if old_status == "Left":
        return

    user_docname = _resolve_user_docname(doc.user_id)
    if not user_docname:
        return

    # 1) Disable User (DB update -> no permission issues)
    frappe.db.set_value("User", user_docname, "enabled", 0, update_modified=True)

    # 2) Disable Notification Settings too (DB update -> avoid write permission error)
    # Notification Settings docname is usually the same as user (email)
    if frappe.db.exists("Notification Settings", user_docname):
        frappe.db.set_value("Notification Settings", user_docname, "enabled", 0, update_modified=True)

    # Clear cache
    try:
        frappe.clear_cache(user=user_docname)
    except Exception:
        pass
