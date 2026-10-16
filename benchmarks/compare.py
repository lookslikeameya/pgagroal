#!/usr/bin/env python3
#
# Copyright (C) 2026 The pgagroal community
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this list
# of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice, this
# list of conditions and the following disclaimer in the documentation and/or other
# materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors may
# be used to endorse or promote products derived from this software without specific
# prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
# OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
# THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
# OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# Compare two runs of bench.sh: a baseline and a candidate.
#
# A run directory has meta.json (branch, commit, machine, ...) and one
# iter-N.json per iteration: {workload: {"tps": ..., "p99_ms": ...}}.
#
# Each side is the median of its iterations. TPS: higher is better; p99:
# lower is better. A difference is reported only if it is at least the band
# (10%) AND the min-max ranges of the two sides don't overlap. Otherwise it
# is "no change": overlapping ranges mean the difference is noise.
#
# Drift: a whole run can be shifted (busy machine), so even the same code can
# look faster. With --baseline-previous / --candidate-previous, each branch is
# also compared with its own previous run. If they differ, a DRIFT warning
# says differences of that size are not a result.
#
# Exit: 0 = compared (also when slower), 2 = bad or missing data.
#
# Usage:
#   compare.py BASELINE_RUN_DIR CANDIDATE_RUN_DIR [--band PCT]
#              [--baseline-previous RUN_DIR] [--candidate-previous RUN_DIR]

import argparse
import glob
import json
import os
import statistics
import sys

INTEGRITY_EXIT = 2

# (name, higher_is_better (ratio), display format)
METRICS = [
    ("tps", True, "{:.1f}"),
    ("p99_ms", False, "{:.2f}"),
]


class IntegrityError(Exception):
    pass


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise IntegrityError(f"cannot read/parse {path}: {e}")


def is_positive_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def load_run(label, run_dir):
    """Load and validate one run directory. Returns (meta, iterations)."""
    if not os.path.isdir(run_dir):
        raise IntegrityError(f"{label}: {run_dir} is not a directory")

    meta = read_json(os.path.join(run_dir, "meta.json"))
    if not isinstance(meta, dict):
        raise IntegrityError(f"{label}: meta.json is not an object")
    for key in ("case", "branch", "commit"):
        if not isinstance(meta.get(key), str) or not meta[key]:
            raise IntegrityError(f"{label}: meta.json has no '{key}'")

    paths = sorted(glob.glob(os.path.join(run_dir, "iter-*.json")))
    if not paths:
        raise IntegrityError(f"{label}: no iter-*.json files in {run_dir}")

    iterations = []
    workloads = None
    for p in paths:
        data = read_json(p)
        if not isinstance(data, dict) or not data:
            raise IntegrityError(f"{label}: {p} is not a non-empty object")
        # Every iteration must have the same workloads.
        if workloads is None:
            workloads = list(data)
        elif set(data) != set(workloads):
            raise IntegrityError(f"{label}: {p} measures {sorted(data)}, expected {sorted(workloads)}")
        for wl in workloads:
            if not isinstance(data[wl], dict):
                raise IntegrityError(f"{label}: {p} {wl} is not an object")
            for metric, _, _ in METRICS:
                v = data[wl].get(metric)
                if not is_positive_number(v):
                    raise IntegrityError(f"{label}: {p} {wl}.{metric} is not a positive number ({v!r})")
        iterations.append(data)

    return meta, iterations


def median_of(iterations, wl, metric):
    return statistics.median(it[wl][metric] for it in iterations)


def range_of(iterations, wl, metric):
    values = [it[wl][metric] for it in iterations]
    return min(values), max(values)


def overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def classify(base, cand, higher_is_better, band, base_range, cand_range):
    """Like pgmoneta's classify(), plus: overlapping ranges are no change."""
    if abs(cand - base) / base < band:
        return "no change"
    if overlap(base_range, cand_range):
        return "no change (ranges overlap)"
    ratio = cand / base if higher_is_better else base / cand
    if ratio > 1.0:
        return f"{ratio:.2f}x faster"
    return f"{1.0 / ratio:.2f}x slower"


def drift_check(label, newest_meta, newest_iters, previous_dir, band):
    """Compare a side with its previous run. Returns (warnings, notes)."""
    branch = newest_meta["branch"]
    if previous_dir is None:
        return [], [f"{branch} was measured once; measure it again to check for drift."]
    try:
        prev_meta, prev_iters = load_run(f"{label} previous", previous_dir)
    except IntegrityError as e:
        return [], [f"drift of {branch} not checked: {e}"]

    shifted = []
    for wl in newest_iters[0]:
        if wl not in prev_iters[0]:
            continue
        for metric, higher_is_better, fmt in METRICS:
            p = median_of(prev_iters, wl, metric)
            n = median_of(newest_iters, wl, metric)
            change = classify(p, n, higher_is_better, band,
                              range_of(prev_iters, wl, metric), range_of(newest_iters, wl, metric))
            if not change.startswith("no change"):
                shifted.append(f"{wl} {metric} {fmt.format(p)} -> {fmt.format(n)} ({change})")

    stamp = prev_meta.get("timestamp", "?")
    if shifted:
        return ["\n".join([f"DRIFT: {branch} differs from its own previous run ({stamp}):"]
                           + [f"  {x}" for x in shifted]
                           + ["  Same code, different numbers: differences this small are not a result."])], []
    return [], [f"{branch} agrees with its previous run ({stamp}): no drift."]


def warnings_for(base_meta, cand_meta):
    out = []
    for key, what in (("cpus", "CPU count"), ("os", "OS"), ("ev_backend", "event backend"),
                      ("postgres", "PostgreSQL setup")):
        b = base_meta.get(key)
        c = cand_meta.get(key)
        if b is not None and c is not None and b != c:
            out.append(f"WARNING: {what} differs ({b} vs {c}); results are not comparable.")
    for label, meta in (("baseline", base_meta), ("candidate", cand_meta)):
        bt = meta.get("build_type")
        if bt is not None and bt != "Release":
            out.append(f"WARNING: {label} was built as {bt}, not Release.")
        if meta.get("dirty") is True:
            out.append(f"WARNING: {label} was measured on a dirty working tree.")
        errors = meta.get("pgagroal_errors")
        if isinstance(errors, int) and not isinstance(errors, bool) and errors > 0:
            out.append(f"WARNING: pgagroal logged {errors} errors while measuring the {label}.")
    return out


def describe(meta):
    out = f"{meta['branch']} @ {meta['commit']}"
    if meta.get("pgagroal_version"):
        out += f" (pgagroal {meta['pgagroal_version']})"
    return out


def notes_for(base_meta, cand_meta):
    out = []
    for meta in (base_meta, cand_meta):
        note = meta.get("note")
        if isinstance(note, str) and note and note not in out:
            out.append(note)
    systems = sorted({m.get("os") for m in (base_meta, cand_meta)
                      if m.get("os") not in (None, "Linux")})
    if systems:
        out.append(f"Measured on {', '.join(systems)}, not Linux: only the direction of a change counts.")
    return out


def main():
    ap = argparse.ArgumentParser(description="Compare two bench.sh runs.")
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    ap.add_argument("--baseline-previous", help="the baseline branch's previous run, for the drift check")
    ap.add_argument("--candidate-previous", help="the candidate branch's previous run, for the drift check")
    ap.add_argument("--band", type=float, default=10.0,
                    help="no-change band in percent (default: 10)")
    args = ap.parse_args()

    if not 0 < args.band < 100:
        ap.error("--band must be between 0 and 100")
    band = args.band / 100.0

    try:
        base_meta, base_iters = load_run("baseline", args.baseline)
        cand_meta, cand_iters = load_run("candidate", args.candidate)
        if base_meta["case"] != cand_meta["case"]:
            raise IntegrityError(f"different cases: '{base_meta['case']}' vs '{cand_meta['case']}'")
        workloads = list(base_iters[0])
        if set(cand_iters[0]) != set(workloads):
            raise IntegrityError(f"baseline measures {sorted(workloads)}, candidate measures {sorted(cand_iters[0])}")
    except IntegrityError as e:
        print(f"BENCH INTEGRITY FAILURE: {e}", file=sys.stderr)
        return INTEGRITY_EXIT

    print(f"case         : {base_meta['case']}")
    print(f"runs         : {len(base_iters)} baseline / {len(cand_iters)} candidate (median)")
    if "build_type" in base_meta:
        print(f"build        : {base_meta['build_type']}")
    if "cpus" in base_meta or "os" in base_meta:
        machine = f"{base_meta.get('cpus', '?')} cores, {base_meta.get('os', '?')}"
        if "arch" in base_meta:
            machine += f" {base_meta['arch']}"
        print(f"machine      : {machine}")
    if "ev_backend" in base_meta:
        print(f"ev_backend   : {base_meta['ev_backend']}")
    if "pg_version" in base_meta:
        print(f"postgresql   : {base_meta['pg_version']}")
    print(f"baseline     : {describe(base_meta)}")
    print(f"candidate    : {describe(cand_meta)}")

    drift_warnings, drift_notes = [], []
    for label, meta, iters, previous in (("baseline", base_meta, base_iters, args.baseline_previous),
                                         ("candidate", cand_meta, cand_iters, args.candidate_previous)):
        w, n = drift_check(label, meta, iters, previous, band)
        drift_warnings += [x for x in w if x not in drift_warnings]
        drift_notes += [x for x in n if x not in drift_notes]

    warnings = warnings_for(base_meta, cand_meta) + drift_warnings
    if warnings:
        print()
        for w in warnings:
            print(w)

    print()
    print(f"{'workload':<20}  {'metric':<8}  {'baseline':>10}  {'min-max':>19}  "
          f"{'candidate':>10}  {'min-max':>19}  change")
    print(f"{'-' * 20}  {'-' * 8}  {'-' * 10}  {'-' * 19}  {'-' * 10}  {'-' * 19}  {'-' * 12}")
    for wl in workloads:
        for metric, higher_is_better, fmt in METRICS:
            b = median_of(base_iters, wl, metric)
            c = median_of(cand_iters, wl, metric)
            br = range_of(base_iters, wl, metric)
            cr = range_of(cand_iters, wl, metric)
            change = classify(b, c, higher_is_better, band, br, cr)
            brs = f"{fmt.format(br[0])}-{fmt.format(br[1])}"
            crs = f"{fmt.format(cr[0])}-{fmt.format(cr[1])}"
            print(f"{wl:<20}  {metric:<8}  {fmt.format(b):>10}  {brs:>19}  "
                  f"{fmt.format(c):>10}  {crs:>19}  {change}")

    notes = notes_for(base_meta, cand_meta) + drift_notes
    if notes:
        print()
        for n in notes:
            print(f"Note: {n}")

    print()
    print("tps: higher is better. p99_ms: lower is better.")
    print(f"No change: the difference is below {args.band:g}%, or the min-max ranges overlap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
