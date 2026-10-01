"""Run every test suite in the repo from one place.

    python scripts/run_tests.py                 # all suites, one after another
    python scripts/run_tests.py -j 4            # run up to 4 suites at once
    python scripts/run_tests.py --list          # show the suites and exit
    python scripts/run_tests.py backend ingestion      # only these suites
    python scripts/run_tests.py --skip ingestion       # everything but these
    python scripts/run_tests.py -v                     # stream each suite's full output
    python scripts/run_tests.py backend -- -k approval # extra args go to pytest

The script needs nothing but Python. Each suite runs with its own interpreter: the
suite's own ``.venv`` when it has one, else the repo-root ``.venv``, else the Python
running this script.
The exit code is 0 only if every suite passed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Suite:
    name: str
    description: str
    cwd: str  # relative to the repo root
    args: tuple[str, ...] = ()
    pythonpath: tuple[str, ...] = ()  # relative to cwd
    own_venv: bool = True  # prefer <cwd>/.venv over the repo-root .venv


SUITES = [
    Suite(
        "backend",
        "Chat API: auth, config, approvals, streaming, MCP loading, agent service",
        "apps/backend",
        own_venv=False,
    ),
    Suite(
        "simulator",
        "Alarm & ticketing simulator API",
        "apps/alarms_and_ticket_simulation_api",
        pythonpath=(".",),
        own_venv=False,
    ),
    Suite("alarm-mcp", "Alarm management MCP server", "mcp_servers/alarm-management", pythonpath=("src",)),
    Suite("ticketing-mcp", "Ticketing MCP server", "mcp_servers/ticketing", pythonpath=("src",)),
    Suite("knowledge-base-mcp", "Knowledge base (RAG) MCP server", "mcp_servers/knowledge-base", pythonpath=("src",)),
    Suite("ingestion", "Document ingestion service (slow: includes the end-to-end test)", "ingestion"),
]


@dataclass
class Result:
    suite: Suite
    status: str  # passed | failed | skipped
    seconds: float = 0.0
    output: str = ""
    note: str = ""
    summary: str = field(default="")


def venv_python(venv: Path) -> Path | None:
    for rel in ("Scripts/python.exe", "bin/python"):
        candidate = venv / rel
        if candidate.is_file():
            return candidate
    return None


def find_python(suite: Suite) -> str:
    cwd = ROOT / suite.cwd
    candidates = ([cwd / ".venv"] if suite.own_venv else []) + [ROOT / ".venv"]
    for venv in candidates:
        if python := venv_python(venv):
            return str(python)
    return sys.executable


def command_for(suite: Suite, extra: list[str]) -> list[str]:
    return [find_python(suite), "-m", "pytest", "-p", "no:cacheprovider", "-q", *suite.args, *extra]


def summary_line(output: str) -> str:
    """The last line that looks like a pytest summary, e.g. '44 passed in 47.00s'."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for ln in reversed(lines):
        low = ln.lower().strip("= ")
        if any(w in low for w in (" passed", " failed", " error", "no tests ran")):
            return ln.strip("= ")
    return ""


def run_suite(suite: Suite, extra: list[str], verbose: bool) -> Result:
    cmd = command_for(suite, extra)

    cwd = ROOT / suite.cwd
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if suite.pythonpath:
        parts = [str((cwd / p).resolve()) for p in suite.pythonpath]
        env["PYTHONPATH"] = os.pathsep.join(parts + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))

    started = time.monotonic()
    if verbose:
        print(f"\n=== {suite.name}: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        if verbose:
            print(line, end="", flush=True)
    code = proc.wait()
    output = "".join(lines)

    result = Result(suite, "passed" if code == 0 else "failed", time.monotonic() - started, output)
    result.summary = summary_line(output)
    if code == 5:  # pytest: nothing collected
        result.status, result.note = "failed", "no tests were collected"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("suites", nargs="*", metavar="SUITE", help="suites to run (default: all)")
    parser.add_argument("--skip", nargs="+", default=[], metavar="SUITE", help="suites to leave out")
    parser.add_argument("-j", "--jobs", type=int, default=1, help="suites to run at the same time (default 1)")
    parser.add_argument("-v", "--verbose", action="store_true", help="stream each suite's output as it runs")
    parser.add_argument("--list", action="store_true", help="list suites and exit")
    argv = sys.argv[1:]
    extra: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, extra = argv[:split], argv[split + 1 :]
    args = parser.parse_args(argv)

    by_name = {s.name: s for s in SUITES}
    unknown = [n for n in [*args.suites, *args.skip] if n not in by_name]
    if unknown:
        parser.error(f"unknown suite(s): {', '.join(unknown)} (see --list)")

    if args.list:
        for s in SUITES:
            print(f"{s.name:20} {s.cwd:42} {s.description}")
        return 0

    selected = [by_name[n] for n in args.suites] if args.suites else list(SUITES)
    selected = [s for s in selected if s.name not in args.skip]
    if not selected:
        parser.error("no suites selected")
    if args.jobs > 1 and args.verbose:
        print("note: output of parallel suites will interleave", file=sys.stderr)

    print(f"Running {len(selected)} suite(s): {', '.join(s.name for s in selected)}", flush=True)
    results: list[Result] = []

    def work(suite: Suite) -> Result:
        result = run_suite(suite, extra, args.verbose)
        if not args.verbose:
            mark = "PASS" if result.status == "passed" else "FAIL"
            print(f"  [{mark}] {suite.name:20} {result.seconds:6.1f}s  {result.summary or result.note}", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(work, selected))

    failed = [r for r in results if r.status != "passed"]
    for r in failed:  # show details only for what broke
        print(f"\n{'-' * 78}\n{r.suite.name} FAILED {r.note}\n{'-' * 78}")
        if not args.verbose:
            print(r.output.rstrip())

    print("\n" + "=" * 78)
    print(f"{'Suite':20} {'Result':8} {'Time':>8}  Summary")
    for r in results:
        print(f"{r.suite.name:20} {r.status.upper():8} {r.seconds:7.1f}s  {r.summary or r.note}")
    total = sum(r.seconds for r in results)
    print(f"\n{len(results) - len(failed)}/{len(results)} suites passed in {total:.1f}s (sum of suite times)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
