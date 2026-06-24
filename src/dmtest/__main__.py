import argparse
import dmtest.db as db
import dmtest.dependency_tracker as dep
import itertools
import os
import re
import shutil
import subprocess
import sys
from typing import Optional, NamedTuple, Sequence


class TreeFormatter:
    def __init__(self):
        self._previous = []
        self._indent = "  "

    def tree_line(self, path):
        components = [c for c in path.split("/") if c.strip()]
        strs = []
        depth = 0
        for old, new in itertools.zip_longest(
            self._previous, components, fillvalue=None
        ):
            if not new:
                break

            if old != new:
                strs.append(f"{self._indent * depth}{new}".ljust(50, " ") + "\n")
            depth += 1
        self._previous = components
        return "".join(strs)[:-1]


# -----------------------------------------
# Result set helpers

def get_result_set(args):
    if args.result_set:
        return str(args.result_set)

    rs = os.environ.get("DMTEST_RESULT_SET", None)
    if rs:
        return str(rs)

    print(
        """
Missing result set.

This can be specified either on the command line:
    --result-set device-mapper2

or by setting an environment variable:
    export DMTEST_RESULT_SET=device-mapper2

The result set can be any string that is meaningful to you,
eg 'bufio-rewrite'.
    """,
        file=sys.stderr,
    )
    sys.exit(1)


# -----------------------------------------
# Filtering helpers — applied to stored test names

def build_filter(args):
    filters = []
    for pat in args.rx or []:
        regex = re.compile(pat)
        filters.append(("rx", regex))
    for ss in args.substring or []:
        filters.append(("ss", ss))
    return filters


def matches_filter(test_name, filters, and_mode=False):
    if not filters:
        return True
    results = []
    for kind, val in filters:
        if kind == "rx":
            results.append(bool(val.search(test_name)))
        elif kind == "ss":
            results.append(val in test_name)
    if and_mode:
        return all(results)
    return any(results)


def matches_state(pass_fail, state_filters):
    if not state_filters:
        return True
    for s in state_filters:
        negate = s.startswith("^")
        target = s[1:] if negate else s
        match = (pass_fail or "-").lower() == target.lower()
        if negate:
            match = not match
        if match:
            return True
    return False


def get_matching_paths(results, result_set, args):
    filters = build_filter(args)
    state_filters = args.state or []
    and_mode = getattr(args, "and_filters", False)

    all_names = results.get_test_names(result_set)
    paths = []
    for name in all_names:
        if not matches_filter(name, filters, and_mode):
            continue
        if state_filters:
            res_list = results.get_test_results(name, result_set)
            pf = res_list[0].pass_fail if res_list else "-"
            if not matches_state(pf, state_filters):
                continue
        paths.append(name)
    return sorted(paths)


# -----------------------------------------
# 'result-sets' command

def cmd_result_sets(args, results):
    for rs in results.get_result_sets():
        print(f"    {rs}")


# -----------------------------------------
# 'result-set-delete' command

def cmd_result_set_delete(args, results):
    try:
        results.delete_result_set(args.result_set)
    except db.NoSuchResultSet:
        print(f"No such result set '{args.result_set}'", file=sys.stderr)


# -----------------------------------------
# 'result-set-rename' command

def cmd_result_set_rename(args, results):
    try:
        results.rename_result_set(args.old_result_set, args.new_result_set)
    except (db.NoSuchResultSet, db.ResultSetInUse) as e:
        print(str(e), file=sys.stderr)


# -----------------------------------------
# 'list' command

class AvgResult(NamedTuple):
    pass_fail: Optional[str]
    nr_pass: int
    nr_runs: int
    duration: float


def average_results(res_list: Sequence[db.TestResult]) -> Optional[AvgResult]:
    if len(res_list) == 0:
        return None

    if len(res_list) == 1:
        return AvgResult(
            res_list[0].pass_fail,
            1 if res_list[0].pass_fail == "PASS" else 0,
            1,
            res_list[0].duration
        )

    nr_pass = 0
    all_duration = 0.0
    pass_duration = 0.0
    all_same = True
    pass_fail = res_list[0].pass_fail

    for result in res_list:
        all_duration += result.duration
        if result.pass_fail != pass_fail:
            all_same = False
        if result.pass_fail == "PASS":
            nr_pass += 1
            pass_duration += result.duration

    return AvgResult(
        pass_fail if all_same else None,
        nr_pass,
        len(res_list),
        pass_duration / nr_pass if nr_pass > 0 else all_duration / len(res_list)
    )


def cmd_list(args, results):
    result_set = get_result_set(args)
    paths = get_matching_paths(results, result_set, args)
    formatter = TreeFormatter()

    if len(paths) == 0:
        print("No matching tests found.")

    for p in paths:
        print(f"{formatter.tree_line(p)}", end=" ")
        result = average_results(results.get_test_results(p, result_set, getattr(args, 'run_nr', None)))
        if result is None:
            print("-")
        elif result.nr_runs == 1:
            print(f"{result.pass_fail} [{result.duration:.2f}s]")
        elif result.pass_fail:
            print(f"{result.nr_runs}/{result.nr_runs} {result.pass_fail} [{result.duration:.2f}s]")
        else:
            print(f"{result.nr_pass}/{result.nr_runs} PASS [{result.duration:.2f}s]")


# -----------------------------------------
# 'log' command

def cmd_log(args, results):
    result_set = get_result_set(args)
    paths = get_matching_paths(results, result_set, args)

    if len(paths) == 0:
        print("No matching tests found.")

    for p in paths:
        res_list = results.get_test_results(p, result_set, getattr(args, 'run_nr', None))
        if len(res_list) == 0:
            print(f"*** NO LOG FOR {p}")
            continue
        for result in res_list:
            if len(paths) > 1 or len(res_list) > 1:
                msg = ""
                if len(paths) > 1:
                    msg += f" {p}"
                if len(res_list) > 1:
                    msg += f" RUN {result.run_nr}"
                print(f"*** LOG FOR{msg}, {len(result.log)} ***")
            print(result.log)
            if args.with_dmesg:
                print("*** KERNEL LOG ***")
                print(result.dmesg)


# -----------------------------------------
# 'compare' command

def can_compare_times(old, new):
    if old is None or new is None:
        return False
    if old.nr_pass != 0 and new.nr_pass != 0:
        return True
    return old.pass_fail and old.pass_fail == new.pass_fail


def cmd_compare(args, results):
    if not args.old_result_set:
        print("Missing old result set.", file=sys.stderr)
        sys.exit(1)
    new_set = get_result_set(args)
    paths = get_matching_paths(results, new_set, args)
    formatter = TreeFormatter()

    if len(paths) == 0:
        print("No matching tests found.")

    for p in paths:
        old_result = average_results(results.get_test_results(p, args.old_result_set))
        new_result = average_results(results.get_test_results(p, new_set))
        print(f"{formatter.tree_line(p)}", end=" ")
        if old_result:
            if old_result.pass_fail:
                print(f"{old_result.pass_fail} => ", end="")
            else:
                print(f"{old_result.nr_pass / old_result.nr_runs * 100:.0f}% PASS => ", end="")
        else:
            print("- => ", end="")
        if new_result:
            if new_result.pass_fail:
                print(f"{new_result.pass_fail} ", end="")
            else:
                print(f"{new_result.nr_pass / new_result.nr_runs * 100:.0f}% PASS ", end="")
        else:
            print("- ", end="")
        if can_compare_times(old_result, new_result):
            diff = new_result.duration - old_result.duration
            print(f"[{diff * 100 / old_result.duration:+.0f}% {diff:+.2f}s]")
        else:
            print("")


# -----------------------------------------
# 'list-runs' command

def cmd_list_runs(args, results):
    result_set = get_result_set(args)
    paths = get_matching_paths(results, result_set, args)
    formatter = TreeFormatter()

    if len(paths) == 0:
        print("No matching tests found.")

    for p in paths:
        found = False
        res_list = results.get_test_results(p, result_set)
        print(f"{formatter.tree_line(p)}", end=" ")
        for result in res_list:
            if args.run_state and result.pass_fail.lower() != args.run_state.lower():
                continue
            if found:
                print(f"{''.ljust(50,' ')}", end="")
            else:
                found = True
            print(f"{result.run_nr}: {result.pass_fail} [{result.duration:.2f}s]")
        if not found:
            print("-")


# -----------------------------------------
# 'run' command — wraps pytest

def cmd_run(args, results):
    pytest_args = ["pytest"]

    if args.result_set:
        pytest_args.extend(["--result-set", args.result_set])
    elif os.environ.get("DMTEST_RESULT_SET"):
        pass  # pytest conftest reads DMTEST_RESULT_SET itself

    pytest_args.extend(args.pytest_args)

    try:
        result = subprocess.run(pytest_args)
        sys.exit(result.returncode)
    except FileNotFoundError:
        print("Error: pytest not found. Install with: pip install pytest", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------
# 'health' command

def which(executable):
    exe_path = shutil.which(executable)
    return exe_path if exe_path else "-"


def has_target(target):
    targets_to_kmodules = {
        "thin-pool": "dm_thin_pool",
        "thin": "dm_thin_pool",
        "cache": "dm_cache",
        "linear": "device_mapper",
        "bufio_test": "dm_bufio_test",
        "vdo": "dm_vdo",
    }
    stdout = subprocess.run(
        ["dmsetup", "targets"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    ).stdout
    if target in stdout:
        return True
    try:
        kmod = targets_to_kmodules[target]
    except KeyError:
        kmod = f"dm_{target}"
    return subprocess.run(
        ["modprobe", kmod],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).returncode == 0


test_dep_path = "./test_dependencies.toml"


def cmd_health(args, results):
    test_deps = dep.read_test_deps(test_dep_path)

    repo = os.getenv("DMTEST_KERNEL_SOURCE", "linux")
    found = "present" if os.path.isdir(os.path.join(repo, ".git")) else "missing"
    print("Kernel Repo:\n")
    print(f"{repo.ljust(40,'.')} {found}\n\n")

    print("Executables:\n")
    tools = test_deps.get_all_executables()
    for t in tools:
        print(f"{(t + ' ').ljust(40, '.')} {which(t)}")
    print("\n")

    print("Targets:\n")
    targets = test_deps.get_all_targets()
    for t in targets:
        found = "present" if has_target(t) else "missing"
        print(f"{t.ljust(40, '.')} {found}")


# -----------------------------------------
# Command line parser

def arg_filter(p):
    p.add_argument(
        "--rx",
        metavar="PATTERN",
        type=str,
        help="select tests that match the given regular expression",
        action="append",
    )
    p.add_argument(
        "substring",
        type=str,
        nargs="*",
        help="substring to filter tests",
    )
    p.add_argument(
        "--state",
        metavar="[^]TEST_STATE",
        type=str,
        help="select tests whose result matches the given state. Use '^' to invert the selection",
        action="append",
    )
    p.add_argument(
        "--and-filters",
        help="Select tests that match _all_ filters",
        action="store_true",
    )


def arg_result_set(p):
    p.add_argument(
        "--result-set",
        metavar="RESULT_SET",
        type=str,
        help="Specify a nickname for the kernel you are testing",
    )


def arg_run_nr(p):
    p.add_argument(
        "--run-nr",
        metavar="RUN_NR",
        type=int,
        help="Specify which run of a result set to use",
    )


def command_line_parser():
    parser = argparse.ArgumentParser(
        prog="dmtest", description="device-mapper test runner and result browser",
        fromfile_prefix_chars="@",
        epilog="Arguments starting with @ will be treated as files containing one argument per line.",
    )
    subparsers = parser.add_subparsers(
        title="command arguments",
        help="'{cmd} -h' for command specific options",
        metavar="command",
    )

    # --- run (wraps pytest) ---
    run_p = subparsers.add_parser("run", help="run tests via pytest")
    run_p.set_defaults(func=cmd_run)
    arg_result_set(run_p)
    run_p.add_argument(
        "pytest_args",
        nargs="*",
        help="arguments forwarded to pytest (e.g. -k 'vdo' -v)",
    )

    # --- result queries ---
    result_sets_p = subparsers.add_parser("result-sets", help="list result sets")
    result_sets_p.set_defaults(func=cmd_result_sets)

    result_set_delete_p = subparsers.add_parser(
        "result-set-delete", help="delete result set"
    )
    result_set_delete_p.set_defaults(func=cmd_result_set_delete)
    result_set_delete_p.add_argument("result_set", help="The result set to delete")

    result_set_rename_p = subparsers.add_parser(
        "result-set-rename", help="rename result set"
    )
    result_set_rename_p.set_defaults(func=cmd_result_set_rename)
    result_set_rename_p.add_argument(
        "old_result_set", help="The old result set name"
    )
    result_set_rename_p.add_argument(
        "new_result_set", help="The new result set name"
    )

    list_p = subparsers.add_parser("list", help="list test results")
    list_p.set_defaults(func=cmd_list)
    arg_filter(list_p)
    arg_result_set(list_p)
    arg_run_nr(list_p)

    log_p = subparsers.add_parser("log", help="list test logs")
    log_p.set_defaults(func=cmd_log)
    arg_filter(log_p)
    arg_result_set(log_p)
    arg_run_nr(log_p)
    log_p.add_argument(
        "--with-dmesg",
        help="Print the kernel log as well",
        action="store_true",
    )

    compare_p = subparsers.add_parser("compare", help="compare two result sets")
    compare_p.set_defaults(func=cmd_compare)
    arg_filter(compare_p)
    compare_p.add_argument(
        "--old-result-set",
        metavar="RESULT_SET",
        type=str,
        help="Old result set to compare against",
    )
    arg_result_set(compare_p)

    list_runs_p = subparsers.add_parser("list-runs", help="list each test run individually")
    list_runs_p.set_defaults(func=cmd_list_runs)
    arg_filter(list_runs_p)
    arg_result_set(list_runs_p)
    list_runs_p.add_argument(
        "--run-state",
        metavar="STATE",
        type=str,
        help="only show runs whose result matches the given state",
    )

    health_p = subparsers.add_parser(
        "health", help="check required tools are installed"
    )
    health_p.set_defaults(func=cmd_health)

    return parser


# -----------------------------------------
# Main

def main():
    parser = command_line_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)

    try:
        with db.TestResults("test_results.db") as results:
            args.func(args, results)
    except BrokenPipeError:
        os._exit(0)


if __name__ == "__main__":
    main()
