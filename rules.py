from models import Customer, Decision


RULES_TEXT = """
RETURN DESK DECISION RULES
===========================

Evaluate customers in this exact order. Stop at the first rule that triggers.

RULE 1 — Photo Gate:
  If the receipt PDF does NOT contain the customer's photo → DENY

RULE 2 — Loyalty Override:
  If the customer's loyalty tier is "GOLD" → ACCEPT
  ⚠ The loyalty tier is NOT in any of the 3 files.
  Look it up by customer_id in BigQuery:
    SELECT loyalty_tier
    FROM prod-peach-street.analytics_poc.return_desk_customers
    WHERE customer_id = '...'

RULE 3 — Fraud Override:
  If fraud report says "not malicious" AND spending potential is "high" → ACCEPT
  (This overrides Rules 4-6)

RULE 4 — Return Rate:
  If return rate > 20% → DENY
  (But check Rule 5 first — it's an exception)

RULE 5 — Dollar Amount Exception (to Rule 4):
  If total return dollar amount < 80% of total purchase amount → ACCEPT

RULE 6 — Default:
  If none of the above triggered → DENY

Evaluation order: Rule 1 → Rule 2 → Rule 3 → Rule 4 (with Rule 5 exception) → Rule 6
""".strip()


def evaluate(customer: Customer) -> tuple[Decision, str]:
    """Evaluate a customer and return (decision, explanation)."""

    # Rule 1: Photo Gate
    if not customer.photo_included:
        return Decision.DENY, "Rule 1: Receipt does not contain customer photo → DENY"

    # Rule 2: Loyalty Override
    if customer.loyalty_tier == "GOLD":
        return Decision.ACCEPT, "Rule 2: Customer loyalty tier is GOLD → ACCEPT"

    # Rule 3: Fraud Override
    if customer.fraud_assessment == "not_malicious" and customer.spending_potential == "high":
        return Decision.ACCEPT, "Rule 3: Fraud report is 'not malicious' and spending potential is 'high' → ACCEPT"

    # Rule 4 + Rule 5: Return Rate with Dollar Exception
    if customer.return_rate > 0.20:
        # Check Rule 5 exception
        if customer.total_return_amount < 0.80 * customer.total_purchase_amount:
            return Decision.ACCEPT, "Rule 5: Return rate > 20% but return dollar amount < 80% of purchase amount → ACCEPT (exception to Rule 4)"
        return Decision.DENY, "Rule 4: Return rate > 20% and return dollars ≥ 80% of purchase dollars → DENY"

    # Rule 5 standalone (return rate ≤ 20%)
    if customer.total_return_amount < 0.80 * customer.total_purchase_amount:
        return Decision.ACCEPT, "Rule 5: Return dollar amount < 80% of purchase amount → ACCEPT"

    # Rule 6: Default
    return Decision.DENY, "Rule 6: No qualifying rule triggered → DENY"
