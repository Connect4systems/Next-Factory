import frappe
from frappe.utils import flt


def execute():
    """Backfill Work Order projects on Stock Entries and refresh Project cost."""
    affected_projects = frappe.db.sql_list(
        """
        SELECT DISTINCT wo.project
        FROM `tabStock Entry` se
        INNER JOIN `tabWork Order` wo ON wo.name = se.work_order
        WHERE COALESCE(wo.project, '') != ''
          AND COALESCE(se.project, '') != wo.project
          AND COALESCE(wo.update_consumed_material_cost_in_project, 0) = 1
        """
    )

    frappe.db.sql(
        """
        UPDATE `tabStock Entry` se
        INNER JOIN `tabWork Order` wo ON wo.name = se.work_order
        SET se.project = wo.project
        WHERE COALESCE(wo.project, '') != ''
          AND COALESCE(se.project, '') != wo.project
        """
    )

    if not affected_projects:
        return

    for project_name in affected_projects:
        material_cost = frappe.db.sql(
            """
            SELECT COALESCE(SUM(sed.amount), 0)
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
            WHERE se.docstatus = 1
              AND se.project = %s
              AND COALESCE(sed.t_warehouse, '') = ''
            """,
            (project_name,),
        )[0][0]
        additional_cost = frappe.db.sql(
            """
            SELECT COALESCE(SUM(cost.base_amount), 0)
            FROM `tabStock Entry` se
            INNER JOIN `tabLanded Cost Taxes and Charges` cost
                ON cost.parent = se.name
            WHERE se.docstatus = 1
              AND se.project = %s
              AND se.purpose = 'Manufacture'
            """,
            (project_name,),
        )[0][0]

        frappe.db.set_value(
            "Project",
            project_name,
            "total_consumed_material_cost",
            flt(material_cost) + flt(additional_cost),
            update_modified=False,
        )
