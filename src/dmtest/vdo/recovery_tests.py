"""
Recovery tests for dm-vdo.

These tests trigger the recovery path by replacing the storage device
under dm-vdo with a dm-error target mid-operation, then verifying that
dm-vdo can recover to a consistent state.
"""

from dmtest.assertions import assert_equal, assert_matches, assert_raises
from dmtest.device_mapper import dev as dmdev
from dmtest.device_mapper import table, targets
from dmtest.units import SECTOR_SIZE
from dmtest.vdo.utils import wait_for_index, fsync
from dmtest.vdo.utils import BLOCK_SIZE
from dmtest.vdo.vdo_stack import VDOStack
import dmtest.vdo.stats as stats
import dmtest.vdo.status as status
from dmtest.gendatablocks import make_block_range
import dmtest.utils as utils
import logging as log
import re
import threading
import time


def write_recovery_data(vdo, tag, block_size=BLOCK_SIZE):
    """Write some data to be interrupted by device loss."""
    log.info("Writing data for 5 seconds (should be interrupted)")

    # This write should fail due to error target introduction
    def write_data():
        run_fio(vdo, offset=30000, comression=55, duration=5, size=0)

    assert_raises(write_data)


def fail_vdo_storage(vdo, linear, block_size=BLOCK_SIZE):
    """
    Force a recovery by replacing the underlying storage with an error target.
    """
    # Get stats before failure
    stats_before = stats.vdo_stats(vdo)
    log.info(f"Stats before failure - data blocks: {stats_before['dataBlocksUsed']}, "
             f"logical blocks: {stats_before['logicalBlocksUsed']}")

    recovery_start_time = time.time()
    async_write = threading.Thread(target=write_recovery_data,
                                   args=(vdo, "fail", block_size,))
    async_write.start()
    linear_size = utils.dev_size(linear)
    time.sleep(0.5)

    log.info("Replacing storage with error target")
    with linear.pause():
        error_table = table.Table(targets.ErrorTarget(linear_size))
        linear.load(error_table)

    async_write.join()

    # Flush pending writes to trigger an error
    try:
        fsync(vdo)
    except:
        pass

    return recovery_start_time


def verify_vdo_recovery(vdo, start_time: float):
    """Verify that VDO recovered successfully to a consistent state."""
    # Wait for index to come online
    wait_for_index(vdo)

    # Check the logs to make sure recovery really happened
    # If one of these assertions fails, the rebuild likely didn't happen
    message = utils.get_dmesg_log(start_time)
    assert_matches(message, r'[Rr]ebuilding reference counts')
    assert_matches(message, r'[Rr]ebuild complete')

    match = re.search(r'[Rr]eplaying (\d+) recovery entries into block map', message)
    if match:
        recovery_entries = match.group(0)
        log.info(f"Replayed {recovery_entries} recovery entries")

    # vdo Status should be normal
    vdo_status = status.vdo_status(vdo)
    log.info(f"VDO status after recovery: {vdo_status}")

    assert_equal(vdo_status["mode"], "normal",
                 "VDO should be in normal mode after recovery")
    assert_equal(vdo_status["index-state"], "online",
                 "VDO index should be online after recovery")

    # Get stats to verify no catastrophic errors
    vdo_stats = stats.vdo_stats(vdo)
    log.info(f"VDO stats after recovery - data blocks: {vdo_stats['dataBlocksUsed']}, "
             f"logical blocks: {vdo_stats['logicalBlocksUsed']}")


def t_single_recovery(fix):
    """
    Test VDO recovery after a failure during write operations.
    """
    data_dev = fix.cfg("data_dev")
    data_size = utils.dev_size(data_dev)

    # Create an intermediate dm-linear device so we can control the storage
    log.info(f"Creating dm-linear device on {data_dev} ({data_size} sectors)")
    linear_table = table.Table(targets.LinearTarget(data_size, data_dev, 0))

    with dmdev.dev(linear_table) as linear:
        log.info(f"Created linear device: {linear.path}")

        # Create and format vdo on the linear device
        with VDOStack(linear, format=True).activate() as vdo:
            log.info(f"Activated vdo device: {vdo.path}")
            wait_for_index(vdo)

            initial_blocks = make_block_range(
                path=vdo.path,
                block_size=BLOCK_SIZE,
                block_count=2500
            )
            initial_blocks.write(tag="initial", dedupe=0.1, compress=0.55, fsync=True)
            initial_blocks.verify()
            log.info("Initial data written and verified")

            # Get stats before failure simulation
            stats_before = stats.vdo_stats(vdo)
            log.info(f"vdostats before - data blocks: {stats_before['dataBlocksUsed']}, "
                     f"logical blocks: {stats_before['logicalBlocksUsed']}")

            start_time = fail_vdo_storage(vdo, linear)

        # Restore the linear device to point back to real storage
        log.info("Restoring linear device to real storage")
        with linear.pause():
            linear.load(linear_table)

        # Force a recovery by recreating the vdo target without formatting
        with VDOStack(linear, format=False).activate() as vdo:
            log.info(f"vdo recovery complete: {vdo.path}")
            verify_vdo_recovery(vdo, start_time)

            log.info("Verifying initial data")
            initial_blocks.update_path(vdo.path)
            initial_blocks.verify()

            # Write new data to prove VDO is functional
            log.info("Writing new data post-recovery to verify functionality")
            post_blocks = make_block_range(
                path=vdo.path,
                block_size=BLOCK_SIZE,
                block_count=1000,
                offset=30000
            )
            post_blocks.write(tag="post", dedupe=0.1, compress=0.55, fsync=True)
            post_blocks.verify()

            stats_after = stats.vdo_stats(vdo)
            log.info(f"vdostats after - data blocks: {stats_after['dataBlocksUsed']}, "
                     f"logical blocks: {stats_after['logicalBlocksUsed']}")


def t_double_recovery(fix):
    """
    Test two consecutive recoveries.
    """
    data_dev = fix.cfg("data_dev")
    data_size = utils.dev_size(data_dev)

    # Create an intermediate dm-linear device so we can control the storage
    log.info(f"Creating dm-linear device on {data_dev} ({data_size} sectors)")
    linear_table = table.Table(targets.LinearTarget(data_size, data_dev, 0))

    with dmdev.dev(linear_table) as linear:
        log.info(f"Created linear device: {linear.path}")

        # Create and format vdo on the linear device
        with VDOStack(linear, format=True).activate() as vdo:
            log.info(f"Activated vdo device: {vdo.path}")
            wait_for_index(vdo)

            initial_blocks = make_block_range(
                path=vdo.path,
                block_size=BLOCK_SIZE,
                block_count=5000
            )
            initial_blocks.write(tag="initial", dedupe=0.1, compress=0.55, fsync=True)
            initial_blocks.verify()
            log.info("Initial data written and verified")

            # Get stats before failure simulation
            stats_before = stats.vdo_stats(vdo)
            log.info(f"vdostats before - data blocks: {stats_before['dataBlocksUsed']}, "
                     f"logical blocks: {stats_before['logicalBlocksUsed']}")

            start_time = fail_vdo_storage(vdo, linear)

        # Restore the linear device to point back to real storage
        log.info("Restoring linear device to real storage")
        with linear.pause():
            linear.load(linear_table)

        # Force a recovery by recreating the vdo target without formatting
        with VDOStack(linear, format=False).activate() as vdo:
            log.info(f"vdo recovery complete: {vdo.path}")
            verify_vdo_recovery(vdo, start_time)

            log.info("Verifying initial data")
            initial_blocks.update_path(vdo.path)
            initial_blocks.verify()

            # Write new data to prove VDO is functional
            log.info("Writing post-recovery data")
            mid_blocks = make_block_range(
                path=vdo.path,
                block_size=BLOCK_SIZE,
                block_count=2000,
                offset=5000
            )
            mid_blocks.write(tag="mid", dedupe=0.1, compress=0.55, fsync=True)
            mid_blocks.verify()

            stats_between = stats.vdo_stats(vdo)
            log.info(f"vdostats between - data blocks: {stats_between['dataBlocksUsed']}, "
                     f"logical blocks: {stats_between['logicalBlocksUsed']}")

            # Fail the VDO again
            start_time = fail_vdo_storage(vdo, linear)

        log.info("Restoring linear device to real storage again")
        with linear.pause():
            linear.load(linear_table)

        # Force another recovery
        with VDOStack(linear, format=False).activate() as vdo:
            log.info(f"second vdo recovery complete: {vdo.path}")
            verify_vdo_recovery(vdo, start_time)

            log.info("Verifying existing data")
            initial_blocks.update_path(vdo.path)
            initial_blocks.verify()
            mid_blocks.update_path(vdo.path)
            mid_blocks.verify()

            log.info("Writing more new data post-recovery")
            post_blocks = make_block_range(
                path=vdo.path,
                block_size=BLOCK_SIZE,
                block_count=2000,
                offset=7000
            )
            post_blocks.write(tag="post", dedupe=0.1, compress=0.55, fsync=True)
            post_blocks.verify()

            stats_after = stats.vdo_stats(vdo)
            log.info(f"vdostats after - data blocks: {stats_after['dataBlocksUsed']}, "
                     f"logical blocks: {stats_after['logicalBlocksUsed']}")


def t_512_recovery(fix):
    """
    Test VDO recovery after a failure during write operations.
    """
    data_dev = fix.cfg("data_dev")
    data_size = utils.dev_size(data_dev)

    # Create an intermediate dm-linear device so we can control the storage
    log.info(f"Creating dm-linear device on {data_dev} ({data_size} sectors)")
    linear_table = table.Table(targets.LinearTarget(data_size, data_dev, 0))

    with dmdev.dev(linear_table) as linear:
        log.info(f"Created linear device: {linear.path}")

        # Create and format vdo with a small block size
        with VDOStack(linear, block_size=512, format=True).activate() as vdo:
            log.info(f"Activated vdo device: {vdo.path}")
            wait_for_index(vdo)

            initial_blocks = make_block_range(
                path=vdo.path,
                block_size=SECTOR_SIZE,
                block_count=20000
            )
            initial_blocks.write(tag="initial", dedupe=0.1, compress=0.55, fsync=True)
            initial_blocks.verify()
            log.info("Initial data written and verified")

            # Get stats before failure simulation
            stats_before = stats.vdo_stats(vdo)
            log.info(f"vdostats before - data blocks: {stats_before['dataBlocksUsed']}, "
                     f"logical blocks: {stats_before['logicalBlocksUsed']}")

            start_time = fail_vdo_storage(vdo, linear, block_size=SECTOR_SIZE)

        # Restore the linear device to point back to real storage
        log.info("Restoring linear device to real storage")
        with linear.pause():
            linear.load(linear_table)

        # Force a recovery by recreating the vdo target without formatting
        with VDOStack(linear, format=False).activate() as vdo:
            log.info(f"vdo recovery complete: {vdo.path}")
            verify_vdo_recovery(vdo, start_time)

            log.info("Verifying initial data")
            initial_blocks.update_path(vdo.path)
            initial_blocks.verify()

            # Write new data to prove VDO is functional
            log.info("Writing new data post-recovery to verify functionality")
            post_blocks = make_block_range(
                path=vdo.path,
                block_size=SECTOR_SIZE,
                block_count=5000,
                offset=20000
            )
            post_blocks.write(tag="post", dedupe=0.1, compress=0.55, fsync=True)
            post_blocks.verify()

            stats_after = stats.vdo_stats(vdo)
            log.info(f"vdostats after - data blocks: {stats_after['dataBlocksUsed']}, "
                     f"logical blocks: {stats_after['logicalBlocksUsed']}")


def register(tests):
    """Register recovery tests."""
    tests.register_batch(
        "/vdo/recovery/",
        [
            ("quick_recovery", t_single_recovery),
            ("double_recovery", t_double_recovery),
            ("512_recovery", t_512_recovery),
        ],
    )
