import dmtest.device_mapper.dev as dmdev
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.pattern_stomper as stomper
import dmtest.pool_stack as ps
import dmtest.utils as utils
import dmtest.units as units
import dmtest.tvm as tvm
import logging as log

from contextlib import contextmanager

import pudb

# ------------------------------


class ExternalSnapStack:
    def __init__(self, data_dev, metadata_dev, **opts):
        self.opts = opts
        self.metadata_dev = metadata_dev
        self.data_dev = data_dev

        self.md_tvm = tvm.VM()
        self.md_tvm.add_allocation_volume(self.metadata_dev)
        self.md_tvm.add_volume(tvm.LinearVolume("md", self.metadata_size))
        log.info("metadata tvm complete")

        log.info(
            f"data_dev = {data_dev}, size = {utils.dev_size(data_dev)}, origin_size = {self.origin_size}, data_size = {self.data_size}"
        )
        self.data_tvm = tvm.VM()
        self.data_tvm.add_allocation_volume(self.data_dev)
        self.data_tvm.add_volume(tvm.LinearVolume("origin", self.origin_size))
        self.data_tvm.add_volume(tvm.LinearVolume("data", self.data_size))
        log.info("data tvm complete")

    @property
    def metadata_size(self):
        return self.opts.get("metadata_size", units.meg(4))

    @property
    def origin_size(self):
        return self.opts.get("origin_size", units.meg(512))

    @property
    def thin_size(self):
        return self.opts.get("thin_size", self.origin_size)

    @property
    def data_size(self):
        return self.opts.get("data_size", units.gig(2))

    @contextmanager
    def activate_origin(self):
        with dmdev.dev(self.data_tvm.table("origin")) as origin:
            self.origin = origin
            yield origin

    @contextmanager
    def activate_thin(self):
        with dmdev.dev(self.md_tvm.table("md")) as md:
            with dmdev.dev(self.data_tvm.table("data")) as data:
                self.pool_stack = ps.PoolStack(data, md, **self.opts)
                with self.pool_stack.activate() as pool:
                    with ps.new_thin(
                        pool, self.thin_size, 0, origin=self.origin
                    ) as thin:
                        yield thin


def do_pattern_stamp_test(fix, opts=None):
    opts = opts or {}
    opts["data_size"] = opts.get("data_size", units.gig(4))
    opts["origin_size"] = opts.get("origin_size", units.gig(1))

    cfg = fix.cfg

    s = ExternalSnapStack(cfg["data_dev"], cfg["metadata_dev"], **opts)

    with s.activate_origin() as origin:
        origin_stomper = stomper.PatternStomper(
            origin.path, units.kilo(64), need_zero=True
        )
        origin_stomper.stamp(20)

        with s.activate_thin() as thin:
            origin_stomper.verify(0, 1)

            cache_stomper = origin_stomper.fork(thin.path)
            cache_stomper.verify(0, 1)

            cache_stomper.stamp(10)
            cache_stomper.verify(0, 2)

            origin_stomper.verify(0, 1)


def test_snap_equal_size(fix):
    do_pattern_stamp_test(fix)


def test_snap_smaller_than_origin(fix):
    do_pattern_stamp_test(fix, {"thin_size": units.meg(512)})


def test_snap_bigger_than_origin(fix):
    do_pattern_stamp_test(fix, {"thin_size": units.gig(2)})


def test_snap_fractional_tail_block(fix):
    do_pattern_stamp_test(fix, {"origin_size": units.gig(1) + 16})
