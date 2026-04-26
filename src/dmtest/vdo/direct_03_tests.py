"""
VDO Direct03 test - Block discard operations
"""
import logging as log
import time

from dmtest.assertions import assert_equal
from dmtest.gendatablocks import make_block_range
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo, settle_devices
import dmtest.process as process
import dmtest.vdo.stats as stats


def _wait_for_vdo_idle(vdo, timeout_seconds=30):
    """
    Wait for VDO to finish all in-progress I/Os.

    Args:
        vdo: VDO device object
        timeout_seconds: Maximum time to wait
    """
    start_time = time.time()
    while True:
        current_stats = stats.vdo_stats(vdo)
        vios_in_progress = current_stats.get('currentVIOsInProgress', 0)

        if vios_in_progress == 0:
            log.debug("VDO is idle (no VIOs in progress)")
            return

        if time.time() - start_time > timeout_seconds:
            log.warning(f"Timeout waiting for VDO to become idle, {vios_in_progress} VIOs still in progress")
            return

        log.debug(f"Waiting for VDO to become idle ({vios_in_progress} VIOs in progress)...")
        time.sleep(0.1)


def t_discard_blocks(fix) -> None:
    """
    Test basic block discard: write blocks, discard them, verify they become
    zeros, and check that data blocks used returns to zero.
    """
    block_count = 1024

    with standard_vdo(fix) as vdo:
        settle_devices()

        # Verify initial state
        initial_stats = stats.vdo_stats(vdo)
        assert_equal(initial_stats['dataBlocksUsed'], 0,
                    "initial data blocks used should be zero")

        # Write data
        log.info(f"Writing {block_count} blocks with tag 'discard'")
        dataset = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=0)
        dataset.write(tag="discard", dedupe=0, compress=0, fsync=True)

        # Drop caches and verify
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")
        dataset.verify()

        # Check data blocks used
        after_write = stats.vdo_stats(vdo)
        assert_equal(after_write['dataBlocksUsed'], block_count,
                    f"data blocks used should be {block_count}")

        # Trim the data
        log.info("Trimming data")
        before_discard_stats = stats.vdo_stats(vdo)
        dataset.trim(fsync=False)
        _wait_for_vdo_idle(vdo)

        # Drop caches and verify zeros
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")
        dataset.verify()

        # Check data blocks used should be zero again
        after_trim = stats.vdo_stats(vdo)
        assert_equal(after_trim['dataBlocksUsed'], 0,
                    "data blocks used should be zero after trim")

        # Check that discard bios were processed
        discard_bios = after_trim['biosIn']['discard'] - before_discard_stats['biosIn']['discard']
        assert discard_bios > 0, f"should have processed discard bios, got {discard_bios}"
        log.info(f"Processed {discard_bios} discard bios")

        log.info("Discard blocks test completed successfully")


def t_discard_duplicated_blocks(fix) -> None:
    """
    Test discarding deduplicated blocks: write duplicated data twice, discard
    first copy (blocks should remain), discard second copy (blocks should be freed).
    """
    block_count = 1024

    with standard_vdo(fix) as vdo:
        settle_devices()

        # Verify initial state
        initial_stats = stats.vdo_stats(vdo)
        assert_equal(initial_stats['dataBlocksUsed'], 0,
                    "initial data blocks used should be zero")

        # Write data twice with same tag (will deduplicate)
        log.info(f"Writing {block_count} blocks twice with tag 'dupe'")
        dataset1 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                    block_count=block_count, offset=0)
        dataset2 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                    block_count=block_count, offset=block_count)

        dataset1.write(tag="dupe", dedupe=0, compress=0, fsync=True)
        dataset2.write(tag="dupe", dedupe=0, compress=0, fsync=True)

        # Drop caches and verify
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")
        dataset1.verify()
        dataset2.verify()

        # Check deduplication worked
        after_write = stats.vdo_stats(vdo)
        assert_equal(after_write['dataBlocksUsed'], block_count,
                    f"data blocks used should be {block_count} (deduplicated)")

        # Trim first copy
        log.info("Trimming first copy")
        before_first_trim = stats.vdo_stats(vdo)
        dataset1.trim(fsync=False)
        _wait_for_vdo_idle(vdo)

        # Drop caches and verify
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")
        dataset1.verify()

        # Check data blocks used should remain (second copy still references)
        after_first_trim = stats.vdo_stats(vdo)
        assert_equal(after_first_trim['dataBlocksUsed'], block_count,
                    f"data blocks used should still be {block_count}")

        # Check that discard bios were processed
        discard_bios_1 = after_first_trim['biosIn']['discard'] - before_first_trim['biosIn']['discard']
        assert discard_bios_1 > 0, f"should have processed discard bios, got {discard_bios_1}"
        log.info(f"First trim processed {discard_bios_1} discard bios")

        # Trim second copy
        log.info("Trimming second copy")
        dataset2.trim(fsync=False)
        _wait_for_vdo_idle(vdo)

        # Drop caches and verify both read zeros
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")
        dataset1.verify()
        dataset2.verify()

        # Check data blocks used should now be zero
        after_second_trim = stats.vdo_stats(vdo)
        assert_equal(after_second_trim['dataBlocksUsed'], 0,
                    "data blocks used should be zero after both trims")

        # Check that total discard bios increased
        total_discard_bios = after_second_trim['biosIn']['discard'] - initial_stats['biosIn']['discard']
        assert total_discard_bios > discard_bios_1, \
            f"total discard bios should be greater than first trim: {total_discard_bios} > {discard_bios_1}"
        log.info(f"Total discard bios processed: {total_discard_bios}")

        log.info("Discard duplicated blocks test completed successfully")


def t_discard_with_holes(fix) -> None:
    """
    Test discarding with holes: write 3*N blocks, discard the middle N blocks,
    verify the first and third datasets remain intact.
    """
    block_count = 1024

    with standard_vdo(fix) as vdo:
        settle_devices()

        # Verify initial state
        initial_stats = stats.vdo_stats(vdo)
        assert_equal(initial_stats['dataBlocksUsed'], 0,
                    "initial data blocks used should be zero")

        # Write three datasets
        log.info(f"Writing three datasets of {block_count} blocks each")
        dataset1 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                    block_count=block_count, offset=0)
        dataset2 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                    block_count=block_count, offset=block_count)
        dataset3 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                    block_count=block_count, offset=2 * block_count)

        dataset1.write(tag="notrim1", dedupe=0, compress=0, fsync=True)
        dataset2.write(tag="trim", dedupe=0, compress=0, fsync=True)
        dataset3.write(tag="notrim2", dedupe=0, compress=0, fsync=True)

        # Drop caches
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")

        # Check data blocks used
        after_write = stats.vdo_stats(vdo)
        assert_equal(after_write['dataBlocksUsed'], 3 * block_count,
                    f"data blocks used should be {3 * block_count}")

        # Trim the middle dataset
        log.info("Trimming middle dataset")
        before_trim = stats.vdo_stats(vdo)
        dataset2.trim(fsync=False)
        _wait_for_vdo_idle(vdo)

        # Check data blocks used
        after_trim = stats.vdo_stats(vdo)
        assert_equal(after_trim['dataBlocksUsed'], 2 * block_count,
                    f"data blocks used should be {2 * block_count}")

        # Check that discard bios were processed
        discard_bios = after_trim['biosIn']['discard'] - before_trim['biosIn']['discard']
        assert discard_bios > 0, f"should have processed discard bios, got {discard_bios}"
        log.info(f"Processed {discard_bios} discard bios")

        # Verify the trimmed and untrimmed data
        log.info("Verifying all three datasets")
        dataset1.verify()  # Should have original data
        dataset2.verify()  # Should be zeros
        dataset3.verify()  # Should have original data

        log.info("Discard with holes test completed successfully")


def register(tests):
    tests.register_batch("/vdo/direct/", [
        ("direct-03-discard-blocks", t_discard_blocks),
        ("direct-03-discard-duplicated-blocks", t_discard_duplicated_blocks),
        ("direct-03-discard-with-holes", t_discard_with_holes),
    ])
