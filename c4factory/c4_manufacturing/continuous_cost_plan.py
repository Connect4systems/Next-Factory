"""Deterministic cost allocation, independent of Frappe and database state."""

from decimal import ROUND_HALF_UP, Decimal


def decimal(value):
	return Decimal(str(value or 0))


def distribute(total, quantities, precision=2):
	"""Allocate a signed amount by output quantity; preserve the exact rounded total."""
	quantities = [decimal(qty) for qty in quantities]
	if not quantities or any(qty <= 0 for qty in quantities):
		raise ValueError("Every finished quantity must be positive")
	unit = Decimal(1).scaleb(-precision)
	total = decimal(total).quantize(unit, rounding=ROUND_HALF_UP)
	denominator = sum(quantities)
	# Round cumulative totals so the last row only absorbs rounding, not a new cost.
	result, cumulative, allocated = [], Decimal(0), Decimal(0)
	for qty in quantities:
		cumulative += qty
		target = (total * cumulative / denominator).quantize(unit, rounding=ROUND_HALF_UP)
		result.append(target - allocated)
		allocated = target
	return result


def material_adjustments(total_material_cost, batch_material_costs, quantities, precision=2):
	"""Differences move cost between receipts, without consuming materials twice."""
	if len(batch_material_costs) != len(quantities):
		raise ValueError("Material costs and finished quantities must match")
	targets = distribute(total_material_cost, quantities, precision)
	return [target - decimal(actual) for target, actual in zip(targets, batch_material_costs, strict=True)]


def stock_quantity(value, precision):
	"""Never emit a Stock Entry row whose stock quantity would round to zero."""
	return decimal(value).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
