#!/usr/bin/env python3
"""Vital signs parameter tuning benchmark.

Evaluates breathing and heart rate extraction accuracy against recorded
CSI data with ground-truth annotations.  Produces per-parameter-set
metrics that guide tuning decisions.

Usage (with recorded data):
    python -m tools.vitals_benchmark \\
        --data recordings/session_001.npz \\
        --config backend/config/vitals.yaml

Usage (synthetic self-test — no hardware needed):
    python -m tools.vitals_benchmark --synthetic

Recording format (.npz):
    amplitudes:  (T, N_subcarriers) float64 — CSI amplitude time series
    sample_rate: scalar float — Hz
    Optional ground truth:
        breathing_bpm:     (K,) float — reference breathing rate at each window
        breathing_times:   (K,) float — timestamp (seconds) of each measurement
        heartrate_bpm:     (K,) float — reference heart rate at each window
        heartrate_times:   (K,) float — timestamp (seconds) of each measurement
        motion_labels:     (T,) int8  — 0=stationary, 1=moving per sample
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from numpy.typing import NDArray

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config.vitals_config import VitalsConfig, load_vitals_config


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class BreathingMetrics:
    """Accuracy metrics for breathing rate extraction."""
    total_windows: int = 0
    detected_windows: int = 0          # windows where extractor produced a result
    mean_abs_error_bpm: float = float("nan")
    median_abs_error_bpm: float = float("nan")
    within_1bpm_pct: float = 0.0       # % of detections within ±1 bpm
    within_2bpm_pct: float = 0.0       # % of detections within ±2 bpm
    mean_confidence: float = 0.0
    mean_snr_db: float = 0.0
    detection_rate: float = 0.0        # detected / total


@dataclass
class HeartRateMetrics:
    """Accuracy metrics for heart rate extraction."""
    total_windows: int = 0
    detected_windows: int = 0
    displayed_windows: int = 0          # windows where display=True
    mean_abs_error_bpm: float = float("nan")
    median_abs_error_bpm: float = float("nan")
    within_5bpm_pct: float = 0.0
    within_10bpm_pct: float = 0.0
    mean_confidence: float = 0.0
    mean_snr_db: float = 0.0
    detection_rate: float = 0.0
    display_rate: float = 0.0


@dataclass
class MotionMetrics:
    """Accuracy metrics for motion classification."""
    total_samples: int = 0
    accuracy: float = 0.0
    precision_stationary: float = 0.0
    recall_stationary: float = 0.0
    f1_stationary: float = 0.0


@dataclass
class BenchmarkResult:
    """Complete benchmark result."""
    config_path: str
    data_source: str
    breathing: BreathingMetrics = field(default_factory=BreathingMetrics)
    heartrate: HeartRateMetrics = field(default_factory=HeartRateMetrics)
    motion: Optional[MotionMetrics] = None
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Synthetic data generator (for self-test)
# ---------------------------------------------------------------------------


def generate_synthetic_recording(
    duration_s: float = 120.0,
    sample_rate: float = 100.0,
    n_subcarriers: int = 114,
    breathing_bpm: float = 15.0,
    heartrate_bpm: float = 72.0,
    seed: int = 42,
) -> dict[str, NDArray]:
    """Generate a synthetic CSI recording with known vital signs.

    Returns a dict matching the .npz recording format.
    """
    rng = np.random.default_rng(seed)
    n_samples = int(duration_s * sample_rate)
    t = np.arange(n_samples) / sample_rate

    breathing_freq = breathing_bpm / 60.0
    heartrate_freq = heartrate_bpm / 60.0

    # Build amplitude matrix: base + breathing + heartrate + noise
    base = 50.0 + 5.0 * np.sin(0.01 * t)  # slow drift
    breathing_sig = 3.0 * np.sin(2 * math.pi * breathing_freq * t)
    heartrate_sig = 0.3 * np.sin(2 * math.pi * heartrate_freq * t)

    # Per-subcarrier variation: some subcarriers are more responsive
    subcarrier_weights = rng.uniform(0.5, 1.5, n_subcarriers)

    amplitudes = np.zeros((n_samples, n_subcarriers), dtype=np.float64)
    for sc in range(n_subcarriers):
        w = subcarrier_weights[sc]
        noise = rng.normal(0, 1.5, n_samples)
        amplitudes[:, sc] = (
            base + w * breathing_sig + w * 0.5 * heartrate_sig + noise
        )

    amplitudes = np.maximum(amplitudes, 1.0)

    # Ground truth: one measurement per 30s window
    window_s = 30.0
    n_windows = int(duration_s / window_s)
    gt_times = np.array([window_s * (i + 0.5) for i in range(n_windows)])
    gt_breathing = np.full(n_windows, breathing_bpm)
    gt_heartrate = np.full(n_windows, heartrate_bpm)

    # Motion labels: stationary throughout
    motion_labels = np.zeros(n_samples, dtype=np.int8)

    return {
        "amplitudes": amplitudes,
        "sample_rate": np.float64(sample_rate),
        "breathing_bpm": gt_breathing,
        "breathing_times": gt_times,
        "heartrate_bpm": gt_heartrate,
        "heartrate_times": gt_times,
        "motion_labels": motion_labels,
    }


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run_benchmark(
    data: dict[str, NDArray],
    config: VitalsConfig,
    config_path: str = "default",
    data_source: str = "unknown",
) -> BenchmarkResult:
    """Run the full vital signs benchmark against recorded data."""
    start = time.monotonic()

    amplitudes = data["amplitudes"]  # (T, N)
    sample_rate = float(data["sample_rate"])
    n_samples, n_subcarriers = amplitudes.shape

    # Override sample rate from data
    from backend.config.vitals_config import (
        BreathingConfig,
        HeartRateConfig,
        MotionConfig,
    )

    # Create extractors from config
    breathing_ext = config.create_breathing_extractor()
    heartrate_ext = config.create_heartrate_extractor()
    motion_det = config.create_motion_detector()

    # Push all samples through extractors
    window_size = int(30.0 * sample_rate)  # 30s windows
    breathing_results: list[tuple[float, object]] = []  # (time_s, result)
    heartrate_results: list[tuple[float, object]] = []
    motion_results: list[Optional[object]] = []

    for i in range(n_samples):
        amp = amplitudes[i]
        t_s = i / sample_rate

        # Motion
        motion_r = motion_det.update(amp)
        motion_results.append(motion_r)

        # Breathing
        br = breathing_ext.update(amp)
        if br is not None and i > 0 and i % window_size == 0:
            breathing_results.append((t_s, br))

        # Heart rate (assume stationary for benchmark)
        is_stat = motion_r.is_stationary if motion_r else True
        stat_dur = motion_r.stationary_duration_s if motion_r else t_s
        breathing_freq = br.breathing_rate_bpm / 60.0 if br else None

        hr = heartrate_ext.update(
            amp,
            position_confidence=0.8,
            is_stationary=is_stat,
            stationary_duration_s=stat_dur,
            breathing_freq_hz=breathing_freq,
        )
        if hr is not None and i > 0 and i % window_size == 0:
            heartrate_results.append((t_s, hr))

    result = BenchmarkResult(
        config_path=config_path,
        data_source=data_source,
    )

    # ── Breathing metrics ──
    gt_br_bpm = data.get("breathing_bpm")
    gt_br_times = data.get("breathing_times")
    if gt_br_bpm is not None and gt_br_times is not None and len(breathing_results) > 0:
        bm = result.breathing
        bm.total_windows = len(gt_br_bpm)

        errors = []
        confidences = []
        snrs = []

        for gt_t, gt_bpm in zip(gt_br_times, gt_br_bpm):
            # Find closest estimated result
            closest = min(breathing_results, key=lambda r: abs(r[0] - float(gt_t)))
            est_t, est_r = closest
            if abs(est_t - float(gt_t)) < 35.0:  # within reasonable window
                bm.detected_windows += 1
                errors.append(abs(float(est_r.breathing_rate_bpm) - float(gt_bpm)))
                confidences.append(float(est_r.breathing_confidence))
                snrs.append(float(est_r.snr_db))

        if errors:
            arr = np.array(errors)
            bm.mean_abs_error_bpm = float(np.mean(arr))
            bm.median_abs_error_bpm = float(np.median(arr))
            bm.within_1bpm_pct = float(np.mean(arr <= 1.0)) * 100
            bm.within_2bpm_pct = float(np.mean(arr <= 2.0)) * 100
            bm.mean_confidence = float(np.mean(confidences))
            bm.mean_snr_db = float(np.mean(snrs))
        bm.detection_rate = bm.detected_windows / max(bm.total_windows, 1) * 100

    # ── Heart rate metrics ──
    gt_hr_bpm = data.get("heartrate_bpm")
    gt_hr_times = data.get("heartrate_times")
    if gt_hr_bpm is not None and gt_hr_times is not None and len(heartrate_results) > 0:
        hm = result.heartrate
        hm.total_windows = len(gt_hr_bpm)

        errors = []
        confidences = []
        snrs = []

        for gt_t, gt_bpm in zip(gt_hr_times, gt_hr_bpm):
            closest = min(heartrate_results, key=lambda r: abs(r[0] - float(gt_t)))
            est_t, est_r = closest
            if abs(est_t - float(gt_t)) < 35.0:
                hm.detected_windows += 1
                if est_r.display:
                    hm.displayed_windows += 1
                if est_r.rate_bpm is not None:
                    errors.append(abs(float(est_r.rate_bpm) - float(gt_bpm)))
                confidences.append(float(est_r.confidence))
                snrs.append(float(est_r.snr_db))

        if errors:
            arr = np.array(errors)
            hm.mean_abs_error_bpm = float(np.mean(arr))
            hm.median_abs_error_bpm = float(np.median(arr))
            hm.within_5bpm_pct = float(np.mean(arr <= 5.0)) * 100
            hm.within_10bpm_pct = float(np.mean(arr <= 10.0)) * 100
        if confidences:
            hm.mean_confidence = float(np.mean(confidences))
            hm.mean_snr_db = float(np.mean(snrs))
        hm.detection_rate = hm.detected_windows / max(hm.total_windows, 1) * 100
        hm.display_rate = hm.displayed_windows / max(hm.total_windows, 1) * 100

    # ── Motion metrics ──
    gt_motion = data.get("motion_labels")
    if gt_motion is not None:
        valid_results = [r for r in motion_results if r is not None]
        if valid_results:
            # Align: skip the first few samples where detector wasn't ready
            offset = n_samples - len(valid_results)
            gt_aligned = gt_motion[offset:]
            pred = np.array([0 if r.is_stationary else 1 for r in valid_results])
            gt_a = gt_aligned[: len(pred)]

            mm = MotionMetrics()
            mm.total_samples = len(gt_a)
            mm.accuracy = float(np.mean(pred == gt_a)) * 100

            # Stationary precision/recall
            tp = int(np.sum((pred == 0) & (gt_a == 0)))
            fp = int(np.sum((pred == 0) & (gt_a == 1)))
            fn = int(np.sum((pred == 1) & (gt_a == 0)))
            mm.precision_stationary = tp / max(tp + fp, 1) * 100
            mm.recall_stationary = tp / max(tp + fn, 1) * 100
            if mm.precision_stationary + mm.recall_stationary > 0:
                mm.f1_stationary = (
                    2 * mm.precision_stationary * mm.recall_stationary
                    / (mm.precision_stationary + mm.recall_stationary)
                )
            result.motion = mm

    result.elapsed_seconds = time.monotonic() - start
    return result


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------


def format_report(result: BenchmarkResult) -> str:
    """Format benchmark results as a readable report."""
    lines = [
        "=" * 60,
        "  VITAL SIGNS BENCHMARK REPORT",
        "=" * 60,
        f"  Config:  {result.config_path}",
        f"  Data:    {result.data_source}",
        f"  Runtime: {result.elapsed_seconds:.1f}s",
        "",
    ]

    # Breathing
    b = result.breathing
    lines.append("── Breathing Rate ──────────────────────────────────")
    if b.total_windows > 0:
        lines.extend([
            f"  Detection rate:    {b.detection_rate:.0f}% ({b.detected_windows}/{b.total_windows})",
            f"  Mean abs error:    {b.mean_abs_error_bpm:.1f} bpm",
            f"  Median abs error:  {b.median_abs_error_bpm:.1f} bpm",
            f"  Within ±1 bpm:     {b.within_1bpm_pct:.0f}%",
            f"  Within ±2 bpm:     {b.within_2bpm_pct:.0f}%",
            f"  Mean confidence:   {b.mean_confidence:.3f}",
            f"  Mean SNR:          {b.mean_snr_db:.1f} dB",
        ])
    else:
        lines.append("  (no ground truth data)")
    lines.append("")

    # Heart rate
    h = result.heartrate
    lines.append("── Heart Rate ──────────────────────────────────────")
    if h.total_windows > 0:
        lines.extend([
            f"  Detection rate:    {h.detection_rate:.0f}% ({h.detected_windows}/{h.total_windows})",
            f"  Display rate:      {h.display_rate:.0f}% ({h.displayed_windows}/{h.total_windows})",
            f"  Mean abs error:    {h.mean_abs_error_bpm:.1f} bpm",
            f"  Median abs error:  {h.median_abs_error_bpm:.1f} bpm",
            f"  Within ±5 bpm:     {h.within_5bpm_pct:.0f}%",
            f"  Within ±10 bpm:    {h.within_10bpm_pct:.0f}%",
            f"  Mean confidence:   {h.mean_confidence:.3f}",
            f"  Mean SNR:          {h.mean_snr_db:.1f} dB",
        ])
    else:
        lines.append("  (no ground truth data)")
    lines.append("")

    # Motion
    if result.motion:
        m = result.motion
        lines.append("── Motion Detection ────────────────────────────────")
        lines.extend([
            f"  Accuracy:          {m.accuracy:.1f}%",
            f"  Precision (stat):  {m.precision_stationary:.1f}%",
            f"  Recall (stat):     {m.recall_stationary:.1f}%",
            f"  F1 (stat):         {m.f1_stationary:.1f}%",
            f"  Total samples:     {m.total_samples}",
        ])
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vital signs parameter tuning benchmark"
    )
    parser.add_argument(
        "--data",
        type=str,
        help="Path to recorded CSI data (.npz file)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to vitals.yaml config (default: backend/config/vitals.yaml)",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Run with synthetic data (self-test, no hardware needed)",
    )
    args = parser.parse_args()

    if not args.data and not args.synthetic:
        parser.error("Provide --data <file.npz> or --synthetic")

    # Load config
    if args.config:
        config = load_vitals_config(args.config)
        config_path = args.config
    else:
        try:
            config = load_vitals_config()
            config_path = "backend/config/vitals.yaml"
        except FileNotFoundError:
            config = VitalsConfig()
            config_path = "defaults (no vitals.yaml)"

    # Load data
    if args.synthetic:
        data = generate_synthetic_recording()
        data_source = "synthetic (15 bpm breathing, 72 bpm heart rate)"
    else:
        npz = np.load(args.data, allow_pickle=False)
        data = dict(npz)
        data_source = args.data

    # Run benchmark
    result = run_benchmark(
        data=data,
        config=config,
        config_path=config_path,
        data_source=data_source,
    )

    print(format_report(result))


if __name__ == "__main__":
    main()
