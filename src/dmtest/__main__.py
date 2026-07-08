import argparse
import dmtest.bufio.bufio_tests as bufio
import dmtest.db as db
import dmtest.fixture
import dmtest.test_register as test_register
import dmtest.blk_archive.rolling_snaps as blk_archive
import dmtest.blk_archive.unit as blk_archive_unit
import dmtest.cache.register as cache_register
import dmtest.thin.register as thin_register
import dmtest.thin_migrate.register as thin_migrate_register
import dmtest.vdo.register as vdo_register
import dmtest.dependency_tracker as dep
import dmtest.config as config
import dmtest.test_filter as filter
import dmtest.tag_expression as tag_expr
from dmtest.utils import get_dmesg_log
import io
import itertools
import logging as log
import os
import sys
import time
import traceback
import subprocess
import shutil
from typing import Optional, NamedTuple, Sequence


class TreeFormatter:
    INDENT = "  "

    def __init__(self):
        self._previous = []

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
                strs.append(f"{self.INDENT * depth}{new}".ljust(50, " ") + "\n")
            depth += 1
        self._previous = components
        return "".join(strs)[:-1]

    def depth(self):
        return len(self._previous)


# -----------------------------------------
# 'result set' should come from command line
# or environment.


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
# 'result-sets' command


def cmd_result_sets(tests: test_register.TestRegister, args, results: db.TestResults):
    for rs in results.get_result_sets():
        print(f"    {rs}")


# -----------------------------------------
# 'result-set-delete' command


def cmd_result_set_delete(
    tests: test_register.TestRegister, args, results: db.TestResults
):
    try:
        results.delete_result_set(args.result_set)
    except db.NoSuchResultSet:
        print(f"No such result set '{args.result_set}'", file=sys.stderr)


# -----------------------------------------
# -----------------------------------------
# 'result-set-rename' command


def cmd_result_set_rename(
    tests: test_register.TestRegister, args, results: db.TestResults
):
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


def cmd_list(tests: test_register.TestRegister, args, results: db.TestResults):
    result_set = get_result_set(args)
    filter = build_filter(args)
    paths = sorted(tests.paths(results, result_set, filter))
    formatter = TreeFormatter()

    if len(paths) == 0:
        print("No matching tests found.")

    for p in paths:
        print(f"{formatter.tree_line(p)}", end=" ")
        result = average_results(results.get_test_results(p, result_set, args.run_nr))
        if result is None:
            print("-")
        elif result.nr_runs == 1:
            print(f"{result.pass_fail} [{result.duration:.2f}s]")
        elif result.pass_fail:
            print(f"{result.nr_runs}/{result.nr_runs} {result.pass_fail} [{result.duration:.2f}s]")
        else:
            print(f"{result.nr_pass}/{result.nr_runs} PASS [{result.duration:.2f}s]")
        if args.show_tags:
            tags = tests.get_tags(p)
            if tags:
                indent = formatter.INDENT * formatter.depth()
                print(f"{indent}[{', '.join(sorted(tags))}]")


# -----------------------------------------
# 'log' command


def cmd_log(tests: test_register.TestRegister, args, results: db.TestResults):
    result_set = get_result_set(args)
    filter = build_filter(args)
    paths = sorted(tests.paths(results, result_set, filter))

    if len(paths) == 0:
        print("No matching tests found.")

    for p in paths:
        res_list = results.get_test_results(p, result_set, args.run_nr)
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

def can_compare_times(old: Optional[AvgResult], new: Optional[AvgResult]) -> bool:
    if old is None or new is None:
        return False
    if old.nr_pass != 0 and new.nr_pass != 0:
        return True
    return old.pass_fail and old.pass_fail == new.pass_fail


def cmd_compare(tests: test_register.TestRegister, args, results: db.TestResults):
    if not args.old_result_set:
        print("Missing old result set.", file=sys.stderr)
        sys.exit(1)
    new_set = get_result_set(args)
    filter = build_filter(args)
    paths = sorted(tests.paths(results, new_set, filter))
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

def cmd_list_runs(tests: test_register.TestRegister, args, results: db.TestResults):
    result_set = get_result_set(args)
    filter = build_filter(args)
    paths = sorted(tests.paths(results, result_set, filter))
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
# 'run' command

test_dep_path = "./test_dependencies.toml"


# Used to implement the --log switch
class StringIOWithStderr(io.StringIO):
    def write(self, s):
        # Write to StringIO buffer
        super().write(s)

        # Also write to stdout
        sys.stderr.write(s)


def cmd_run(tests: test_register.TestRegister, args, results: db.TestResults):

    exit_code = 0

    test_deps = dep.read_test_deps(test_dep_path)

    result_set = get_result_set(args)

    if args.nr_runs < 1:
        print("--nr-runs must be at least 1")
        return

    # select tests
    filter = build_filter(args)
    paths = sorted(tests.paths(results, result_set, filter))

    if len(paths) == 0:
        print("No matching tests found.")

    # Set up the logging
    if args.log:
        buffer = StringIOWithStderr()
    else:
        buffer = io.StringIO()

    log.basicConfig(
        level=log.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=buffer,
    )

    for run_nr in range(args.nr_runs):
        formatter = TreeFormatter()
        if args.nr_runs > 1:
            print(f"*** Run: {run_nr} ***")
        for p in paths:
            buffer.seek(0)
            buffer.truncate()

            print(f"{formatter.tree_line(p)}", end=" ", flush=True)
            log.info(f"Running '{p}'")

            fix = dmtest.fixture.Fixture()
            passed = True
            missing_dep = None
            start = time.time()
            try:
                with dep.dep_tracker() as tracker:
                    old_deps = test_deps.get_deps(p)
                    tests.check_deps(old_deps)
                    tests.run(p, fix)
                    exes = tracker.executables
                    targets = tracker.targets
                    test_deps.set_deps(p, exes, targets)

            except test_register.MissingTestDep as e:
                missing_dep = e

            except Exception as e:
                passed = False
                exit_code = 1
                if bool(os.getenv("DMTEST_PY_VERBOSE_TB", False)):
                    log.error(f"Exception caught: \n{traceback.format_exc()}\n")
                else:
                    log.error(f"Exception caught: {e}")
                while e.__cause__ or e.__context__:
                    if e.__cause__:
                        e = e.__cause__
                    else:
                        e = e.__context__
                    log.error(f"Triggered while handling Exception: {e}")
            elapsed = time.time() - start

            dmesg_log = get_dmesg_log(start)
            if "BUG" in dmesg_log:
                log.error("BUG in kernel log, see dmesg for more info")
                passed = False
                exit_code = 2

            pass_str = None
            if missing_dep:
                log.info(f"Missing dependency: {missing_dep}")
                print(f"MISSING_DEP [{missing_dep}]")
                pass_str = "MISSING_DEP"
            elif passed:
                print(f"PASS [{elapsed:.2f}s]")
                pass_str = "PASS"
            else:
                print("FAIL")
                pass_str = "FAIL"

            test_log = buffer.getvalue()
            result = db.TestResult(p, pass_str, test_log, dmesg_log, result_set, elapsed, run_nr)
            results.insert_test_result(result, with_delete=(run_nr == 0))

    dep.write_test_deps(test_dep_path, test_deps)
    os._exit(exit_code)

# -----------------------------------------
# 'health' command


def which(executable):
    exe_path = shutil.which(executable)
    return exe_path if exe_path else "-"


def cmd_health(tests: test_register.TestRegister, args, results):
    test_deps = dep.read_test_deps(test_dep_path)

    print("Kernel Repo:\n")
    repo = os.getenv("DMTEST_KERNEL_SOURCE", "linux")
    found = "present" if test_register.has_repo(repo) else "missing"
    print(f"{repo.ljust(40,'.')} {found}\n\n")

    print("Executables:\n")
    tools = test_deps.get_all_executables()
    for t in tools:
        print(f"{(t + ' ').ljust(40, '.')} {which(t)}")
    print("\n")

    print("Targets:\n")
    targets = test_deps.get_all_targets()
    for t in targets:
        found = "present" if test_register.has_target(t) else "missing"
        print(f"{t.ljust(40, '.')} {found}")


# -----------------------------------------
# 'list-tags' command


def cmd_list_tags(tests: test_register.TestRegister, args, results: db.TestResults):
    tags = tests.count_tags()
    if not tags:
        print("No tags defined.")
        return
    for tag, count in sorted(tags.items()):
        print(f"  {tag.ljust(20)} {count} tests")


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
    p.add_argument(
        "--tags",
        metavar="EXPRESSION",
        type=str,
        help="select tests matching the tag expression (e.g. '!experimental')",
    )


def build_filter(args):
    if args.and_filters:
        top_filter = filter.AndFilter()
    else:
        top_filter = filter.OrFilter()

    for pat in args.rx or []:
        top_filter.add_sub_filter(filter.RegexFilter(pat))

    for ss in args.substring or []:
        top_filter.add_sub_filter(filter.SubstringFilter(ss))

    for s in args.state or []:
        if len(s) >= 1 and s.startswith("^"):
            top_filter.add_sub_filter(filter.NotFilter(filter.StateFilter(s[1:])))
        else:
            top_filter.add_sub_filter(filter.StateFilter(s))

    # CLI --tags overrides config [filter].tags
    tag_expression = getattr(args, "tags", None)
    if tag_expression is None:
        cfg = config.read_config()
        tag_expression = cfg.get("tags")

    if tag_expression:
        matcher = tag_expr.parse_tag_expression(tag_expression)
        combined = filter.AndFilter()
        combined.add_sub_filter(top_filter)
        combined.add_sub_filter(filter.TagFilter(matcher))
        return combined

    return top_filter


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
        prog="dmtest", description="run device-mapper tests",
        fromfile_prefix_chars="@", epilog="Arguments starting with @ will be treaded as files containing one argument per line, and will be replaced with the arguments they contain.",
    )
    subparsers = parser.add_subparsers(
        title="command arguments",
        help="'{cmd} -h' for command specific options",
        metavar="command",
    )

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
    list_p.add_argument(
        "-T",
        help="Show tags alongside test names",
        action="store_true",
        dest="show_tags",
    )

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

    run_p = subparsers.add_parser("run", help="run tests")
    run_p.set_defaults(func=cmd_run)
    arg_filter(run_p)
    arg_result_set(run_p)
    run_p.add_argument(
        "--nr-runs",
        metavar="NR_RUNS",
        type=int,
        default=1,
        help="The number of times to run the tests",
    )
    run_p.add_argument(
        "--log",
        help="Print the log to stdout",
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

    list_tags_p = subparsers.add_parser("list-tags", help="list all tags with test counts")
    list_tags_p.set_defaults(func=cmd_list_tags)

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

    tests = test_register.TestRegister()
    blk_archive.register(tests)
    blk_archive_unit.register(tests)
    cache_register.register(tests)
    thin_register.register(tests)
    thin_migrate_register.register(tests)
    bufio.register(tests)
    vdo_register.register(tests)

    try:
        with db.TestResults("test_results.db") as results:
            args.func(tests, args, results)
    except BrokenPipeError:
        os._exit(0)


if __name__ == "__main__":
    main()
