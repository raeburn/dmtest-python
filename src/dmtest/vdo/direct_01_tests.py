"""
VDO Direct01 test - Basic block-level deduplication and persistence
"""
import logging as log

from dmtest.assertions import assert_equal
from dmtest.gendatablocks import make_block_range
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo, settle_devices
import dmtest.process as process
import dmtest.vdo.stats as stats


def t_direct_01(fix) -> None:
    """
    Test VDO's fundamental block-level deduplication by writing data directly
    to the VDO device, verifying deduplication occurs correctly, and ensuring
    data persists across device restarts.
    """
    block_count = 5000

    with standard_vdo(fix, slab_bits=17) as vdo:
        # Wait for udev to settle
        settle_devices()

        # Check initial statistics are zero
        log.info("Verifying initial VDO statistics are zero")
        initial_stats = stats.vdo_stats(vdo)
        assert_equal(initial_stats['dataBlocksUsed'], 0, "initial data blocks used")
        assert_equal(initial_stats['hashLock']['dedupeAdviceValid'], 0, "initial dedupe advice valid")
        assert_equal(initial_stats['hashLock']['dedupeAdviceStale'], 0, "initial dedupe advice stale")
        assert_equal(initial_stats['dedupeAdviceTimeouts'], 0, "initial dedupe advice timeouts")
        assert_equal(initial_stats['index']['entriesIndexed'], 0, "initial entries indexed")

        # Write first slice: 5000 blocks at offset 0 with tag "Direct1"
        log.info(f"Writing first slice: {block_count} blocks at offset 0")
        slice1 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=0)
        slice1.write(tag="Direct1", dedupe=0, compress=0, fsync=True)

        # Verify first slice
        log.info("Verifying first slice")
        slice1.verify()

        # Check statistics after first write
        log.info("Checking statistics after first write")
        after_first = stats.vdo_stats(vdo)
        assert_equal(after_first['dataBlocksUsed'], block_count,
                    "data blocks used after first write")
        assert_equal(after_first['index']['entriesIndexed'], block_count,
                    "entries indexed after first write")

        # Write second slice: same 5000 blocks at offset 5000 with same tag
        # This should be fully deduplicated
        log.info(f"Writing second slice: {block_count} blocks at offset {block_count}")
        slice2 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=block_count)
        slice2.write(tag="Direct1", dedupe=0, compress=0, fsync=True)

        # Verify second slice
        log.info("Verifying second slice")
        slice2.verify()

        # Check statistics after second write - should show deduplication
        log.info("Checking statistics after second write")
        after_second = stats.vdo_stats(vdo)
        assert_equal(after_second['hashLock']['dedupeAdviceValid'], block_count,
                    "dedupe advice valid after second write")
        assert_equal(after_second['dataBlocksUsed'], block_count,
                    "data blocks used after second write (no new blocks due to dedupe)")
        assert_equal(after_second['index']['entriesIndexed'], block_count,
                    "entries indexed after second write (no new unique blocks)")

    # VDO device is now stopped (exited context manager)
    log.info("Restarting VDO device to verify data persistence")

    # Restart VDO device without reformatting
    with standard_vdo(fix, format=False, slab_bits=17) as vdo:
        settle_devices()

        # Update paths for the block ranges
        slice1.update_path(vdo.path)
        slice2.update_path(vdo.path)

        # Verify both slices persist across restart
        log.info("Verifying first slice after restart")
        slice1.verify()

        log.info("Verifying second slice after restart")
        slice2.verify()

        log.info("Direct01 test completed successfully")


def register(tests):
    tests.register("/vdo/direct/direct-01", t_direct_01)
