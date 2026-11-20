import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.reconciliation import (  # noqa: E402
    InputError,
    compute,
    parse_reported_yield,
    reported_interval,
    required_passed,
)


def items(*rows):
    return [
        {"batch": batch, "line": line, "fired": fired, "reported_yield": y}
        for batch, line, fired, y in rows
    ]


class ReportedYieldTest(unittest.TestCase):
    def test_parses_one_decimal(self):
        self.assertEqual(parse_reported_yield(92.3), (923, "92.3"))
        self.assertEqual(parse_reported_yield("100"), (1000, "100.0"))
        self.assertEqual(parse_reported_yield(0), (0, "0.0"))

    def test_rejects_extra_precision(self):
        for value in (92.35, "92.35", 92.30001):
            with self.assertRaises(InputError):
                parse_reported_yield(value)

    def test_rejects_out_of_range_and_bool(self):
        for value in (-0.1, 100.1, True, None, "abc", "nan", "inf"):
            with self.assertRaises(InputError):
                parse_reported_yield(value)

    def test_interval_is_inclusive_and_half_up(self):
        # 2,000 件、93.0% -> 1859（92.95%，半进位到 93.0）与 1860 两件都成立
        self.assertEqual(reported_interval(2000, 930), (1859, 1860))
        self.assertEqual(reported_interval(400, 923), (369, 369))
        self.assertEqual(reported_interval(100, 950), (95, 95))
        self.assertEqual(reported_interval(3, 1000), (3, 3))

    def test_unachievable_yield_has_empty_interval(self):
        # 200 件跳到 0.5% 一格，92.3% 没有对应整数件数
        low, high = reported_interval(200, 923)
        self.assertGreater(low, high)

    def test_interval_never_exceeds_bounds(self):
        low, high = reported_interval(7, 1000)
        self.assertLessEqual(low, high)
        self.assertGreaterEqual(low, 0)
        self.assertLessEqual(high, 7)

    def test_required_passed_rounds_up(self):
        self.assertEqual(required_passed(200, 93), 186)
        self.assertEqual(required_passed(150, 93), 140)
        self.assertEqual(required_passed(1000, 92.5), 925)


class TrialTest(unittest.TestCase):
    def test_trial_without_ledger_never_approvable(self):
        result = compute(items(("W-1", "A", 100, 93.0), ("W-2", "A", 200, 90.0)), target=93)
        self.assertEqual(result["phase"], "trial")
        self.assertIsNone(result["feasible"])
        self.assertFalse(result["can_approve"])
        self.assertTrue(any("总账" in reason for reason in result["blocking_reasons"]))

    def test_trial_reports_individual_ranges(self):
        result = compute(items(("W-1", "A", 2000, 93.0)), target=93)
        row = result["items"][0]
        self.assertEqual((row["reported_min"], row["reported_max"]), (1859, 1860))
        self.assertEqual(row["verdict"], "待核")
        self.assertEqual(row["required_passed"], 1860)

    def test_trial_can_mark_clearly_below_target(self):
        result = compute(items(("W-1", "A", 200, 80.0)), target=93)
        self.assertEqual(result["items"][0]["verdict"], "未达标")


class LedgerTest(unittest.TestCase):
    def base(self):
        return items(
            ("W-1", "A", 100, 93.0),
            ("W-2", "A", 200, 90.0),
            ("W-3", "B", 150, 93.3),
        )

    def test_feasible_ledger_constrains_each_kiln(self):
        result = compute(self.base(), ledger_total=413, target=93)
        self.assertTrue(result["feasible"])
        self.assertTrue(result["can_approve"])
        self.assertEqual([r["constrained_min"] for r in result["items"]], [93, 180, 140])
        self.assertEqual([r["constrained_max"] for r in result["items"]], [93, 180, 140])
        verdicts = {r["batch"]: r["verdict"] for r in result["items"]}
        self.assertEqual(verdicts, {"W-1": "达标", "W-2": "未达标", "W-3": "达标"})

    def test_ledger_below_minimum_is_infeasible_with_explanation(self):
        result = compute(self.base(), ledger_total=390, target=93)
        self.assertFalse(result["feasible"])
        self.assertFalse(result["can_approve"])
        self.assertIn("390", result["total"]["note"])
        self.assertIn("413", result["total"]["note"])

    def test_ledger_above_maximum_is_infeasible(self):
        result = compute(self.base(), ledger_total=450, target=93)
        self.assertFalse(result["feasible"])
        self.assertFalse(result["can_approve"])

    def test_ledger_above_total_fired_is_infeasible(self):
        result = compute(self.base(), ledger_total=451, target=93)
        self.assertFalse(result["feasible"])
        self.assertIn("烧成件数", result["total"]["note"])

    def test_wide_interval_stays_undetermined_under_full_constraint(self):
        rows = items(("W-1", "A", 2000, 93.0), ("W-2", "A", 2000, 93.0))
        result = compute(rows, ledger_total=3719, target=93)
        self.assertTrue(result["feasible"])
        self.assertFalse(result["can_approve"])
        undetermined = [r for r in result["items"] if r["verdict"] == "待核"]
        self.assertEqual(len(undetermined), 2)
        for row in undetermined:
            self.assertLess(row["constrained_min"], row["required_passed"])
            self.assertGreaterEqual(row["constrained_max"], row["required_passed"])
            self.assertIn("跨越达标线", row["reason"])


class SupplyTest(unittest.TestCase):
    def base(self):
        return items(
            ("W-1", "A", 2000, 93.0),
            ("W-2", "A", 2000, 92.0),
            ("W-3", "A", 2000, 95.0),
        )

    def test_supplied_value_replaces_single_kiln_estimate(self):
        rows = self.base()
        rows[0]["supplied_passed"] = 1859
        result = compute(rows, ledger_total=5599, target=93)
        row = result["items"][0]
        self.assertEqual((row["constrained_min"], row["constrained_max"]), (1859, 1859))
        self.assertEqual(row["verdict"], "未达标")
        self.assertTrue(result["can_approve"])

    def test_supplied_value_must_match_reported_yield(self):
        rows = self.base()
        rows[0]["supplied_passed"] = 1840
        result = compute(rows, ledger_total=5580, target=93)
        self.assertFalse(result["can_approve"])
        self.assertEqual(result["items"][0]["verdict"], "待核")
        self.assertIn("不一致", result["items"][0]["reason"])

    def test_supplied_value_cannot_exceed_fired(self):
        rows = self.base()
        rows[0]["supplied_passed"] = 2001
        result = compute(rows, ledger_total=5741, target=93)
        self.assertIn("超过烧成件数", result["items"][0]["reason"])

    def test_supplied_value_shifts_ledger_feasibility(self):
        rows = self.base()
        rows[0]["supplied_passed"] = 1859
        # 补报前总账 5600 有解；补报后各窑上限合计 5599，总账对不上
        result = compute(rows, ledger_total=5600, target=93)
        self.assertFalse(result["feasible"])
        self.assertFalse(result["can_approve"])
        # 补报实数的那一窑仍有确定结论；未补报的窑因整批推算失效而待核
        verdicts = {r["batch"]: r["verdict"] for r in result["items"]}
        self.assertEqual(verdicts["W-1"], "未达标")
        self.assertEqual(verdicts["W-2"], "待核")
        self.assertIn("整批推算失效", result["items"][1]["reason"])


class ValidationTest(unittest.TestCase):
    def test_duplicate_batch_rejected(self):
        with self.assertRaises(InputError):
            compute(items(("W-1", "A", 100, 90.0), ("W-1", "B", 100, 91.0)))

    def test_missing_batch_rejected(self):
        with self.assertRaises(InputError):
            compute([{"fired": 100, "reported_yield": 90.0}])

    def test_non_integer_fired_rejected(self):
        with self.assertRaises(InputError):
            compute([{"batch": "W-1", "fired": 100.5, "reported_yield": 90.0}])

    def test_zero_fired_rejected(self):
        with self.assertRaises(InputError):
            compute([{"batch": "W-1", "fired": 0, "reported_yield": 90.0}])

    def test_empty_items_rejected(self):
        with self.assertRaises(InputError):
            compute([], ledger_total=100)

    def test_bad_target_rejected(self):
        with self.assertRaises(InputError):
            compute(items(("W-1", "A", 100, 90.0)), target=0)

    def test_unrepresentable_report_is_flagged_not_silent(self):
        result = compute(items(("W-1", "A", 200, 92.3)), ledger_total=185, target=93)
        row = result["items"][0]
        self.assertEqual(row["verdict"], "待核")
        self.assertIn("无法对应", row["reason"])


if __name__ == "__main__":
    unittest.main()


class NeedsInputTest(unittest.TestCase):
    def test_unrepresentable_report_blocks_batch_totals(self):
        result = compute(
            [
                {"batch": "W-1", "fired": 2000, "reported_yield": 93.0},
                {"batch": "W-2", "fired": 200, "reported_yield": 92.3},
            ],
            ledger_total=2000,
            target=93,
        )
        self.assertEqual(result["phase"], "needs_input")
        self.assertFalse(result["feasible"])
        self.assertFalse(result["can_approve"])
        self.assertIsNone(result["total"]["min"])
        self.assertIsNone(result["total"]["max"])
        bad = next(r for r in result["items"] if r["batch"] == "W-2")
        self.assertEqual(bad["verdict"], "待核")
        self.assertIn("无法对应", bad["reason"])
        ok = next(r for r in result["items"] if r["batch"] == "W-1")
        self.assertEqual(ok["verdict"], "待核")

    def test_supplied_value_can_rescue_unrepresentable_report(self):
        result = compute(
            [{"batch": "W-2", "fired": 200, "reported_yield": 92.3, "supplied_passed": 185}],
            ledger_total=185,
            target=93,
        )
        # 补报实数后总账合计可算，但上报值本身仍无法解析，必须人工核对上报源。
        self.assertEqual(result["phase"], "needs_input")
        self.assertFalse(result["can_approve"])
        row = result["items"][0]
        self.assertEqual(row["constrained_min"], 185)
        self.assertEqual(row["verdict"], "待核")
        self.assertIn("无法对应", row["reason"])
