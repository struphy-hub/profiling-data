"""Integration checks for report exports using small, known HDF5 profiles."""

import json
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np
from scope_profiler import read_h5

from analysis import scaling_axes, write_analysis


def profile(path, ranks, threads=1, nodes=None):
    with h5py.File(path, "w") as h5:
        metadata = h5.create_group("metadata")
        metadata.attrs["label"] = path.stem
        metadata.attrs["omp_num_threads"] = threads
        if nodes is not None:
            metadata.attrs["num_nodes"] = nodes
        for rank in range(ranks):
            regions = h5.create_group(f"rank{rank}/regions")
            for name, start, end in [("root", 0, 10 + 4 * rank), ("child", 1, 4 + 2 * rank)]:
                region = regions.create_group(name)
                region["start_times"] = np.array([start * 10**9], dtype=np.int64)
                region["end_times"] = np.array([end * 10**9], dtype=np.int64)
    return read_h5(path)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def export(self, readers, work=None):
        path = write_analysis(readers, self.root / "out", self.root, case_files=work)
        return json.loads((self.root / path).read_text())

    def payload(self, manifest, kind):
        chart = next(chart for chart in manifest["charts"] if chart["kind"] == kind)
        return json.loads((self.root / chart["path"]).read_text())

    def test_rank_views_use_all_ranks_and_own_time(self):
        data = self.export([profile(self.root / "mpi.h5", ranks=2)])
        self.assertFalse(data["unavailable"])
        stats = data["files"][0]["region_statistics"]
        self.assertAlmostEqual(stats["root"]["total_duration_seconds"], 24)
        self.assertEqual(set(stats["root"]["per_rank"]), {"0", "1"})
        regions = {region["name"]: region for region in data["regions"]}
        self.assertEqual(regions["root"]["depth"], 0)
        self.assertEqual(regions["child"]["depth"], 1)
        heatmap = self.payload(data, "rank_heatmap")
        root_points = {p["rank"]: p["exclusive_duration_seconds"] for p in heatmap["points"] if p["region"] == "root"}
        self.assertEqual(root_points, {0: 7, 1: 9})
        timeline = self.payload(data, "gantt")
        self.assertEqual({item["rank"] for item in timeline["intervals"]}, {0})
        stacked = next(chart for chart in data["charts"] if chart["id"] == "stacked-durations")
        bars = json.loads((self.root / stacked["path"]).read_text())["bars"]
        total_root = sum(bar["value_seconds"] for bar in bars if bar["region"] == "root" and bar["metric"] == "total")
        self.assertAlmostEqual(total_root, 24)

    def test_single_rank_omits_rank_diagnostics(self):
        data = self.export([profile(self.root / "serial.h5", ranks=1)])
        self.assertFalse({"imbalance", "rank_heatmap"} & {chart["kind"] for chart in data["charts"]})

    def test_axes_and_scaling_require_supporting_metadata(self):
        readers = [profile(self.root / "a.h5", 2), profile(self.root / "b.h5", 4)]
        self.assertEqual(scaling_axes(readers), ["num_ranks", "total_cores"])
        fixed = [{"weak_scaling": {"total_cells": 100, "cells_per_rank": 100 / n}} for n in (2, 4)]
        data = self.export(readers, fixed)
        kinds = {chart["kind"] for chart in data["charts"]}
        self.assertIn("scaling_efficiency", kinds)
        self.assertNotIn("weak_scaling_efficiency", kinds)
        change = self.payload(data, "comparison")
        self.assertEqual([file["label"] for file in change["files"]], ["a", "b"])
        for chart in data["charts"]:
            self.assertTrue((self.root / chart["path"]).is_file())
            for variant in chart.get("variants", []):
                self.assertTrue((self.root / variant["path"]).is_file())

        mixed = [dict(entry, run_parameters={"backend": backend}) for entry, backend in zip(fixed, ["numpy", "cupy"])]
        data = self.export(readers, mixed)
        self.assertNotIn("scaling_efficiency", {chart["kind"] for chart in data["charts"]})

        weak = [{"weak_scaling": {"total_cells": 50 * n, "cells_per_rank": 50}} for n in (2, 4)]
        data = self.export(readers, weak)
        kinds = {chart["kind"] for chart in data["charts"]}
        self.assertIn("weak_scaling_efficiency", kinds)
        self.assertNotIn("scaling_efficiency", kinds)
        payload = self.payload(data, "weak_scaling_efficiency")
        self.assertEqual(payload["options"]["work_per_rank"], 50)

        data = self.export(readers)
        kinds = {chart["kind"] for chart in data["charts"]}
        self.assertNotIn("weak_scaling_efficiency", kinds)
        self.assertNotIn("scaling_efficiency", kinds)

    def test_equal_scale_has_comparison_without_scaling(self):
        data = self.export([profile(self.root / "a.h5", 1), profile(self.root / "b.h5", 1)])
        self.assertEqual({chart["kind"] for chart in data["charts"]}, {"durations", "comparison"})
        self.assertEqual(data["unavailable"][0]["title"], "Scaling")


if __name__ == "__main__":
    unittest.main()
