import unittest

from audit.economics import evaluate, net_weekly_minutes


class EconomicsTests(unittest.TestCase):
    def setUp(self):
        self.c = {
            "id": "test", "name_zh": "synthetic fixture",
            "checks_per_week": {"low": 0, "central": 10, "high": 20},
            "automation_fraction": {"low": 0.5, "central": 0.5, "high": 1},
            "manual_minutes_per_check": 5, "review_minutes_per_check": 2,
            "maintenance_minutes_per_week": 3, "exception_minutes_per_week": 2,
            "build_minutes": {"central": 60}, "validation_gate": "fixture",
        }

    def test_partial_coverage_review_and_costs(self):
        self.assertEqual(net_weekly_minutes(self.c, "central"), 10)
        result = evaluate(self.c)
        self.assertEqual(result["central_payback_weeks"], 6)
        self.assertEqual(result["central_annual_net_hours_after_build"], 7.67)
        self.assertEqual(result["net_minutes_per_week"]["low"], -5)

    def test_negative_savings_do_not_get_a_payback(self):
        self.c["review_minutes_per_check"] = 8
        self.assertIsNone(evaluate(self.c)["central_payback_weeks"])

    def test_coverage_and_numeric_validation(self):
        for value in (1.2, -0.2, float("nan"), True):
            with self.subTest(value=value):
                self.c["automation_fraction"]["central"] = value
                with self.assertRaises(ValueError):
                    evaluate(self.c)

    def test_zero_coverage_never_claims_savings(self):
        self.c["automation_fraction"]["central"] = 0
        result = evaluate(self.c)
        self.assertEqual(result["net_minutes_per_week"]["central"], -5)
        self.assertIsNone(result["weekly_checks_to_break_even_within_12_weeks"])


if __name__ == "__main__":
    unittest.main()
