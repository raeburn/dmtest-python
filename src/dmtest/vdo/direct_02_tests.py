"""
VDO Direct02 test - Overwrite with identical data (self-deduplication)
"""
import logging as log

from dmtest.assertions import assert_equal
from dmtest.gendatablocks import make_block_range
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo
import dmtest.process as process
import dmtest.vdo.stats as stats


def t_direct_02(fix) -> None:
    """
    Test VDO's handling of identical data overwrite scenarios. Writes blocks
    to the VDO device, then overwrites the same logical addresses with
    identical data content. Verifies that VDO correctly deduplicates the
    overwrite by recognizing that the new data matches the existing data.
    """
    block_count = 1000

    with standard_vdo(fix, slab_bits=17) as vdo:
        # Wait for udev to settle
        process.run("udevadm settle")

        # Check initial statistics are zero
        log.info("Verifying initial VDO statistics are zero")
        initial_stats = stats.vdo_stats(vdo)
        assert_equal(initial_stats['dataBlocksUsed'], 0, "initial data blocks used")
        assert_equal(initial_stats['hashLock']['dedupeAdviceValid'], 0, "initial dedupe advice valid")
        assert_equal(initial_stats['index']['entriesIndexed'], 0, "initial entries indexed")

        # Write first time: 1000 blocks at offset 0 with tag "Direct2"
        log.info(f"Writing {block_count} blocks at offset 0 with tag 'Direct2'")
        slice1 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=0)
        slice1.write(tag="Direct2", dedupe=0, compress=0, fsync=True)

        # Verify first write
        log.info("Verifying first write")
        slice1.verify()

        # Check statistics after first write
        log.info("Checking statistics after first write")
        after_first = stats.vdo_stats(vdo)
        assert_equal(after_first['dataBlocksUsed'], block_count,
                    "data blocks used after first write")
        assert_equal(after_first['index']['entriesIndexed'], block_count,
                    "entries indexed after first write")
        assert_equal(after_first['biosIn']['write'], block_count,
                    "bios in write after first write")
        assert_equal(after_first['biosOut']['write'], block_count,
                    "bios out write after first write")

        # Overwrite with identical data (same tag "Direct2" to same location)
        # This tests self-deduplication at the same address
        log.info(f"Overwriting same {block_count} blocks with identical data (testing self-deduplication)")
        slice1.write(tag="Direct2", dedupe=0, compress=0, fsync=True)

        # Verify overwrite
        log.info("Verifying overwritten data")
        slice1.verify()

        # Check statistics after overwrite - should show deduplication
        log.info("Checking statistics after overwrite")
        after_overwrite = stats.vdo_stats(vdo)

        # Data blocks used should remain the same (no new physical blocks allocated)
        assert_equal(after_overwrite['dataBlocksUsed'], block_count,
                    "data blocks used after overwrite (unchanged due to deduplication)")

        # Entries indexed should remain the same (no new unique blocks)
        assert_equal(after_overwrite['index']['entriesIndexed'], block_count,
                    "entries indexed after overwrite (unchanged, same data)")

        # Bios in write should have doubled (received 2x block_count writes)
        assert_equal(after_overwrite['biosIn']['write'], block_count * 2,
                    "bios in write after overwrite (all writes counted)")

        # Bios out write should remain the same (no new physical writes)
        assert_equal(after_overwrite['biosOut']['write'], block_count,
                    "bios out write after overwrite (no new physical writes due to dedup)")

        # Dedupe advice valid should have increased by block_count
        dedupe_valid = after_overwrite['hashLock']['dedupeAdviceValid']
        assert_equal(dedupe_valid, block_count,
                    "dedupe advice valid after overwrite (all blocks deduplicated)")

        log.info("Direct02 test completed successfully")


def register(tests):
    tests.register("/vdo/direct/direct-02", t_direct_02)
