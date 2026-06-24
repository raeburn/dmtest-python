import os
import shutil
import subprocess
import time

import pytest

import dmtest.db as db
import dmtest.dependency_tracker as dep
import dmtest.fixture
from dmtest.utils import get_dmesg_log


# ---------------------------------------------------------------------------
# Target / executable helpers (moved from test_register.py)
# ---------------------------------------------------------------------------

targets_to_kmodules = {
    "thin-pool": "dm_thin_pool",
    "thin": "dm_thin_pool",
    "cache": "dm_cache",
    "linear": "device_mapper",
    "bufio_test": "dm_bufio_test",
    "vdo": "dm_vdo",
}


def has_target(target):
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


def has_repo(path):
    return os.path.isdir(os.path.join(path, ".git"))


def check_linux_repo():
    path = os.getenv("DMTEST_KERNEL_SOURCE", "linux")
    return has_repo(path)


# ---------------------------------------------------------------------------
# Session-level state
# ---------------------------------------------------------------------------

test_dep_path = "./test_dependencies.toml"
_dmesg_records = {}
_test_deps = None
_db_results = None


# ---------------------------------------------------------------------------
# pytest options
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--result-set", default=None,
        help="Name for the result set (also reads DMTEST_RESULT_SET env var)",
    )
    parser.addoption(
        "--log-to-stderr", action="store_true", default=False,
        help="Also print test log output to stderr",
    )


def _get_result_set(config):
    rs = config.getoption("--result-set")
    if rs:
        return rs
    return os.environ.get("DMTEST_RESULT_SET")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fix():
    return dmtest.fixture.Fixture()


def _get_test_dm_devices():
    result = subprocess.run(
        ["dmsetup", "ls", "--noheadings"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    devs = []
    for line in result.stdout.splitlines():
        name = line.split()[0] if line.strip() else ""
        if name.startswith("test-dev-"):
            devs.append(name)
    return devs


@pytest.fixture(autouse=True)
def check_dm_devices():
    stale = _get_test_dm_devices()
    if stale:
        raise RuntimeError(
            f"test-dev-* dm devices already present at test start: {stale}"
        )
    yield
    leaked = _get_test_dm_devices()
    if leaked:
        for name in leaked:
            subprocess.run(
                ["dmsetup", "remove", "-f", name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        pytest.fail(f"test leaked dm devices (now removed): {leaked}")


@pytest.fixture(autouse=True)
def check_dmesg(request):
    start = time.time()
    yield
    dmesg = get_dmesg_log(start)
    _dmesg_records[request.node.nodeid] = dmesg
    if "BUG" in dmesg:
        pytest.fail("BUG found in kernel dmesg log")


@pytest.fixture(autouse=True)
def track_deps(request):
    global _test_deps
    with dep.dep_tracker() as tracker:
        yield
        if _test_deps is not None:
            _test_deps.set_deps(
                request.node.nodeid,
                tracker.executables,
                tracker.targets,
            )


# ---------------------------------------------------------------------------
# Dependency pre-check
# ---------------------------------------------------------------------------

def pytest_runtest_setup(item):
    global _test_deps
    if _test_deps is None:
        return

    # Check needs_linux_repo marker
    if item.get_closest_marker("needs_linux_repo"):
        if not check_linux_repo():
            path = os.getenv("DMTEST_KERNEL_SOURCE", "linux")
            pytest.skip(f"requires linux git repository at {path}")

    stored = _test_deps.get_deps(item.nodeid)
    for target in stored.targets:
        if not has_target(target):
            pytest.skip(f"missing dm target: {target}")
    for exe in stored.executables:
        if shutil.which(exe) is None:
            pytest.skip(f"missing executable: {exe}")


# ---------------------------------------------------------------------------
# Session setup / teardown
# ---------------------------------------------------------------------------

def pytest_sessionstart(session):
    global _test_deps, _db_results

    try:
        _test_deps = dep.read_test_deps(test_dep_path)
    except Exception:
        _test_deps = dep.TestDeps()

    result_set = _get_result_set(session.config)
    if result_set:
        _db_results = db.TestResults("test_results.db")


def pytest_sessionfinish(session, exitstatus):
    global _test_deps, _db_results

    if _test_deps is not None:
        dep.write_test_deps(test_dep_path, _test_deps)

    if _db_results is not None:
        _db_results.__exit__(None, None, None)
        _db_results = None


# ---------------------------------------------------------------------------
# Result storage
# ---------------------------------------------------------------------------

@pytest.hookimpl(trylast=True)
def pytest_runtest_makereport(item, call):
    if call.when != "call":
        return

    result_set = _get_result_set(item.config)
    if not result_set or _db_results is None:
        return

    passed = call.excinfo is None
    pass_fail = "PASS" if passed else "FAIL"
    duration = call.duration

    # Collect captured log
    test_log = ""
    if hasattr(item, "_report_sections"):
        for when, key, content in item._report_sections:
            if "log" in key:
                test_log += content

    dmesg = _dmesg_records.get(item.nodeid, "")

    result = db.TestResult(
        test_name=item.nodeid,
        pass_fail=pass_fail,
        log=test_log,
        dmesg=dmesg,
        result_set=result_set,
        duration=duration,
        run_nr=0,
    )
    _db_results.insert_test_result(result, with_delete=True)
