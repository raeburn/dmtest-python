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
    tags: frozenset = frozenset()


def _parse_test_entry(entry):
    if len(entry) == 2:
        path, callback = entry
        return path, callback, None, None

    if len(entry) == 3:
        path, callback, tags = entry
        return path, callback, tags, None

    if len(entry) == 4:
        path, callback, tags, dep_fn = entry
        return path, callback, tags, dep_fn

    raise ValueError(f"test entry must have 2-4 elements, got {len(entry)}")


class TestRegister:
    def __init__(self):
        self._tests = {}

    def register(self, path, callback, tags=None, dep_fn=None):
        path = _normalise_path(path)
        if tags is not None and not isinstance(tags, list):
            raise TypeError(f"test {path}: tags must be a list")
        if dep_fn is not None and not callable(dep_fn):
            raise TypeError(f"test {path}: dep_fn must be callable")
        self._tests[path] = Test(dep_fn, callback, frozenset(tags or []))

    def register_batch(self, prefix, tests, batch_tags=None, batch_dep_fn=None):
        # ensure a trailing slash
        prefix = str(prefix)
        if not prefix.endswith("/"):
            prefix += "/"

        if batch_tags is not None and not isinstance(batch_tags, list):
            raise TypeError(f"test {prefix}: batch_tags must be a list")

        for entry in tests:
            path, callback, tags, dep_fn = _parse_test_entry(entry)
            merged_tags = (batch_tags or []) + (tags or [])
            dep_fn = dep_fn or batch_dep_fn
            self.register(prefix + path.lstrip("/"), callback, merged_tags, dep_fn)

    def paths(self, results, result_set, filt=None):
        selected = []

        for (path, t) in self._tests.items():
            res_list = results.get_test_results(path, result_set)
            if filt.matches(path, t, res_list):
                selected.append(path)

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

    def get_tags(self, path):
        t = self._tests.get(path)
        return t.tags if t else frozenset()


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
