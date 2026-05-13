import dmtest.device_mapper.dev as dmdev
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.process as process
import dmtest.tvm as tvm
import dmtest.units as units
import dmtest.utils as utils
import logging as log
import time

from contextlib import contextmanager


def _delay_table(dev, size, delay_ms, **kw):
    return table.Table(targets.DelayTarget(size, dev, 0, delay_ms, **kw))


@contextmanager
def _delay_dev(data_dev, size, delay_ms, **kw):
    """Create a delay device, avoiding udev stalls.

    Activates with delay=0 so udev's device scan is fast, then reloads
    with the real delay table using --noudevsync to skip the slow udev
    rescan.  On teardown, reloads back to delay=0 so the device can be
    removed without waiting for delayed udev I/Os.
    """
    t_zero = _delay_table(data_dev, size, 0)
    d = dmdev.dev(t_zero)
    try:
        if delay_ms > 0 or kw:
            t_delay = table.Table(
                targets.DelayTarget(size, data_dev, 0, delay_ms, **kw)
            )
            d.suspend()
            d.load(t_delay)
            process.run(f"dmsetup resume --noudevsync {d.name}")
        yield d
    finally:
        try:
            d.suspend()
            d.load(t_zero)
            d.resume()
        except Exception:
            pass
        d.remove()


def _timed_dd_read(dev, count=1):
    start = time.time()
    process.run(f"dd if={dev} of=/dev/null bs=4k count={count} iflag=direct")
    return time.time() - start


def _timed_dd_write(dev, count=1):
    start = time.time()
    process.run(
        f"dd if=/dev/zero of={dev} bs=4k count={count} oflag=direct conv=fsync"
    )
    return time.time() - start


def t_create_and_integrity(fix):
    data_dev = fix.cfg["data_dev"]
    size = units.meg(16)
    t = _delay_table(data_dev, size, 0)

    with dmdev.dev(t) as delay:
        process.run(
            f"dd if=/dev/urandom of={delay.path} bs=4k count=1024 oflag=direct"
        )
        process.run(
            f"dd if={delay.path} of=/tmp/delay_readback bs=4k count=1024 iflag=direct"
        )
        process.run(
            f"dd if={data_dev} of=/tmp/delay_direct bs=4k count=1024 iflag=direct"
        )
        process.run("cmp /tmp/delay_readback /tmp/delay_direct")


def t_read_delay(fix):
    data_dev = fix.cfg["data_dev"]
    size = units.meg(16)
    delay_ms = 200

    with _delay_dev(data_dev, size, delay_ms) as delay:
        elapsed = _timed_dd_read(delay.path)
        log.info(f"read took {elapsed:.3f}s, expected >= {delay_ms / 1000:.3f}s")
        assert elapsed >= delay_ms / 1000, (
            f"read completed in {elapsed:.3f}s, expected >= {delay_ms / 1000:.3f}s"
        )


def t_write_delay(fix):
    data_dev = fix.cfg["data_dev"]
    size = units.meg(16)
    delay_ms = 200

    with _delay_dev(data_dev, size, delay_ms) as delay:
        elapsed = _timed_dd_write(delay.path)
        log.info(f"write took {elapsed:.3f}s, expected >= {delay_ms / 1000:.3f}s")
        assert elapsed >= delay_ms / 1000, (
            f"write completed in {elapsed:.3f}s, expected >= {delay_ms / 1000:.3f}s"
        )


def t_asymmetric_rw(fix):
    data_dev = fix.cfg["data_dev"]
    size = units.meg(16)
    write_delay_ms = 200

    with _delay_dev(
        data_dev, size, 0,
        write_dev=data_dev, write_offset=0, write_delay=write_delay_ms,
    ) as delay:
        # write first so there's data to read
        elapsed_write = _timed_dd_write(delay.path)
        log.info(f"write took {elapsed_write:.3f}s")
        assert elapsed_write >= write_delay_ms / 1000, (
            f"write completed in {elapsed_write:.3f}s, "
            f"expected >= {write_delay_ms / 1000:.3f}s"
        )

        elapsed_read = _timed_dd_read(delay.path)
        log.info(f"read took {elapsed_read:.3f}s (should be fast)")
        assert elapsed_read < write_delay_ms / 1000, (
            f"read took {elapsed_read:.3f}s, "
            f"expected < {write_delay_ms / 1000:.3f}s"
        )


def t_flush_delay(fix):
    data_dev = fix.cfg["data_dev"]
    size = units.meg(16)
    flush_delay_ms = 200

    with _delay_dev(
        data_dev, size, 0,
        write_dev=data_dev, write_offset=0, write_delay=0,
        flush_dev=data_dev, flush_offset=0, flush_delay=flush_delay_ms,
    ) as delay:
        elapsed = _timed_dd_write(delay.path)
        log.info(
            f"write+flush took {elapsed:.3f}s, "
            f"expected >= {flush_delay_ms / 1000:.3f}s"
        )
        assert elapsed >= flush_delay_ms / 1000, (
            f"write+flush completed in {elapsed:.3f}s, "
            f"expected >= {flush_delay_ms / 1000:.3f}s"
        )


def t_table_reload(fix):
    data_dev = fix.cfg["data_dev"]
    size = units.meg(16)

    with _delay_dev(data_dev, size, 200) as delay:
        elapsed_slow = _timed_dd_read(delay.path)
        log.info(f"slow read: {elapsed_slow:.3f}s")
        assert elapsed_slow >= 0.2, f"expected >= 0.2s, got {elapsed_slow:.3f}s"

        t_fast = _delay_table(data_dev, size, 0)
        with delay.pause():
            delay.load(t_fast)

        elapsed_fast = _timed_dd_read(delay.path)
        log.info(f"fast read: {elapsed_fast:.3f}s")
        assert elapsed_fast < 0.1, f"expected < 0.1s, got {elapsed_fast:.3f}s"


def t_separate_devices(fix):
    data_dev = fix.cfg["data_dev"]
    vol_size = units.meg(16)

    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("read_vol", vol_size))
    vm.add_volume(tvm.LinearVolume("write_vol", vol_size))

    with dmdev.dev(vm.table("read_vol")) as read_dev:
        with dmdev.dev(vm.table("write_vol")) as write_dev:
            process.run(
                f"dd if=/dev/zero of={read_dev.path} bs=1M count=8 oflag=direct"
            )
            process.run(
                f"dd if=/dev/zero of={write_dev.path} bs=1M count=8 oflag=direct"
            )

            # seed the read volume with a known pattern
            process.run(
                f"dd if=/dev/urandom of={read_dev.path} bs=4k count=256 oflag=direct"
            )

            t = table.Table(
                targets.DelayTarget(
                    vol_size, read_dev.path, 0, 0,
                    write_dev=write_dev.path, write_offset=0, write_delay=0,
                )
            )
            with dmdev.dev(t) as delay:
                # read through delay — should come from read_vol
                process.run(
                    f"dd if={delay.path} of=/tmp/delay_sep_read "
                    f"bs=4k count=256 iflag=direct"
                )
                process.run(
                    f"dd if={read_dev.path} of=/tmp/delay_sep_direct "
                    f"bs=4k count=256 iflag=direct"
                )
                process.run("cmp /tmp/delay_sep_read /tmp/delay_sep_direct")

                # write through delay — should go to write_vol
                process.run(
                    f"dd if=/dev/urandom of=/tmp/delay_sep_pattern bs=4k count=256"
                )
                process.run(
                    f"dd if=/tmp/delay_sep_pattern of={delay.path} "
                    f"bs=4k count=256 oflag=direct conv=fsync"
                )
                process.run(
                    f"dd if={write_dev.path} of=/tmp/delay_sep_wcheck "
                    f"bs=4k count=256 iflag=direct"
                )
                process.run("cmp /tmp/delay_sep_pattern /tmp/delay_sep_wcheck")


def t_concurrent_io(fix):
    data_dev = fix.cfg["data_dev"]
    size = units.meg(16)
    delay_ms = 200
    num_jobs = 4
    io_depth = 4
    total_ios = num_jobs * io_depth

    with _delay_dev(data_dev, size, delay_ms) as delay:
        start = time.time()
        process.run(
            f"fio --name=test --filename={delay.path} --rw=randread "
            f"--bs=4k --direct=1 --numjobs={num_jobs} --iodepth={io_depth} "
            f"--io_size=4k --group_reporting"
        )
        elapsed = time.time() - start

        serial_time = total_ios * delay_ms / 1000
        log.info(
            f"concurrent IO took {elapsed:.3f}s, "
            f"serial would be {serial_time:.3f}s"
        )
        assert elapsed < serial_time / 2, (
            f"IOs appear serialized: {elapsed:.3f}s vs serial {serial_time:.3f}s"
        )


def t_large_delay(fix):
    data_dev = fix.cfg["data_dev"]
    size = units.meg(16)
    delay_ms = 5000

    with _delay_dev(data_dev, size, delay_ms) as delay:
        elapsed = _timed_dd_read(delay.path)
        log.info(
            f"large-delay read took {elapsed:.3f}s, "
            f"expected >= {delay_ms / 1000:.3f}s"
        )
        assert elapsed >= delay_ms / 1000, (
            f"read completed in {elapsed:.3f}s, "
            f"expected >= {delay_ms / 1000:.3f}s"
        )


def register(tests):
    tests.register_batch(
        "/delay/",
        [
            ("create-and-integrity", t_create_and_integrity),
            ("read-delay", t_read_delay),
            ("write-delay", t_write_delay),
            ("asymmetric-rw", t_asymmetric_rw),
            ("flush-delay", t_flush_delay),
            ("table-reload", t_table_reload),
            ("separate-devices", t_separate_devices),
            ("concurrent-io", t_concurrent_io),
            ("large-delay", t_large_delay),
        ],
    )
