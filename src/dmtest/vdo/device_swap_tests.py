"""VDO backing device swap test.

Tests VDO's ability to switch its backing storage device while suspended,
copying data from the old device to the new one, and continuing normal
operations including deduplication after the swap.
"""
import logging as log
import tempfile

from dmtest.assertions import assert_equal
from dmtest.device_mapper.dev import dev
from dmtest.device_mapper.table import Table
from dmtest.device_mapper.targets import LinearTarget, VDOTarget
from dmtest.gendatablocks import make_block_range
from dmtest.process import run
from dmtest.utils import dev_size
from dmtest.vdo.stats import vdo_stats
from dmtest.vdo.utils import BLOCK_SIZE, GB, MB, wait_for_index


def t_device_switch(fix) -> None:
    """Test changing VDO backing store while suspended.

    Verifies that VDO can switch to a new backing device and continue
    normal operations including deduplication.
    """
    log.info("Starting device switch test")

    data_dev = fix.cfg["data_dev"]
    block_count = 1000
    linear_size = 3 * GB
    linear_sectors = linear_size // 512

    # Create two linear devices from the base storage
    # We'll use the first half for linearOne and second half for linearTwo
    log.info("Creating linear devices")
    linear_one_table = Table(LinearTarget(linear_sectors, data_dev, 0))
    linear_two_table = Table(LinearTarget(linear_sectors, data_dev, linear_sectors))

    with dev(linear_one_table) as linear_one, dev(linear_two_table) as linear_two:
        log.info(f"Linear device one: {linear_one.path}")
        log.info(f"Linear device two: {linear_two.path}")

        # Create and format VDO on linear_one
        log.info("Formatting VDO on first linear device")
        physical_size = dev_size(linear_one.path) * 512
        logical_size = 100 * MB
        run(f"vdoformat --force --logical-size={logical_size}B --uds-memory-size=0.25 --slab-bits=15 {linear_one.path}")

        # Create VDO device on linear_one
        log.info("Creating VDO device on first linear device")
        vdo_table = Table(
            VDOTarget(
                logical_size // 512,
                linear_one.path,
                physical_size // 4096,
                4096,
                128 * 1024 * 1024 // 4096,
                16380,
                {}
            )
        )

        with dev(vdo_table) as vdo:
            log.info(f"VDO device: {vdo.path}")
            wait_for_index(vdo)

            # Step 1: Write initial data
            log.info("Writing initial data")
            br_initial = make_block_range(vdo.path, block_count, BLOCK_SIZE, 0)
            br_initial.write("initial", direct=True, sync=True, fsync=True)
            br_initial.verify()
            log.info("Initial data verified")

            # Step 2-3: Prepare new table and suspend VDO
            log.info("Preparing to swap backing device")
            new_vdo_table = Table(
                VDOTarget(
                    logical_size // 512,
                    linear_two.path,  # Swap to second linear device
                    physical_size // 4096,
                    4096,
                    128 * 1024 * 1024 // 4096,
                    16380,
                    {}
                )
            )

            log.info("Loading new VDO table and suspending")
            vdo.load(new_vdo_table)
            vdo.suspend()

            # Step 4: Copy data from linear_one to linear_two
            log.info("Copying data from first to second linear device")
            run(f"dd if={linear_one.path} of={linear_two.path} bs=2M iflag=direct oflag=direct conv=fsync status=noxfer")

            # Step 5: Suspend linear_one to prove it's not accessed
            log.info("Suspending first linear device to prove it's not accessed")
            linear_one.suspend()

            # Step 6: Resume VDO with new table
            log.info("Resuming VDO with new backing device")
            vdo.resume()

            # Step 7: Verify original data is still readable
            log.info("Verifying original data after device swap")
            br_initial.verify()
            log.info("Original data verified after swap")

            # Step 8: Write new data at offset 1000
            log.info("Writing second dataset")
            br_second = make_block_range(vdo.path, block_count, BLOCK_SIZE, block_count)
            br_second.write("second", direct=True, sync=True, fsync=True)
            br_second.verify()
            log.info("Second data verified")

            # Step 9: Write duplicate of original data at offset 2000
            log.info("Writing duplicate of initial data (should deduplicate)")
            br_dup_initial = make_block_range(vdo.path, block_count, BLOCK_SIZE, 2 * block_count)
            br_dup_initial.write("initial", direct=True, sync=True, fsync=True)
            br_dup_initial.verify()
            log.info("Duplicate initial data verified")

            # Step 10: Check statistics for deduplication
            log.info("Checking VDO statistics for deduplication")
            stats_output = run(f"dmsetup status {vdo.name}", raise_on_fail=True)[1]
            log.info(f"VDO status: {stats_output}")

            stats = vdo_stats(vdo)

            data_blocks_used = stats['dataBlocksUsed']
            dedupe_valid = stats['hashLock']['dedupeAdviceValid'] + stats['hashLock']['concurrentDataMatches']

            log.info(f"Data blocks used: {data_blocks_used}, expected: {2 * block_count}")
            log.info(f"Dedupe advice valid: {dedupe_valid}, expected: {block_count}")

            assert_equal(data_blocks_used, 2 * block_count)
            assert_equal(dedupe_valid, block_count)

            # Step 11: Write duplicate of second data at offset 3000
            log.info("Writing duplicate of second data (should deduplicate)")
            br_dup_second = make_block_range(vdo.path, block_count, BLOCK_SIZE, 3 * block_count)
            br_dup_second.write("second", direct=True, sync=True, fsync=True)
            br_dup_second.verify()
            log.info("Duplicate second data verified")

            # Step 12: Check statistics again
            log.info("Checking VDO statistics after second deduplication")
            stats = vdo_stats(vdo)

            data_blocks_used = stats['dataBlocksUsed']
            dedupe_valid = stats['hashLock']['dedupeAdviceValid'] + stats['hashLock']['concurrentDataMatches']

            log.info(f"Data blocks used: {data_blocks_used}, expected: {2 * block_count}")
            log.info(f"Dedupe advice valid: {dedupe_valid}, expected: {2 * block_count}")

            assert_equal(data_blocks_used, 2 * block_count)
            assert_equal(dedupe_valid, 2 * block_count)

            log.info("Device swap test completed successfully")


def register(tests):
    tests.register("/vdo/device-swap/device-switch", t_device_switch)
