import tomllib
import tomli_w
from enum import Enum
from pathlib import Path
from typing import Union

from contextlib import contextmanager


class DepTracker:
    def __init__(self, executables=(), targets=()):
        self._executables = set(executables)
        self._targets = set(targets)

    def add_executable(self, exe):
        self._executables.add(exe)

    def add_target(self, t):
        self._targets.add(t)

    @property
    def executables(self):
        return sorted(self._executables)

    @property
    def targets(self):
        return sorted(self._targets)


class TestDeps:
    def __init__(self):
        self._deps = {}
        self._updated = False

    def get_deps(self, test_name: str) -> DepTracker:
        try:
            dep = self._deps[test_name]
            return DepTracker(dep["executables"], dep["targets"])
        except KeyError:
            return DepTracker()

    def set_deps(self, test_name, exes, targets):
        new_dep = {"executables": exes, "targets": targets}
        if (test_name not in self._deps) or (self._deps[test_name] != new_dep):
            self._updated = True
            self._deps[test_name] = new_dep

    def get_all_executables(self):
        r = set()
        for d in self._deps.values():
            r.update(d["executables"])

        return sorted(r)

    def get_all_targets(self):
        r = set()
        for d in self._deps.values():
            r.update(d["targets"])

        return sorted(r)


def read_test_deps(path):
    deps = TestDeps()
    with open(path, "rb") as f:
        deps._deps = tomllib.load(f)
    return deps


def write_test_deps(path, deps):
    if deps._updated:
        sorted_deps = dict(sorted(deps._deps.items()))
        with open(path, "wb") as f:
            tomli_w.dump(sorted_deps, f)


global_dep_tracker = None


@contextmanager
def dep_tracker():
    global global_dep_tracker

    assert not global_dep_tracker
    global_dep_tracker = DepTracker()
    try:
        yield global_dep_tracker
    finally:
        global_dep_tracker = None


def add_exe(name):
    global global_dep_tracker
    if global_dep_tracker:
        global_dep_tracker.add_executable(name)


def add_target(name):
    global global_dep_tracker
    if global_dep_tracker:
        global_dep_tracker.add_target(name)
