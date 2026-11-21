"""compute_risk for the account-level services: an enabled one that bills is MEDIUM with
review advice; once it is off, or its state unknown, the usual rules apply."""

import unittest

from .fakes import audit

HINT = "bills_while_enabled"
ENABLED = "ACTIVE (enabled - bills while on)"


class BillsWhileEnabledRiskTests(unittest.TestCase):

    def test_an_enabled_service_is_medium_risk_with_review_advice(self):
        risk = audit.compute_risk(ENABLED, False, HINT)
        self.assertTrue(risk.startswith("MEDIUM - bills while enabled"), risk)

    def test_connections_do_not_raise_it_to_high(self):
        """The hint is checked before "active and wired", which would say HIGH."""
        for kwargs in ({"has_connections": True}, {"has_connections": False, "has_dangling": True}):
            with self.subTest(**kwargs):
                risk = audit.compute_risk(ENABLED, kwargs["has_connections"], HINT,
                                          has_dangling=kwargs.get("has_dangling", False))
                self.assertTrue(risk.startswith("MEDIUM - bills while enabled"), risk)

    def test_the_advice_is_not_given_once_the_service_is_off(self):
        risk = audit.compute_risk("STALE (suspended - not billing)", False, HINT)
        self.assertNotIn("bills while enabled", risk)

    def test_an_unreadable_state_stays_unknown(self):
        for flag in ("UNKNOWN", "ERROR"):
            with self.subTest(flag=flag):
                self.assertTrue(audit.compute_risk(flag, False, HINT).startswith("UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
