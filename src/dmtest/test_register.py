import os
import re
import shutil
import subprocess
import dmtest.fixture as fixture
import dmtest.process as process
import dmtest.dependency_tracker as dep

from typing import NamedTuple, Callable, Optional


def _normalise_path(p):
    if not p.startswith("/"):
        return "/" + p
    else:
        return p


def _build_predicate_regex(pats):
    regexes = [re.compile(regex) for regex in pats]

    def predicate(s):
        return any(regex.search(s) for regex in regexes)

    return predicate


class MissingTestDep(Exception):
    pass


class Test(NamedTuple):
    dep_fn: Callable[[], None]
    test_fn: Callable[[fixture.Fixture], None]


def _parse_test_entry(entry):
    if len(entry) == 2:
        path, callback = entry
        return path, callback, None

    if len(entry) == 3:
        path, callback, dep_fn = entry
        return path, callback, dep_fn

    raise ValueError(f"test entry must have 2-3 elements, got {len(entry)}")


class TestRegister:
    def __init__(self):
        self._tests = {}

    def register(self, path, callback, dep_fn=None):
        path = _normalise_path(path)
        self._tests[path] = Test(dep_fn, callback)

    def register_batch(self, prefix, tests, batch_dep_fn=None):
        # ensure a trailing slash
        prefix = str(prefix)
        if not prefix.endswith("/"):
            prefix += "/"

        for entry in tests:
            path, callback, dep_fn = _parse_test_entry(entry)
            dep_fn = dep_fn or batch_dep_fn
            self.register(prefix + path.lstrip("/"), callback, dep_fn)

    def paths(self, results, result_set, filt=None):
        selected = []

        for t in self._tests.keys():
            res_list = results.get_test_results(t, result_set)
            if filt.matches(t, res_list):
                selected.append(t)

        return selected

    def check_deps(self, deps: dep.DepTracker):
        for target in deps.targets:
            if not has_target(target):
                raise MissingTestDep(f"{target} target")
        for exe in deps.executables:
            if shutil.which(exe) is None:
                raise MissingTestDep(f"{exe} executable")

    def run(self, path, fix):
        t = self._tests[path]
        if t:
            if t.dep_fn:
                t.dep_fn()
            t.test_fn(fix)
        else:
            raise ValueError(f"can't find test {path}")


targets_to_kmodules = {
    "thin-pool": "dm_thin_pool",
    "thin": "dm_thin_pool",
    "cache": "dm_cache",
    "linear": "device_mapper",
    "bufio_test": "dm_bufio_test",
    "vdo": "dm_vdo",
}


def has_target(target: str) -> bool:
    # It may already be loaded or compiled in
    stdout = subprocess.run(
        ["dmsetup", "targets"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    ).stdout
    if target in stdout:
        return True

    try:
        kmod = targets_to_kmodules[target]
    except KeyError:
        kmod = f"dm_{target}"

    return subprocess.run(
        ["modprobe", kmod],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).returncode == 0


def has_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def check_linux_repo():
    path = os.getenv("DMTEST_KERNEL_SOURCE", "linux")
    if not has_repo(path):
        raise MissingTestDep(f"{path} repository")
