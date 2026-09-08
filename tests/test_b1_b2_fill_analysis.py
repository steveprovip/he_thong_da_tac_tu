"""Tests for B1 (fill rate F4) and B2 (logistic p_fill model).

These tests use only the live testnet data files (read-only) and
the analysis scripts in phan_tich/. They do NOT modify any data files.
"""
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
B1_SCRIPT = ROOT / "phan_tich" / "_b1_fill_rate_f4.py"
B2_SCRIPT = ROOT / "phan_tich" / "_b2_pfill_model.py"
JOURNAL = ROOT / "data" / "testnet-journal.sqlite"
PROBES = ROOT / "data" / "testnet-signal-lab-v2.jsonl"


def _run(script):
    result = subprocess.run(
        [sys.executable, "-B", str(script)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60
    )
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.mark.skipif(not JOURNAL.exists(), reason="testnet journal not present")
class TestB1FillRateF4:
    def test_output_has_required_keys(self):
        out = _run(B1_SCRIPT)
        for key in ("all", "f4_qualified", "non_f4", "unmatched_to_probe"):
            assert key in out, f"Missing key: {key}"

    def test_fill_rates_are_valid_probabilities(self):
        out = _run(B1_SCRIPT)
        for group in ("all", "f4_qualified", "non_f4"):
            fr = out[group]["fill_rate"]
            if fr is not None:
                assert 0.0 <= fr <= 1.0, f"{group} fill_rate out of range: {fr}"

    def test_wilson_ci_contains_fill_rate(self):
        out = _run(B1_SCRIPT)
        for group in ("all", "f4_qualified", "non_f4"):
            g = out[group]
            if g["fill_rate"] is not None and g["wilson95"] is not None:
                lo, hi = g["wilson95"]
                assert lo <= g["fill_rate"] <= hi, (
                    f"{group}: fill_rate {g['fill_rate']} outside CI [{lo},{hi}]"
                )

    def test_counts_are_consistent(self):
        out = _run(B1_SCRIPT)
        for group in ("all", "f4_qualified", "non_f4"):
            g = out[group]
            assert g["filled"] + g["unfilled"] + g["unknown"] == g["n"], (
                f"{group}: counts don't sum to n"
            )

    def test_all_n_equals_total_entry_intents(self):
        """all.n must equal the number of ENTRY_INTENT events in the journal."""
        import sqlite3
        con = sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True)
        n_entries = con.execute(
            "select count(*) from events where kind='ENTRY_INTENT'"
        ).fetchone()[0]
        con.close()
        out = _run(B1_SCRIPT)
        assert out["all"]["n"] == n_entries, (
            f"all.n={out['all']['n']} != ENTRY_INTENT count={n_entries}"
        )

    def test_f4_fill_rate_reported_with_ci(self):
        """F4 fill rate must be reported with a Wilson CI (not None)."""
        out = _run(B1_SCRIPT)
        f4 = out["f4_qualified"]
        assert f4["fill_rate"] is not None
        assert f4["wilson95"] is not None
        lo, hi = f4["wilson95"]
        assert lo < hi, "CI lower must be less than upper"


@pytest.mark.skipif(not JOURNAL.exists(), reason="testnet journal not present")
class TestB2LogisticModel:
    def test_output_has_required_keys(self):
        out = _run(B2_SCRIPT)
        for key in ("dataset", "metrics", "coefficients", "predicted_p_fill"):
            assert key in out, f"Missing key: {key}"

    def test_dataset_split_is_temporal(self):
        """train_n + test_n must equal total_known (no data leakage)."""
        out = _run(B2_SCRIPT)
        ds = out["dataset"]
        assert ds["train_n"] + ds["test_n"] == ds["total_known"]
        # 70/30 split: train should be ~70%
        ratio = ds["train_n"] / ds["total_known"]
        assert 0.65 <= ratio <= 0.75, f"Train ratio {ratio:.2f} outside expected 65-75%"

    def test_metrics_are_finite(self):
        out = _run(B2_SCRIPT)
        m = out["metrics"]
        for key in ("train_brier", "test_brier", "baseline_brier_test", "test_log_loss"):
            assert math.isfinite(m[key]), f"{key} is not finite: {m[key]}"

    def test_brier_scores_are_in_range(self):
        out = _run(B2_SCRIPT)
        m = out["metrics"]
        for key in ("train_brier", "test_brier", "baseline_brier_test"):
            assert 0.0 <= m[key] <= 1.0, f"{key}={m[key]} out of [0,1]"

    def test_predicted_pfill_are_probabilities(self):
        out = _run(B2_SCRIPT)
        pp = out["predicted_p_fill"]
        for key in ("f4_test_mean", "non_f4_test_mean"):
            v = pp[key]
            if v is not None:
                assert 0.0 <= v <= 1.0, f"{key}={v} not a probability"

    def test_caution_label_present(self):
        """The output must carry the caution label about not replacing real fills."""
        out = _run(B2_SCRIPT)
        assert "KHONG thay the fill that" in out.get("ghichu", ""), (
            "Caution label missing from output"
        )
