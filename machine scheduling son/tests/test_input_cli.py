"""Girdi doğrulama, çıktı dosyaları ve Gantt-schedule uyumu."""
import json
import os
import tempfile
import unittest

from scheduling_engine.cli import main
from scheduling_engine.errors import InputError, UnsupportedInputError
from scheduling_engine.io_csv import load_instance, read_schedule_csv
from scheduling_engine.objectives import evaluate

HEADER = "job_id,processing_time,release_time,due_date,weight\n"


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class TestInputValidation(TmpCase):
    def load(self, text, env="parallel", m=2):
        return load_instance(env, self.write("jobs.csv", text), m)

    def test_valid_file_with_defaults(self):
        inst = self.load("job_id,processing_time\nA,3\nB,4\n")
        self.assertEqual([j.release_time for j in inst.jobs], [0, 0])
        self.assertEqual([j.weight for j in inst.jobs], [1, 1])
        self.assertFalse(inst.has_due_dates)          # due_date uydurulmaz

    def test_semicolon_and_job_id_stays_string(self):
        inst = self.load("job_id;processing_time\n007;3\n")
        self.assertEqual(inst.jobs[0].job_id, "007")

    def test_duplicate_job(self):
        with self.assertRaisesRegex(InputError, r"line 3.*job_id.*duplicated"):
            self.load(HEADER + "A,3,0,5,1\nA,4,0,5,1\n")

    def test_invalid_duration_weight_release(self):
        with self.assertRaisesRegex(InputError, r"processing_time"):
            self.load(HEADER + "A,0,0,5,1\n")
        with self.assertRaisesRegex(InputError, r"processing_time"):
            self.load(HEADER + "A,2.5,0,5,1\n")
        with self.assertRaisesRegex(InputError, r"weight"):
            self.load(HEADER + "A,2,0,5,0\n")
        with self.assertRaisesRegex(InputError, r"release_time"):
            self.load(HEADER + "A,2,-1,5,1\n")
        with self.assertRaisesRegex(InputError, r"due_date"):
            self.load(HEADER + "A,2,0,-3,1\n")

    def test_missing_file_column_and_empty(self):
        with self.assertRaisesRegex(InputError, "not found"):
            load_instance("single", os.path.join(self.dir, "yok.csv"), 1)
        with self.assertRaisesRegex(InputError, "missing column"):
            self.load("job_id,weight\nA,1\n")
        with self.assertRaisesRegex(InputError, "no data rows"):
            self.load(HEADER)
        with self.assertRaisesRegex(InputError, "empty"):
            self.load("")

    def test_invalid_machine_count(self):
        for m in (0, -2):
            with self.assertRaisesRegex(InputError, "Machine count"):
                self.load(HEADER + "A,2,0,5,1\n", m=m)
        with self.assertRaisesRegex(InputError, "single"):
            self.load(HEADER + "A,2,0,5,1\n", env="single", m=3)

    def test_flowshop_operation_file_errors(self):
        jobs = self.write("fj.csv", "job_id\nA\nB\n")
        head = "job_id,machine_id,processing_time\n"
        cases = {
            "eksik": head + "A,M1,2\nA,M2,3\nB,M1,2\n",
            "tekrar": head + "A,M1,2\nA,M1,3\nA,M2,3\nB,M1,2\nB,M2,2\n",
            "bilinmeyen iş": head + "Z,M1,2\n",
            "bilinmeyen makine": head + "A,M7,2\n",
            "geçersiz süre": head + "A,M1,0\n",
        }
        for label, text in cases.items():
            ops = self.write("ops.csv", text)
            with self.assertRaises(InputError, msg=label):
                load_instance("flowshop", jobs, 2, ops)
        with self.assertRaisesRegex(InputError, "--operations"):
            load_instance("flowshop", jobs, 2)

    def test_missing_due_date_blocks_due_methods_only(self):
        path = self.write("jobs.csv", "job_id,processing_time\nA,3\nB,4\n")
        out = os.path.join(self.dir, "out")
        base = ["--environment", "single", "--input", path, "--output", out]
        self.assertEqual(main(base + ["--method", "EDD"]), 2)
        self.assertEqual(main(base + ["--method", "SPT", "--objective", "total_tardiness"]), 2)
        self.assertEqual(main(base + ["--method", "SPT", "--objective", "makespan"]), 0)
        with open(os.path.join(out, "metrics.json"), encoding="utf-8") as f:
            self.assertIsNone(json.load(f)["metrics"]["total_tardiness"])


class TestCliEndToEnd(TmpCase):
    def test_outputs_and_gantt_matches_schedule(self):
        jobs = self.write("jobs.csv", HEADER + "A,3,0,4,1\nB,2,0,10,2\nC,4,0,6,1\n")
        out = os.path.join(self.dir, "run")
        code = main(["--environment", "parallel", "--input", jobs, "--machines", "2",
                     "--method", "SPT", "--objective", "total_tardiness", "--output", out,
                     "--seed", "7", "--time-unit", "dakika"])
        self.assertEqual(code, 0)
        for name in ("schedule.csv", "metrics.json", "run_config.json", "gantt.png"):
            self.assertTrue(os.path.getsize(os.path.join(out, name)) > 0, name)
        with open(os.path.join(out, "metrics.json"), encoding="utf-8") as f:
            mj = json.load(f)
        with open(os.path.join(out, "run_config.json"), encoding="utf-8") as f:
            rc = json.load(f)
        self.assertTrue(mj["feasible"])
        self.assertEqual(rc["seed"], 7)
        self.assertGreaterEqual(mj["end_to_end_runtime_s"], mj["algorithm_runtime_s"])
        # metrics.json'daki değerler schedule.csv'den yeniden hesaplananlarla aynı
        inst = load_instance("parallel", jobs, 2)
        self.assertEqual(evaluate(inst, read_schedule_csv(os.path.join(out, "schedule.csv"))),
                         mj["metrics"])

    def test_fixed_sequence_all_environments(self):
        jobs = self.write("jobs.csv", HEADER + "A,3,0,4,1\nB,2,0,10,2\nC,4,0,6,1\n")
        out = os.path.join(self.dir, "o1")
        self.assertEqual(main(["--environment", "single", "--input", jobs, "--method", "FIXED",
                               "--sequence", "C,A,B", "--output", out]), 0)
        self.assertEqual(main(["--environment", "parallel", "--input", jobs, "--machines", "2",
                               "--method", "FIXED", "--machine-lists", "M1:C,A;M2:B",
                               "--output", os.path.join(self.dir, "o2")]), 0)
        self.assertEqual(main(["--environment", "single", "--input", jobs, "--method", "FIXED",
                               "--sequence", "C,A", "--output", out]), 2)   # eksik iş

    def test_flowshop_run_and_config_precedence(self):
        fj = self.write("fj.csv", "job_id,release_time,due_date,weight\nJ1,0,6,1\nJ2,4,9,1\nJ3,0,11,1\n")
        fo = self.write("fo.csv", "job_id,machine_id,processing_time\nJ1,M1,3\nJ1,M2,2\n"
                        "J2,M1,2\nJ2,M2,4\nJ3,M1,4\nJ3,M2,1\n")
        cfg = self.write("cfg.json", json.dumps({
            "environment": "flowshop", "input": fj, "operations": fo, "machines": 2,
            "method": ["EDD"], "objective": "makespan",
            "output": os.path.join(self.dir, "from_cfg")}))
        out2 = os.path.join(self.dir, "from_cli")
        # komut satırı (--objective, --output) config'i ezmeli
        self.assertEqual(main(["--config", cfg, "--objective", "total_tardiness",
                               "--output", out2]), 0)
        with open(os.path.join(out2, "metrics.json"), encoding="utf-8") as f:
            mj = json.load(f)
        self.assertEqual(mj["objective"], "total_tardiness")
        self.assertEqual(mj["metrics"]["makespan"], 11)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "from_cfg")))

    def test_bad_options_rejected_before_solving(self):
        jobs = self.write("jobs.csv", HEADER + "A,3,0,4,1\n")
        base = ["--input", jobs, "--output", os.path.join(self.dir, "x")]
        self.assertEqual(main(base + ["--environment", "single", "--method", "NOPE"]), 2)
        self.assertEqual(main(base + ["--environment", "flowshop", "--method", "WSPT"]), 2)
        self.assertEqual(main(base + ["--environment", "single"]), 2)             # method yok
        bad_cfg = self.write("bad.json", '{"foo": 1}')
        self.assertEqual(main(["--config", bad_cfg]), 2)


if __name__ == "__main__":
    unittest.main()