import frappe


UNIQUE_CONSTRAINTS = [
    {
        "doctype": "AOS Follow",
        "fields": ["follower_user", "following_user"],
        "constraint_name": "unique_aos_follow_pair",
    },
    {
        "doctype": "AOS Message Star",
        "fields": ["message", "user"],
        "constraint_name": "unique_aos_message_star_user",
    },
    {
        "doctype": "AOS Message Reaction",
        "fields": ["message", "user"],
        "constraint_name": "unique_aos_message_reaction_user",
    },

    # Add more DocTypes here:
    # {
    #     "doctype": "AOS Like",
    #     "fields": ["user", "post"],
    #     "constraint_name": "unique_aos_like_user_post",
    # },
]


def execute():
    for constraint in UNIQUE_CONSTRAINTS:
        doctype = constraint["doctype"]
        fields = constraint["fields"]
        constraint_name = constraint["constraint_name"]

        if not frappe.db.exists("DocType", doctype):
            frappe.log_error(
                title="Unique Constraint Patch Skipped",
                message=f"DocType {doctype} does not exist. Skipping.",
            )
            continue

        _validate_fields_exist(doctype, fields)

        if _unique_index_exists(doctype, constraint_name):
            continue

        _throw_if_duplicates_exist(doctype, fields)

        frappe.db.add_unique(
            doctype,
            fields,
            constraint_name=constraint_name,
        )


def _validate_fields_exist(doctype: str, fields: list[str]) -> None:
    meta = frappe.get_meta(doctype)
    valid_fields = {field.fieldname for field in meta.fields}
    valid_fields.add("name")

    missing_fields = [field for field in fields if field not in valid_fields]

    if missing_fields:
        frappe.throw(
            f"Cannot add unique constraint on {doctype}. "
            f"These fields do not exist: {', '.join(missing_fields)}"
        )


def _unique_index_exists(doctype: str, constraint_name: str) -> bool:
    table_name = f"tab{doctype}"

    existing = frappe.db.sql(
        """
        SELECT INDEX_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
          AND NON_UNIQUE = 0
        LIMIT 1
        """,
        (table_name, constraint_name),
        as_dict=True,
    )

    return bool(existing)


def _throw_if_duplicates_exist(doctype: str, fields: list[str]) -> None:
    table = f"tab{doctype}"
    field_sql = ", ".join(f"`{field}`" for field in fields)

    duplicates = frappe.db.sql(
        f"""
        SELECT {field_sql}, COUNT(*) AS duplicate_count
        FROM `{table}`
        GROUP BY {field_sql}
        HAVING COUNT(*) > 1
        LIMIT 10
        """,
        as_dict=True,
    )

    if not duplicates:
        return

    duplicate_summary = "\n".join(
        f"- {', '.join(f'{field}={row.get(field)}' for field in fields)} "
        f"duplicate_count={row.get('duplicate_count')}"
        for row in duplicates
    )

    frappe.throw(
        f"Cannot add unique constraint on {doctype} for fields "
        f"{', '.join(fields)} because duplicate records already exist.\n\n"
        f"Duplicate examples:\n{duplicate_summary}"
    )
