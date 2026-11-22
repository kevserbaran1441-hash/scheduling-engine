"""Feasibility checker bilerek bozulmuş çizelgeleri REDDETMELİ."""
import unittest

from scheduling_engine.checker import assert_feasible, check_schedule
from scheduling_engine.decoder import decode_flowshop, decode_parallel
from scheduling_engine.errors import InfeasibleScheduleError
from scheduling_engine.models import ScheduleRow as R
from helpers import flow, parallel, single


class TestCheckerSingleParallel(unittest.TestCase):
    def setUp(self):
        self.inst = parallel([("A", 3, 0, 1, 1), ("B", 2, 2, 1, 1)], 2)

    def test_valid_schedule_passes(self):
        rows = [R("A", "M1", 0, 3), R("B", "M2", 2, 4)]
        self.assertEqual(check_schedule(self.inst, rows), [])

    def test_touching_intervals_are_ok(self):
        inst = single([("A", 3, 0, 1, 1), ("B", 2, 0, 1, 1)])
        self.assertEqual(check_schedule(inst, [R("A", "M1", 0, 3), R("B", "M1", 3, 5)]), [])

    def test_overlap_rejected(self):
        inst = single([("A", 3, 0, 1, 1), ("B", 2, 0, 1, 1)])
        errs = check_schedule(inst, [R("A", "M1", 0, 3), R("B", "M1", 2, 4)])
        self.assertTrue(any("Overlap" in e for e in errs))

    def test_missing_job_rejected(self):
        errs = check_schedule(self.inst, [R("A", "M1", 0, 3)])
        self.assertTrue(any("Job B is missing" in e for e in errs))

    def test_duplicate_job_rejected(self):
        rows = [R("A", "M1", 0, 3), R("A", "M2", 0, 3), R("B", "M2", 3, 5)]
        self.assertTrue(any("2 times" in e for e in check_schedule(self.inst, rows)))

    def test_wrong_duration_rejected(self):
        rows = [R("A", "M1", 0, 4), R("B", "M2", 2, 4)]
        self.assertTrue(any("duration" in e for e in check_schedule(self.inst, rows)))

    def test_invalid_machine_rejected(self):
        rows = [R("A", "M9", 0, 3), R("B", "M2", 2, 4)]
        self.assertTrue(any("Invalid machine" in e for e in check_schedule(self.inst, rows)))

    def test_release_violation_rejected(self):
        rows = [R("A", "M1", 0, 3), R("B", "M2", 1, 3)]       # B için r=2
        self.assertTrue(any("release" in e for e in check_schedule(self.inst, rows)))

    def test_assert_feasible_raises(self):
        with self.assertRaises(InfeasibleScheduleError):
            assert_feasible(self.inst, [R("A", "M1", 0, 3)])


class TestCheckerFlowShop(unittest.TestCase):
    def setUp(self):
        ops = {("A", "M1"): 2, ("A", "M2"): 3, ("B", "M1"): 2, ("B", "M2"): 1}
        self.inst = flow([("A", 0, 9, 1), ("B", 0, 9, 1)], ops, 2)
        self.good = decode_flowshop(self.inst, ["A", "B"])

    def test_decoder_output_is_feasible(self):
        self.assertEqual(check_schedule(self.inst, self.good), [])

    def test_route_violation_rejected(self):
        # A, M2'de M1 bitmeden (t=2) başlıyor
        rows = [R("A", "M1", 0, 2), R("A", "M2", 1, 4), R("B", "M1", 2, 4), R("B", "M2", 4, 5)]
        self.assertTrue(any("route" in e for e in check_schedule(self.inst, rows)))

    def test_common_order_violation_rejected(self):
        # M1'de A,B ; M2'de B,A
        rows = [R("A", "M1", 0, 2), R("B", "M1", 2, 4), R("B", "M2", 4, 5), R("A", "M2", 5, 8)]
        self.assertTrue(any("Common job order" in e for e in check_schedule(self.inst, rows)))

    def test_missing_and_duplicate_operation_rejected(self):
        rows = [r for r in self.good if not (r.job_id == "B" and r.machine_id == "M2")]
        self.assertTrue(any("Missing operation" in e for e in check_schedule(self.inst, rows)))
        rows = self.good + [R("A", "M1", 20, 22)]
        self.assertTrue(any("Duplicate operation" in e for e in check_schedule(self.inst, rows)))

    def test_release_at_m1_rejected(self):
        ops = {("A", "M1"): 2, ("A", "M2"): 3}
        inst = flow([("A", 5, 9, 1)], ops, 2)
        rows = [R("A", "M1", 0, 2), R("A", "M2", 2, 5)]
        self.assertTrue(any("release" in e for e in check_schedule(inst, rows)))


if __name__ == "__main__":
    unittest.main()