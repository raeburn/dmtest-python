import dmtest.device_mapper.dev as dmdev
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.tvm as tvm
import dmtest.units as units
import dmtest.utils as utils
import enum
import logging as log
import mmap
import os
import random
import struct
import threading
import time

from contextlib import contextmanager


class Instructions(enum.IntEnum):
    I_HALT = 0
    I_LIT = 1
    I_SUB = 2
    I_ADD = 3
    I_NEW_BUF = 4
    I_READ_BUF = 5
    I_GET_BUF = 6
    I_PUT_BUF = 7
    I_MARK_DIRTY = 8
    I_WRITE_ASYNC = 9
    I_WRITE_SYNC = 10
    I_FLUSH = 11
    I_FORGET = 12
    I_FORGET_RANGE = 13
    I_LOOP = 14
    I_STAMP = 15
    I_VERIFY = 16
    I_CHECKPOINT = 17


class BufioProgram:
    def __init__(self):
        self._bytes = b""
        self._labels = {}
        self._reg_alloc = 0

    def compile(self):
        return self._bytes[:]

    def alloc_reg(self):
        reg = self._reg_alloc
        self._reg_alloc += 1
        return reg

    def label(self):
        return len(self._bytes)

    def halt(self):
        self._bytes += struct.pack("=B", Instructions.I_HALT)

    def lit(self, val, reg):
        self._bytes += struct.pack("=BIB", Instructions.I_LIT, val, reg)

    def sub(self, reg1, v):
        self._bytes += struct.pack("=BBB", Instructions.I_SUB, reg1, v)

    def add(self, reg1, v):
        self._bytes += struct.pack("=BBB", Instructions.I_ADD, reg1, v)

    def inc(self, reg1):
        self.add(reg1, 1)

    def new_buf(self, block_reg, dest_reg):
        self._bytes += struct.pack("=BBB", Instructions.I_NEW_BUF, block_reg, dest_reg)

    def read_buf(self, block_reg, dest_reg):
        self._bytes += struct.pack("=BBB", Instructions.I_READ_BUF, block_reg, dest_reg)

    def get_buf(self, block_reg, dest_reg):
        self._bytes += struct.pack("=BBB", Instructions.I_GET_BUF, block_reg, dest_reg)

    def put_buf(self, reg):
        self._bytes += struct.pack("=BB", Instructions.I_PUT_BUF, reg)

    def mark_dirty(self, reg):
        self._bytes += struct.pack("=BB", Instructions.I_MARK_DIRTY, reg)

    def write_async(self):
        self._bytes += struct.pack("=B", Instructions.I_WRITE_ASYNC)

    def write_sync(self):
        self._bytes += struct.pack("=B", Instructions.I_WRITE_SYNC)

    def flush(self):
        self._bytes += struct.pack("=B", Instructions.I_FLUSH)

    def forget(self, block):
        self._bytes += struct.pack("=BI", Instructions.I_FORGET, block)

    def forget_range(self, block, len):
        self._bytes += struct.pack("=BII", Instructions.I_FORGET_RANGE, block, len)

    def loop(self, addr, count):
        self._bytes += struct.pack("=BHB", Instructions.I_LOOP, addr, count)

    def stamp(self, buf_reg, pattern_reg):
        self._bytes += struct.pack("=BBB", Instructions.I_STAMP, buf_reg, pattern_reg)

    def verify(self, buf_reg, pattern_reg):
        self._bytes += struct.pack("=BBB", Instructions.I_VERIFY, buf_reg, pattern_reg)

    def checkpoint(self, reg):
        self._bytes += struct.pack("=BB", Instructions.I_CHECKPOINT, reg)


@contextmanager
def loop(p, nr_times):
    loop_counter = p.alloc_reg()
    p.lit(nr_times - 1, loop_counter)
    addr = p.label()
    try:
        yield p
    finally:
        p.loop(addr, loop_counter)


def exec_program(dev, program):
    bytes = program.compile()
    if len(bytes) > 4096:
        raise ValueError("buffer is too large")

    fd = os.open(dev.path, os.O_DIRECT | os.O_WRONLY)
    try:
        # Map a single page of memory to the file
        page_size = os.sysconf("SC_PAGE_SIZE")
        with mmap.mmap(-1, page_size) as mem:
            mem.write(bytes)
            with utils.timed("bufio program"):
                os.write(fd, mem)
    finally:
        os.close(fd)


class Code:
    def __init__(self, thread_set):
        self._thread_set = thread_set
        self._code = BufioProgram()

    def __enter__(self):
        return self._code

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            return

        self._code.halt()
        self._thread_set.add_thread(self._code)


class ThreadSet:
    def __init__(self, dev):
        self._dev = dev
        self._programs = []

    def program(self):
        return Code(self)

    def add_thread(self, code):
        self._programs.append(code)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            return

        threads = []

        for code in self._programs:
            tid = threading.Thread(target=exec_program, args=(self._dev, code))
            threads.append(tid)

        for tid in threads:
            tid.start()

        for tid in threads:
            tid.join()


def _sys_param(name: str) -> str:
    return f"/sys/module/dm_bufio/parameters/{name}"


def read_sys_param(name: str) -> int:
    with open(_sys_param(name), "r") as file:
        return int(file.read().strip())


def write_sys_param(name: str, value: str):
    with open(_sys_param(name), "w") as file:
        return file.write(value)


class BufioParams:
    @property
    def peak_allocated(self):
        return read_sys_param("peak_allocated_bytes")

    @peak_allocated.setter
    def peak_allocated(self, value):
        log.info(f"setting peak_allocated_bytes={value}")
        write_sys_param("peak_allocated_bytes", str(value))

    @property
    def current_allocated(self):
        return read_sys_param("current_allocated_bytes")

    @property
    def max_cache_size(self):
        return read_sys_param("max_cache_size_bytes")

    @max_cache_size.setter
    def max_cache_size(self, value):
        log.info(f"setting max_cache_size={value}")
        write_sys_param("max_cache_size_bytes", str(value))

    @property
    def max_age(self):
        return read_sys_param("max_age_seconds")

    @max_age.setter
    def max_age(self, value):
        log.info(f"setting max_age_seconds={value}")
        write_sys_param("max_age_seconds", str(value))


@contextmanager
def bufio_params_tracker(cache_size=units.meg(300), max_age=300):
    def worker(p, stop_event):
        while not stop_event.is_set():
            log.info(
                f"bufio cache size: {p.current_allocated // (1024 * 1024)}m/{p.max_cache_size // (1024 * 1024)}m"
            )
            time.sleep(0.5)

    p = BufioParams()

    # we must always set these, becuase prior tests may
    # have set them to something other than the default.
    p.max_cache_size = cache_size * 512
    p.max_age = max_age
    p.peak_allocated = 0

    stop_event = threading.Event()
    tid = threading.Thread(
        target=worker,
        args=(
            p,
            stop_event,
        ),
    )
    try:
        tid.start()
        yield p
    finally:
        stop_event.set()
        tid.join()

        # check max_cache_size was observed.  We can only do this roughly since
        # it takes time for the cleaner to kick in).
        if p.peak_allocated > int(p.max_cache_size * units.meg(512) * 512):
            raise ValueError(
                f"bufio max cache size exceeded: max = {p.max_cache_size // (1024 * 1024)}m, peak = {p.peak_allocated // (1024 * 1024)}m"
            )


# Activate bufio test device and create a thread set.  max_cache_size is given
# in sectors
@contextmanager
def bufio_tester(data_dev, **opts):
    data_size = utils.dev_size(data_dev)
    t = table.Table(targets.BufioTestTarget(data_size, data_dev))

    with bufio_params_tracker(**opts):
        with dmdev.dev(t) as dev:
            with ThreadSet(dev) as thread_set:
                yield thread_set


# -----------------------------------------------


def test_create(fix):
    with bufio_tester(fix.cfg["data_dev"]):
        pass


def test_empty_program(fix):
    with bufio_tester(fix.cfg["data_dev"]) as tester:
        with tester.program():
            pass


def do_new_buf(p, base):
    block = p.alloc_reg()
    buf = p.alloc_reg()

    p.lit(base, block)

    with loop(p, 1024):
        p.new_buf(block, buf)
        p.put_buf(buf)
        p.inc(block)


def test_new_buf(fix):
    nr_threads = 16
    nr_gets = 1024

    with bufio_tester(fix.cfg["data_dev"]) as tester:
        for t in range(nr_threads):
            with tester.program() as p:
                do_new_buf(p, t * nr_gets)


def test_stamper(fix):
    with bufio_tester(fix.cfg["data_dev"]) as tester:
        with tester.program() as p:
            block = p.alloc_reg()
            buf = p.alloc_reg()
            pattern = p.alloc_reg()

            p.lit(0, block)
            p.lit(random.randint(0, 1024), pattern)

            with loop(p, 1024):
                # stamp
                p.new_buf(block, buf)
                p.stamp(buf, pattern)
                p.mark_dirty(buf)
                p.put_buf(buf)

                # write
                p.write_sync()
                p.forget(block)

                # re-read and verify
                p.read_buf(block, buf)
                p.verify(buf, pattern)
                p.put_buf(buf)

                p.inc(block)
                p.inc(pattern)


def do_stamper(p, base):
    block = p.alloc_reg()
    buf = p.alloc_reg()
    pattern = p.alloc_reg()

    p.lit(base, block)
    p.lit(random.randint(0, 1024), pattern)

    with loop(p, 1024):
        # stamp
        p.new_buf(block, buf)
        p.stamp(buf, pattern)
        p.mark_dirty(buf)
        p.put_buf(buf)

        # write
        p.write_sync()
        p.forget(block)

        # re-read and verify
        p.read_buf(block, buf)
        p.verify(buf, pattern)
        p.put_buf(buf)

        p.inc(block)
        p.inc(pattern)


def test_many_stampers(fix):
    nr_threads = 16
    nr_gets = 1024

    with bufio_tester(fix.cfg["data_dev"]) as tester:
        for t in range(nr_threads):
            with tester.program() as p:
                do_stamper(p, t * nr_gets)


def test_writeback_nothing(fix):
    data_dev = fix.cfg["data_dev"]
    nr_blocks = units.meg(512) // units.kilo(4)

    with bufio_tester(data_dev) as tester:
        with tester.program() as p:
            block = p.alloc_reg()
            buf = p.alloc_reg()

            p.lit(0, block)
            p.checkpoint(0)

            # read data, but don't dirty it
            with loop(p, nr_blocks):
                p.read_buf(block, buf)
                p.put_buf(buf)
                p.inc(block)

            # write back, should do nothing
            p.checkpoint(1)
            p.write_sync()
            p.checkpoint(2)


def do_writes_hit_disk(fix, write_method):
    data_dev = fix.cfg["data_dev"]
    nr_blocks = units.meg(128) // units.kilo(4)
    pattern_base = random.randint(0, 10240)

    # write pattern across disk
    with bufio_tester(data_dev) as tester:
        with tester.program() as p:
            block = p.alloc_reg()
            buf = p.alloc_reg()
            pattern = p.alloc_reg()

            p.lit(0, block)
            p.lit(pattern_base, pattern)

            with loop(p, nr_blocks):
                # stamp
                p.new_buf(block, buf)
                p.stamp(buf, pattern)
                p.mark_dirty(buf)
                p.put_buf(buf)

                p.inc(block)
                p.inc(pattern)

            # write
            write_method(p)

    # we teardown the tester and recreate to be sure
    # the writes have hit the disk before we verify.
    with bufio_tester(data_dev) as tester:
        with tester.program() as p:
            block = p.alloc_reg()
            buf = p.alloc_reg()
            pattern = p.alloc_reg()

            p.lit(0, block)
            p.lit(pattern_base, pattern)

            with loop(p, nr_blocks):
                p.read_buf(block, buf)
                p.verify(buf, pattern)
                p.put_buf(buf)

                p.inc(block)
                p.inc(pattern)


def test_writes_hit_disk_sync(fix):
    do_writes_hit_disk(fix, lambda p: p.write_sync())


def test_writes_hit_disk_async(fix):
    def async_write(p):
        p.write_async()
        p.flush()

    do_writes_hit_disk(fix, async_write)


def test_writeback_many(fix):
    data_dev = fix.cfg["data_dev"]
    nr_blocks = units.gig(8) // units.kilo(4)

    with bufio_tester(data_dev) as tester:
        with tester.program() as p:
            block = p.alloc_reg()
            buf = p.alloc_reg()

            p.lit(0, block)
            p.checkpoint(0)

            # mark first 8G as dirty
            with loop(p, nr_blocks):
                p.new_buf(block, buf)
                p.mark_dirty(buf)
                p.put_buf(buf)
                p.inc(block)

            # write back
            p.checkpoint(1)
            p.write_sync()
            p.checkpoint(2)


def test_hotspots(fix):
    nr_hotspots = 16

    # size in 4k blocks
    region_size = units.meg(4) // units.kilo(4)
    regions = [(n * region_size, (n + 1) * region_size) for n in range(0, nr_hotspots)]

    big_region_size = units.gig(1) // units.kilo(4)

    with bufio_tester(fix.cfg["data_dev"]) as tester:
        # hotspot programs
        for b, e in regions:
            with tester.program() as p:
                block = p.alloc_reg()
                buf = p.alloc_reg()

                with loop(p, 512):
                    p.lit(b, block)
                    with loop(p, e - b):
                        p.read_buf(block, buf)
                        p.put_buf(buf)
                        p.inc(block)

        # a background writer
        with tester.program() as p:
            block = p.alloc_reg()
            buf = p.alloc_reg()
            p.lit(0, block)
            with loop(p, big_region_size):
                p.read_buf(block, buf)
                p.mark_dirty(buf)
                p.put_buf(buf)
                p.inc(block)


def test_hotspots2(fix):
    nr_hotspots = 16

    # size in 4k blocks
    region_size = units.meg(4) // units.kilo(4)
    regions = [(n * region_size, (n + 1) * region_size) for n in range(0, nr_hotspots)]

    big_region_size = units.gig(1) // units.kilo(4)

    with bufio_tester(fix.cfg["data_dev"]) as tester:
        # hotspot programs
        for b, e in regions:
            with tester.program() as p:
                block = p.alloc_reg()
                buf = p.alloc_reg()

                # warm the cache
                p.lit(b, block)
                with loop(p, e - b):
                    p.new_buf(block, buf)
                    p.put_buf(buf)
                    p.inc(block)

                # benchmark
                p.checkpoint(1)
                with loop(p, 512):
                    p.lit(b, block)
                    with loop(p, e - b):
                        p.read_buf(block, buf)
                        p.put_buf(buf)
                        p.inc(block)
                p.checkpoint(2)


def run_cache(fix, table, nr_blocks):
    with dmdev.dev(table) as data:
        with bufio_tester(data.path) as tester:
            with tester.program() as p:
                block = p.alloc_reg()
                buf = p.alloc_reg()
                p.lit(0, block)
                with loop(p, nr_blocks):
                    p.read_buf(block, buf)
                    p.mark_dirty(buf)
                    p.put_buf(buf)
                    p.inc(block)
                p.write_sync()


def test_multiple_caches(fix):
    def volume_name(index):
        return f"data{index}"

    nr_caches = 4
    volume_size = units.gig(1)
    nr_blocks = volume_size // units.kilo(4)

    vm = tvm.VM()
    vm.add_allocation_volume(fix.cfg["data_dev"])

    for i in range(nr_caches):
        vm.add_volume(tvm.LinearVolume(volume_name(i), volume_size))

    threads = []

    for _ in range(nr_caches):
        tid = threading.Thread(
            target=run_cache, args=(fix, vm.table(volume_name(i)), nr_blocks)
        )
        threads.append(tid)

    for tid in threads:
        tid.start()

    for tid in threads:
        tid.join()


# Checks that buffers that haven't been used for a while get evicted.
def test_evict_old(fix):
    data_dev = fix.cfg["data_dev"]
    nr_blocks = units.gig(1) // units.kilo(4)
    data_size = utils.dev_size(data_dev)
    t = table.Table(targets.BufioTestTarget(data_size, data_dev))

    # we want to keep the dev around once the program has
    # executed, so we have to build the stack by hand
    # rather than use bufio_tester().
    with bufio_params_tracker(max_age=30) as params:
        with dmdev.dev(t) as dev:
            with ThreadSet(dev) as tester:
                with tester.program() as p:
                    block = p.alloc_reg()
                    buf = p.alloc_reg()

                    p.lit(0, block)
                    p.checkpoint(0)

                    # mark first 1G as dirty
                    with loop(p, nr_blocks):
                        p.new_buf(block, buf)
                        p.mark_dirty(buf)
                        p.put_buf(buf)
                        p.inc(block)

                    # write back
                    p.checkpoint(1)
                    p.write_sync()
                    p.checkpoint(2)

            # the cache should automatically shrink as time
            # goes by.
            log.info("beginning to wait")
            alloc1 = params.current_allocated
            time.sleep(60)
            alloc2 = params.current_allocated
            if alloc2 >= alloc1:
                raise ValueError("cache didn't shrink")


