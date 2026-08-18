from unittest.mock import MagicMock, call, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4_manufacturing.work_order_hooks import attach_public_bom_files


class TestWorkOrderBOMAttachments(FrappeTestCase):
    def test_attaches_unique_public_files_from_product_and_mold_boms(self):
        work_order = frappe._dict(
            {
                "name": "WO-0001",
                "bom_no": "BOM-PRODUCT-001",
                "custom_mold_bom_no": "MLD-001",
            }
        )
        product_file = MagicMock()
        mold_file = MagicMock()
        missing_file = MagicMock()
        for file_doc in (product_file, mold_file, missing_file):
            file_doc.is_remote_file = False
            file_doc.exists_on_disk.return_value = True
        missing_file.exists_on_disk.return_value = False
        missing_file.name = "FILE-MISSING"
        missing_file.file_url = "/files/missing.pdf"

        def get_all(_doctype, filters=None, **kwargs):
            if filters["attached_to_doctype"] == "Work Order":
                return ["/files/existing.pdf"]
            if filters["attached_to_name"] == "BOM-PRODUCT-001":
                return [
                    frappe._dict({"name": "FILE-PRODUCT", "file_url": "/files/product.pdf"}),
                    frappe._dict({"name": "FILE-EXISTING", "file_url": "/files/existing.pdf"}),
                    frappe._dict({"name": "FILE-MISSING", "file_url": "/files/missing.pdf"}),
                ]
            return [
                frappe._dict({"name": "FILE-DUPLICATE", "file_url": "/files/product.pdf"}),
                frappe._dict({"name": "FILE-MOLD", "file_url": "/files/mold.pdf"}),
            ]

        def get_doc(_doctype, name):
            return {
                "FILE-PRODUCT": product_file,
                "FILE-MOLD": mold_file,
                "FILE-MISSING": missing_file,
            }[name]

        with (
            patch(
                "c4factory.c4_manufacturing.work_order_hooks.frappe.get_all",
                side_effect=get_all,
            ) as get_all_mock,
            patch(
                "c4factory.c4_manufacturing.work_order_hooks.frappe.get_doc",
                side_effect=get_doc,
            ),
        ):
            attached_count = attach_public_bom_files(work_order)

        self.assertEqual(attached_count, 2)
        product_file.create_attachment_copy.assert_called_once_with(
            attached_to_doctype="Work Order",
            attached_to_name="WO-0001",
            ignore_permissions=True,
        )
        mold_file.create_attachment_copy.assert_called_once_with(
            attached_to_doctype="Work Order",
            attached_to_name="WO-0001",
            ignore_permissions=True,
        )
        missing_file.create_attachment_copy.assert_not_called()
        self.assertEqual(get_all_mock.call_count, 3)
        self.assertEqual(
            get_all_mock.call_args_list[1:],
            [
                call(
                    "File",
                    filters={
                        "attached_to_doctype": "BOM",
                        "attached_to_name": "BOM-PRODUCT-001",
                        "is_private": 0,
                    },
                    fields=["name", "file_url"],
                    order_by="creation asc",
                ),
                call(
                    "File",
                    filters={
                        "attached_to_doctype": "BOM",
                        "attached_to_name": "MLD-001",
                        "is_private": 0,
                    },
                    fields=["name", "file_url"],
                    order_by="creation asc",
                ),
            ],
        )

    @patch("c4factory.c4_manufacturing.work_order_hooks.frappe.get_all")
    def test_does_nothing_when_work_order_has_no_bom(self, get_all):
        attached_count = attach_public_bom_files(
            frappe._dict({"name": "WO-0002", "bom_no": None, "custom_mold_bom_no": None})
        )

        self.assertEqual(attached_count, 0)
        get_all.assert_not_called()
