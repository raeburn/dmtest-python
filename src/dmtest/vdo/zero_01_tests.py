import logging as log
import os
import tempfile

import dmtest.process as process
import dmtest.vdo.stats as stats
from dmtest.assertions import assert_equal
from dmtest.fs import Ext4
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo


def drop_caches():
    """Drop filesystem caches."""
    process.run("echo 3 > /proc/sys/vm/drop_caches")


def assert_no_blocks_used(vdo, label):
    """Assert that no physical blocks are used in VDO (zeros are optimized)."""
    vdo_stats = stats.vdo_stats(vdo)
    data_blocks_used = vdo_stats['dataBlocksUsed']
    log.info(f"{label}: dataBlocksUsed={data_blocks_used}")
    assert_equal(data_blocks_used, 0, f"Expected no blocks used at {label}")


def t_dedupe(fix) -> None:
    """Test writing zero blocks to a VDO device.

    Verifies that VDO correctly optimizes zero blocks by not allocating
    physical storage, and that the zeros can be read back correctly.
    """
    block_count = 200000

    with standard_vdo(fix) as vdo:
        # Write zeros directly to the device
        drop_caches()
        assert_no_blocks_used(vdo, "before writing")

        log.info(f"Writing {block_count} zero blocks to {vdo.path}")
        process.run(f"dd if=/dev/zero of={vdo.path} bs={BLOCK_SIZE} count={block_count} oflag=direct")
        process.run("sync")

        assert_no_blocks_used(vdo, "after writing")

        # Read zeros back from the device
        drop_caches()
        log.info(f"Reading {block_count} zero blocks from {vdo.path}")
        process.run(f"dd if={vdo.path} of=/dev/null bs={BLOCK_SIZE} count={block_count} iflag=direct")
        process.run("sync")

        assert_no_blocks_used(vdo, "after reading")

        # Verify that we're actually reading zeros by comparing with /dev/zero
        with tempfile.NamedTemporaryFile() as read_file, \
             tempfile.NamedTemporaryFile() as zero_file:

            log.info("Verifying data by comparing with /dev/zero")
            process.run(f"dd if={vdo.path} of={read_file.name} bs={BLOCK_SIZE} count={block_count}")
            process.run(f"dd if=/dev/zero of={zero_file.name} bs={BLOCK_SIZE} count={block_count}")
            process.run(f"cmp {read_file.name} {zero_file.name}")

        # Test filesystem operations with zeros
        drop_caches()
        fs = Ext4(vdo.path)
        fs.format()

        with tempfile.TemporaryDirectory() as mount_point:
            fs.mount(mount_point)

            zero_file_path = os.path.join(mount_point, "zero")

            log.info(f"Writing {block_count} zero blocks through filesystem")
            process.run(f"dd if=/dev/zero of={zero_file_path} bs={BLOCK_SIZE} count={block_count}")
            process.run("sync")

            drop_caches()
            log.info(f"Reading {block_count} zero blocks through filesystem")
            process.run(f"dd if={zero_file_path} of=/dev/null bs={BLOCK_SIZE} count={block_count}")
            process.run("sync")

            # Verify the file content
            with tempfile.NamedTemporaryFile() as expected_zero:
                log.info("Verifying filesystem data")
                process.run(f"dd if=/dev/zero of={expected_zero.name} bs={BLOCK_SIZE} count={block_count}")
                process.run(f"cmp {zero_file_path} {expected_zero.name}")

            fs.umount()


def t_discard(fix) -> None:
    """Test discarding blocks on a VDO device.

    Verifies that TRIM/discard operations on a VDO device correctly
    result in no physical blocks being used.
    """
    block_count = 200000

    with standard_vdo(fix) as vdo:
        drop_caches()
        assert_no_blocks_used(vdo, "before discard")

        log.info(f"Discarding {block_count} blocks on {vdo.path}")
        # Use blkdiscard to trim the device
        data_size = block_count * BLOCK_SIZE
        process.run(f"blkdiscard -l {data_size} {vdo.path}")
        process.run("sync -d {vdo.path}")

        assert_no_blocks_used(vdo, "after discard")


def register(tests):
    tests.register_batch("/vdo/zero/", [
        ("dedupe", t_dedupe),
        ("discard", t_discard),
    ])
