import unittest
from decimal import Decimal

from c4factory.c4_manufacturing.continuous_cost_plan import (
	distribute,
	material_adjustments,
	stock_quantity,
)


class TestContinuousCostPlan(unittest.TestCase):
	def test_deliver_without_new_materials_redistributes_previous_cost(self):
		self.assertEqual(
			material_adjustments(10000, [10000, 0], [5, 5]),
			[Decimal("-5000.00"), Decimal("5000.00")],
		)

	def test_additional_material_and_late_workshop_hours_share_all_outputs(self):
		materials = distribute(15000, [5, 4, 2])
		labor = distribute(3300, [5, 4, 2])
		self.assertEqual(sum(materials), Decimal("15000.00"))
		self.assertEqual(labor, [Decimal("1500"), Decimal("1200"), Decimal("600")])
		self.assertEqual(sum(materials) + sum(labor), Decimal("18300.00"))
		# The same inputs always yield the same targets: retries cannot add labor twice.
		self.assertEqual(labor, distribute(3300, [5, 4, 2]))

	def test_rounding_preserves_signed_total(self):
		for total in ("0.01", "100.01", "-100.01", "0"):
			self.assertEqual(sum(distribute(total, [1, 1, 1])), Decimal(total))

	def test_production_incident_residuals_do_not_generate_zero_rows(self):
		for value in ("0.001326998", "0.0006", "0.0008", "0.000776272"):
			self.assertEqual(stock_quantity(value, 2), 0)
		self.assertEqual(stock_quantity("67.08", 2), Decimal("67.08"))

	def test_returns_reduce_cost_without_changing_output_quantity(self):
		self.assertEqual(distribute(9000, [5, 5]), [Decimal("4500"), Decimal("4500")])

	def test_invalid_or_zero_output_is_rejected(self):
		for quantities in ([], [0], [-1], [5, 0]):
			with self.assertRaises(ValueError):
				distribute(100, quantities)
