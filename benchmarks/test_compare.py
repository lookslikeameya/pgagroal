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
# Tests of compare.py. No dependencies.
# Run: python3 benchmarks/test_compare.py

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
COMPARE = os.path.join(HERE, "compare.py")

META = {"case": "read_write", "branch": "master", "commit": "aaaaaaa",
        "build_type": "Release", "os": "Darwin", "cpus": 10, "dirty": False}
GOOD = {"read_write": {"tps": 1000, "p99_ms": 4.0}}

_tmp = tempfile.mkdtemp()
_n = 0
failures = []


def deepcopy(d):
    return json.loads(json.dumps(d))


def make_run(iterations, meta=None):
    """Create a run directory. iterations: list of dicts (or str for raw file
    content). meta=None writes META; meta=False writes no meta.json."""
    global _n
    _n += 1
    d = os.path.join(_tmp, f"run{_n}")
    os.makedirs(d)
    if meta is not False:
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(META if meta is None else meta, f)
    for i, it in enumerate(iterations, 1):
        with open(os.path.join(d, f"iter-{i}.json"), "w") as f:
            f.write(it if isinstance(it, str) else json.dumps(it))
    return d


def cand_meta(**kw):
    m = deepcopy(META)
    m.update({"branch": "my-branch", "commit": "bbbbbbb"})
    m.update(kw)
    return m


def run(args):
    p = subprocess.run([sys.executable, COMPARE] + args, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def row(out, metric):
    """The output line for the read_write workload's given metric."""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "read_write" and parts[1] == metric:
            return line
    return ""


def check(name, cond):
    print(f"{'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        failures.append(name)


def with_metric(metric, value):
    it = deepcopy(GOOD)
    it["read_write"][metric] = value
    return it


base = make_run([GOOD, GOOD, GOOD])

# 1. identical runs -> exit 0, no change, header names both branches
rc, out = run([base, make_run([GOOD, GOOD, GOOD], cand_meta())])
check("identical: exit 0", rc == 0)
check("identical: tps no change", row(out, "tps").endswith("no change"))
check("identical: p99 no change", row(out, "p99_ms").endswith("no change"))
check("identical: header", "master @ aaaaaaa" in out and "my-branch @ bbbbbbb" in out)
check("identical: run counts", "3 baseline / 3 candidate" in out)

# 2. TPS is higher-is-better
rc, out = run([base, make_run([with_metric("tps", 1200)], cand_meta())])
check("tps +20%: faster", row(out, "tps").endswith("1.20x faster"))
rc, out = run([base, make_run([with_metric("tps", 800)], cand_meta())])
check("tps -20%: slower", row(out, "tps").endswith("1.25x slower"))

# 3. p99 is lower-is-better
rc, out = run([base, make_run([with_metric("p99_ms", 2.0)], cand_meta())])
check("p99 halved: faster", row(out, "p99_ms").endswith("2.00x faster"))
rc, out = run([base, make_run([with_metric("p99_ms", 8.0)], cand_meta())])
check("p99 doubled: slower", row(out, "p99_ms").endswith("2.00x slower"))

# 4. the band
cand = make_run([with_metric("tps", 1050)], cand_meta())
rc, out = run([base, cand])
check("tps +5%, default band: no change", row(out, "tps").endswith("no change"))
rc, out = run([base, cand, "--band", "3"])
check("tps +5%, band 3: faster", row(out, "tps").endswith("1.05x faster"))
check("band 3: footer", "below 3%" in out)
rc, out = run([base, cand, "--band", "0"])
check("band 0: rejected", rc != 0 and "--band" in out)

# 5. median, not mean: one outlier iteration does not move the result
rc, out = run([base, make_run([with_metric("tps", 1000), with_metric("tps", 1010),
                               with_metric("tps", 9000)], cand_meta())])
check("median ignores outlier", row(out, "tps").endswith("no change"))

# 5b. the overlap rule: past the band, but the iterations' ranges overlap
rc, out = run([make_run([with_metric("tps", 1000), with_metric("tps", 1100), with_metric("tps", 1300)]),
               make_run([with_metric("tps", 1150), with_metric("tps", 1250), with_metric("tps", 1400)],
                        cand_meta())])
check("ranges overlap: no change", row(out, "tps").endswith("no change (ranges overlap)"))
check("ranges shown", "1000.0-1300.0" in row(out, "tps") and "1150.0-1400.0" in row(out, "tps"))
rc, out = run([make_run([with_metric("tps", 1000), with_metric("tps", 1100), with_metric("tps", 1140)]),
               make_run([with_metric("tps", 1150), with_metric("tps", 1250), with_metric("tps", 1400)],
                        cand_meta())])
check("ranges apart, past the band: faster", row(out, "tps").endswith("1.14x faster"))
rc, out = run([make_run([with_metric("p99_ms", 4.0), with_metric("p99_ms", 4.4)]),
               make_run([with_metric("p99_ms", 4.4), with_metric("p99_ms", 6.0)], cand_meta())])
check("touching ranges count as overlap", row(out, "p99_ms").endswith("no change (ranges overlap)"))
check("footer explains the overlap rule", "or the min-max ranges overlap" in out)

# 6. several workloads, reported in the order measured
multi = {"read_only": {"tps": 5000, "p99_ms": 1.0}, "read_write": {"tps": 1000, "p99_ms": 4.0}}
rc, out = run([make_run([multi]), make_run([multi], cand_meta())])
check("multi workload: exit 0", rc == 0)
rows = [line.split()[0] for line in out.splitlines()
        if line.startswith(("read_only ", "read_write "))]
check("multi workload: order kept", rows == ["read_only", "read_only", "read_write", "read_write"])

# 7. warnings (not fatal)
rc, out = run([base, make_run([GOOD], cand_meta(cpus=4))])
check("cpus differ: warned, exit 0", rc == 0 and "CPU count differs" in out)
rc, out = run([make_run([GOOD], cand_meta(ev_backend="io_uring")),
               make_run([GOOD], cand_meta(ev_backend="epoll"))])
check("ev_backend differs: warned, exit 0", rc == 0 and "event backend differs" in out)
rc, out = run([make_run([GOOD], cand_meta(postgres="external")),
               make_run([GOOD], cand_meta(postgres="container pgagroal-test-postgresql17-rocky10"))])
check("postgres setup differs: warned", rc == 0 and "PostgreSQL setup differs" in out)
rc, out = run([base, make_run([GOOD], cand_meta(build_type="Debug"))])
check("debug build: warned", "built as Debug" in out)
rc, out = run([base, make_run([GOOD], cand_meta(dirty=True))])
check("dirty tree: warned", "dirty working tree" in out)
rc, out = run([base, make_run([GOOD], cand_meta(pgagroal_errors=3))])
check("pgagroal errors: warned, exit 0", rc == 0 and "pgagroal logged 3 errors while measuring the candidate" in out)
rc, out = run([base, make_run([GOOD], cand_meta(pgagroal_errors=0))])
check("no pgagroal errors: no warning", "pgagroal logged" not in out)

# 8. header details and notes
full = cand_meta(arch="arm64", ev_backend="kqueue", pg_version="15.8", pgagroal_version="2.1.0")
rc, out = run([base, make_run([GOOD], full)])
# The header describes the machine from the baseline, which has no ev_backend here.
check("header: baseline without ev_backend", "ev_backend" not in out)
check("header: pgagroal version", "my-branch @ bbbbbbb (pgagroal 2.1.0)" in out)
rc, out = run([make_run([GOOD], full), make_run([GOOD], full)])
check("header: arch", "10 cores, Darwin arm64" in out)
check("header: baseline ev_backend", "ev_backend   : kqueue" in out)
check("header: postgresql", "postgresql   : 15.8" in out)
check("note: not Linux", "Note: Measured on Darwin, not Linux" in out)
linux = cand_meta(os="Linux")
rc, out = run([make_run([GOOD], linux), make_run([GOOD], linux)])
check("note: no platform note on Linux", "not Linux" not in out)
noted = cand_meta(os="Linux", note="pgbench -C is unreliable on master.")
rc, out = run([make_run([GOOD], noted), make_run([GOOD], noted)])
check("note: case note printed once", out.count("Note: pgbench -C is unreliable on master.") == 1)
rc, out = run([make_run([GOOD], cand_meta(os="Linux", note=""))] * 2)
check("note: empty note ignored", "Note: \n" not in out and "Note: ." not in out
      and out.count("Note:") == 1)  # only the drift hint

# 9. drift: each side against its own previous run
def tps_run(values, meta=None):
    return make_run([with_metric("tps", v) for v in values], meta)

newest_base = tps_run([1000, 1010, 1020])
rc, out = run([newest_base, tps_run([1000, 1010, 1020], cand_meta())])
check("drift: measured once -> hint", "master was measured once" in out and "my-branch was measured once" in out)

stable_prev = tps_run([1005, 1015, 1025])
rc, out = run([newest_base, tps_run([1000, 1010, 1020], cand_meta()), "--baseline-previous", stable_prev])
check("drift: agrees -> note", rc == 0 and "master agrees with its previous run" in out and "DRIFT" not in out)

drifted_prev = tps_run([1300, 1310, 1320])
rc, out = run([newest_base, tps_run([1000, 1010, 1020], cand_meta()), "--baseline-previous", drifted_prev])
check("drift: shifted -> warning, exit 0", rc == 0 and "DRIFT: master differs from its own previous run" in out)
check("drift: names the metric and both values", "read_write tps 1310.0 -> 1010.0 (1.30x slower)" in out)

overlap_prev = tps_run([900, 1150, 1400])
rc, out = run([newest_base, tps_run([1000], cand_meta()), "--baseline-previous", overlap_prev])
check("drift: overlapping ranges are not drift", "DRIFT" not in out)

rc, out = run([newest_base, tps_run([1000], cand_meta()), "--candidate-previous", make_run(["{bad"], cand_meta())])
check("drift: unreadable previous run -> note, comparison still valid",
      rc == 0 and "drift of my-branch not checked" in out)

rc, out = run([newest_base, newest_base, "--baseline-previous", drifted_prev, "--candidate-previous", drifted_prev])
check("drift: same branch on both sides -> one warning", out.count("DRIFT:") == 1)

# 10. integrity failures -> exit 2
def integrity(name, args):
    rc, out = run(args)
    check(f"{name}: integrity exit 2", rc == 2 and "INTEGRITY" in out)

integrity("missing run dir", [base, os.path.join(_tmp, "nope")])
integrity("missing meta.json", [base, make_run([GOOD], meta=False)])
integrity("meta without branch", [base, make_run([GOOD], {"case": "read_write", "commit": "b"})])
integrity("no iterations", [base, make_run([], cand_meta())])
integrity("malformed iteration", [base, make_run(["{not json"], cand_meta())])
integrity("null tps", [base, make_run([with_metric("tps", None)], cand_meta())])
integrity("zero p99", [base, make_run([with_metric("p99_ms", 0)], cand_meta())])
integrity("bool tps", [base, make_run([with_metric("tps", True)], cand_meta())])
integrity("missing metric", [base, make_run([{"read_write": {"tps": 1000}}], cand_meta())])
integrity("workloads differ within a run",
          [base, make_run([GOOD, multi], cand_meta())])
integrity("workloads differ between runs", [base, make_run([multi], cand_meta())])
integrity("different cases", [base, make_run([GOOD], cand_meta(case="read_only"))])

print()
if failures:
    print(f"FAILED ({len(failures)}): {failures}")
    sys.exit(1)
print("all benchmarks/compare.py tests passed")
