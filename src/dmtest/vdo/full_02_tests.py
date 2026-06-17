"""VDO out-of-space with parallel writes test.

Tests VDO behavior when running out of physical space with parallel writes
to multiple non-overlapping slices, using various dedupe and compression
rates. Tests both direct I/O (Full02) and page-cache I/O (Full04) paths.
"""
import errno
import logging as log
import os
import threading

import dmtest.device_mapper.dev as dmdev
from dmtest.gendatablocks import make_block_range, ClaimError
import dmtest.process as process
import dmtest.tvm as tvm
import dmtest.units as units
from dmtest.vdo.utils import GB
import dmtest.vdo.vdo_stack as vs
import dmtest.vdo.stats as stats
from dmtest.vdo.stats import get_usable_data_blocks


def _sync_device_ignoring_errors(device_path):
    """Sync device data, retrying on errors from a full VDO.

    With page-cache I/O, ENOSPC surfaces during fsync rather than write.
    Retry up to 10 times to clear sticky write-error conditions.
    """
    for retry in range(10):
        try:
            with open(device_path, "r+b") as f:
                os.fsync(f.fileno())
            return
        except OSError as e:
            log.info(f"fsync attempt {retry + 1} got error: {e}")


def run_out_of_space(fix, dedupe_fraction, compress_fraction, direct):
    """Test VDO behavior when running out of physical space.

    Allocates 4 non-overlapping slices where the entire device has only enough
    physical storage to accommodate one slice. Writes data to each slice in
    parallel until space is exhausted, then verifies data integrity.

    When direct=True (Full02), ENOSPC is reported per-block and the stream
    counter is exact. When direct=False (Full04), writes go through the page
    cache and errors surface during sync; verification tolerates zero blocks.
    """
    data_dev = fix.cfg["data_dev"]
    SLICE_COUNT = 4

    physical_size_mb = 3 * 1024
    logical_size_gb = 2

    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("vdo_storage", units.meg(physical_size_mb)))

    with dmdev.dev(vm.table("vdo_storage")) as storage:
        vdo_volume = vs.VDOStack(storage,
                                 logical_size=logical_size_gb * GB,
                                 slab_bits=15)
        with vdo_volume.activate() as vdo:
            initial_stats = stats.vdo_stats(vdo)
            usable_blocks = get_usable_data_blocks(initial_stats)
            block_count = int(usable_blocks / 1000) * 1000
            block_size = initial_stats["blockSize"]

            log.info(f"Physical Blocks: {initial_stats['physicalBlocks']}")
            log.info(f"Overhead Blocks: {initial_stats['overheadBlocksUsed']}")
            log.info(f"Usable Blocks:   {usable_blocks}")
            log.info(f"Block Count per slice: {block_count}")
            log.info(f"direct={direct}")

            slices = []
            for number in range(1, SLICE_COUNT + 1):
                offset = (number - 1) * block_count
                slice_range = make_block_range(path=vdo.path,
                                              block_size=block_size,
                                              block_count=block_count,
                                              offset=offset)
                slices.append((number, slice_range))

            blocks_written = {}
            write_errors = {}

            def write_slice(number, slice_range):
                tag = f"data{number}"
                try:
                    if direct:
                        slice_range.write(tag=tag,
                                        dedupe=dedupe_fraction,
                                        compress=compress_fraction,
                                        direct=True)
                    else:
                        slice_range.write(tag=tag,
                                        dedupe=dedupe_fraction,
                                        compress=compress_fraction,
                                        fsync=False)
                except OSError as e:
                    if direct and e.errno != errno.ENOSPC:
                        write_errors[number] = e
                finally:
                    blocks_written[number] = slice_range.streams[-1].counter

            threads = []
            for number, slice_range in slices:
                thread = threading.Thread(target=write_slice,
                                        args=(number, slice_range))
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join()

            if write_errors:
                raise OSError(f"Unexpected write errors: {write_errors}")

            if not direct:
                _sync_device_ignoring_errors(vdo.path)
                process.run("sh -c 'echo 1 > /proc/sys/vm/drop_caches'")

            verify_errors = []

            def verify_slice(number, slice_range):
                written = blocks_written.get(number, 0)
                if written == 0:
                    return
                try:
                    if direct:
                        verify_range = make_block_range(path=vdo.path,
                                                       block_size=block_size,
                                                       block_count=written,
                                                       offset=slice_range.offset)
                        verify_range.streams = slice_range.streams
                        verify_range.verify()
                    else:
                        slice_range.verify()
                except ClaimError:
                    if direct:
                        verify_errors.append(number)
                except Exception as e:
                    verify_errors.append(number)

            threads = []
            for number, slice_range in slices:
                thread = threading.Thread(target=verify_slice,
                                        args=(number, slice_range))
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join()

            if verify_errors:
                raise AssertionError(f"Verification failed for slices: {verify_errors}")


def _make_test(dedupe, compress, direct):
    def test(fix):
        run_out_of_space(fix, dedupe, compress, direct)
    return test


t_direct_vanilla             = _make_test(0.0,   0.0, True)
t_direct_dedupe              = _make_test(0.5,   0.0, True)
t_direct_compress            = _make_test(0.0,   0.6, True)
t_direct_compress_and_dedupe = _make_test(0.334, 0.6, True)

t_cached_vanilla             = _make_test(0.0,   0.0, False)
t_cached_dedupe              = _make_test(0.5,   0.0, False)
t_cached_compress            = _make_test(0.0,   0.6, False)
t_cached_compress_and_dedupe = _make_test(0.334, 0.6, False)


def register(tests):
    tests.register_batch(
        "/vdo/full/",
        [
            ("direct-vanilla",            t_direct_vanilla),
            ("direct-dedupe",             t_direct_dedupe),
            ("direct-compress",           t_direct_compress),
            ("direct-compress-and-dedupe", t_direct_compress_and_dedupe),
            ("cached-vanilla",            t_cached_vanilla),
            ("cached-dedupe",             t_cached_dedupe),
            ("cached-compress",           t_cached_compress),
            ("cached-compress-and-dedupe", t_cached_compress_and_dedupe),
        ],
    )
