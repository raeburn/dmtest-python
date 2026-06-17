"""VDO UDS deduplication timeout test.

Tests that VDO reports dedupe advice timeouts when the UDS index
cannot respond quickly enough due to slow underlying storage.
Converted from UDSTimeout01.pm.
"""
import logging as log
import os
import threading
import time

import dmtest.device_mapper.dev as dmdev
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.process as process
import dmtest.vdo.stats as vdo_stats_mod
import dmtest.vdo.vdo_stack as vs
from dmtest.gendatablocks import make_block_range
from dmtest.utils import dev_size
from dmtest.vdo.utils import wait_for_index, BLOCK_SIZE

BLOCK_COUNT = 20000
DATASET_COUNT = 10
READ_DELAY_MS = 6000
DELAY_ACTIVE_SECS = 60


def _swap_delay_table(delay_dev_name, data_size, data_dev, read_delay_ms):
    """Swap the dm-delay table to change read delay without udev blocking."""
    tline = f"0 {data_size} delay {data_dev} 0 {read_delay_ms} {data_dev} 0 0"
    process.run(f"dmsetup suspend --noflush {delay_dev_name}")
    process.run(f"dmsetup load {delay_dev_name} --table '{tline}'")
    process.run(f"dmsetup resume --noudevsync {delay_dev_name}")


def t_uds_timeout(fix) -> None:
    """Test that VDO reports dedupe timeouts with slow storage.

    Writes duplicate data to a VDO stacked on a dm-delay device and
    verifies that dedupe advice timeouts increase when UDS index reads
    are delayed beyond the 5-second default timeout.
    """
    data_dev = fix.cfg["data_dev"]
    data_size = dev_size(data_dev)

    # Phase 1: Format VDO directly and populate the UDS index.
    log.info("Phase 1: populating UDS index on fast storage")
    stack = vs.VDOStack(data_dev)
    with stack.activate() as vdo:
        wait_for_index(vdo)
        vdo_path = str(vdo)

        log.info(f"Writing {DATASET_COUNT} datasets of {BLOCK_COUNT} blocks each")
        for n in range(DATASET_COUNT):
            tag = f"D{n}"
            first_offset = 2 * n * BLOCK_COUNT
            log.info(f"Writing dataset {tag} at offset {first_offset}")
            br = make_block_range(vdo_path, BLOCK_COUNT, BLOCK_SIZE, first_offset)
            br.write(tag=tag, fsync=True)

        before_stats = vdo_stats_mod.vdo_stats(vdo)
        before_timeouts = before_stats['dedupeAdviceTimeouts']
        log.info(f"Dedupe advice timeouts after phase 1: {before_timeouts}")

    # Phase 2: Restart VDO on a dm-delay device with read delay.
    # Create dm-delay initially with 0ms delay to avoid udev probe hang,
    # then swap to the real delay after VDO is running and caches are dropped.
    log.info("Phase 2: restarting VDO on dm-delay device")
    zero_delay_table = table.Table(
        targets.Target("delay", data_size,
                       data_dev, 0, 0, data_dev, 0, 0)
    )
    delay_dev = dmdev.dev(zero_delay_table)
    try:
        stack2 = vs.VDOStack(str(delay_dev), format=False)
        with stack2.activate() as vdo:
            log.info("Waiting for UDS index to come online")
            wait_for_index(vdo)
            vdo_path = str(vdo)

            # Evict UDS index pages from the page cache so subsequent
            # lookups must read through the slow dm-delay device.
            log.info("Dropping page caches")
            with open("/proc/sys/vm/drop_caches", "w") as f:
                f.write("3\n")

            # Enable read delay on the underlying device.
            log.info(f"Enabling {READ_DELAY_MS}ms read delay")
            _swap_delay_table(delay_dev.name, data_size, data_dev,
                              READ_DELAY_MS)

            # Write duplicate copies of all datasets in parallel.
            log.info("Writing second copies of all datasets in parallel")
            errors = []

            def write_second_copy(dataset_num: int) -> None:
                try:
                    tag = f"D{dataset_num}"
                    second_offset = 2 * dataset_num * BLOCK_COUNT + BLOCK_COUNT
                    br = make_block_range(
                        vdo_path, BLOCK_COUNT, BLOCK_SIZE, second_offset
                    )
                    br.write(tag=tag)
                except Exception as e:
                    errors.append(e)

            threads = []
            for n in range(DATASET_COUNT):
                t = threading.Thread(target=write_second_copy, args=(n,))
                threads.append(t)
                t.start()

            # Keep the delay active long enough for VDO to process
            # blocks through the slow path and accumulate timeouts,
            # then disable it so remaining I/O drains quickly.
            log.info(f"Waiting {DELAY_ACTIVE_SECS}s for timeouts to accumulate")
            time.sleep(DELAY_ACTIVE_SECS)

            log.info("Disabling read delay for remaining I/O and cleanup")
            _swap_delay_table(delay_dev.name, data_size, data_dev, 0)

            for t in threads:
                t.join()

            if errors:
                raise errors[0]

            os.sync()

            after_stats = vdo_stats_mod.vdo_stats(vdo)
            after_timeouts = after_stats['dedupeAdviceTimeouts']
            log.info(f"Dedupe advice timeouts after phase 2: {after_timeouts}")

            assert after_timeouts > before_timeouts, (
                f"Expected dedupe advice timeouts to increase, "
                f"but before={before_timeouts}, after={after_timeouts}"
            )
            log.info(
                f"Timeout count increased from {before_timeouts} to {after_timeouts}"
            )
    finally:
        delay_dev.remove()


def register(tests):
    tests.register_batch("/vdo/uds-timeout/", [
        ("timeout", t_uds_timeout),
    ])
