"""Tests VDO's handling of massive hash collisions with a single hash value.

Verifies that VDO correctly stores all blocks when every block has the same
MurmurHash3 hash but different content, detecting collisions without data loss.
"""

import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo
from dmtest.vdo.collision_helpers import write_colliding_blocks, verify_colliding_blocks
import dmtest.gendatablocks as generator
import dmtest.process as process
import dmtest.vdo.stats as stats


def t_many_collisions(fix) -> None:
    """Test VDO with massive hash collisions to a single hash value.

    Writes one block, then writes 999,999 more blocks that all have the same
    MurmurHash3 hash as the first but different content. Verifies that VDO
    stores all blocks and correctly detects all collisions without data loss.
    """
    block_count = 1000000
    logical_size = 30 * 1024 * 1024 * 1024  # 30GB in bytes

    with standard_vdo(fix, logical_size=logical_size) as vdo:
        log.info("Verifying initial VDO statistics")
        initial_stats = stats.vdo_stats(vdo)
        assert_equal(initial_stats['dataBlocksUsed'], 0)
        dedupe_valid = initial_stats['hashLock']['dedupeAdviceValid']
        dedupe_stale = initial_stats['hashLock']['dedupeAdviceStale']
        assert_equal(dedupe_valid, 0)
        assert_equal(dedupe_stale, 0)
        assert_equal(initial_stats['index']['entriesIndexed'], 0)

        # Write a single block
        log.info("Writing single base block")
        single_range = generator.make_block_range(
            path=vdo.path,
            block_size=BLOCK_SIZE,
            block_count=1,
            offset=0)
        single_range.write(tag="Single", dedupe=0, compress=0, fsync=True)

        # Write N-1 blocks with chained collisions (all have same hash as first block)
        log.info(f"Writing {block_count - 1} blocks with chained hash collisions")
        write_colliding_blocks(
            source_path=vdo.path,
            dest_path=vdo.path,
            block_count=block_count - 1,
            source_offset=0,
            dest_offset=1,
            block_size=BLOCK_SIZE,
            chain=True)

        # Verify statistics
        log.info("Verifying statistics after all writes")
        final_stats = stats.vdo_stats(vdo)

        # Check for dedupe timeouts first - if any exist, other values will be wrong
        timeouts = final_stats['dedupeAdviceTimeouts']
        assert_equal(timeouts, 0,
                    f"Expected 0 dedupe timeouts, got {timeouts}")

        # Only 1 entry should be indexed (the first block's hash)
        indexed = final_stats['index']['entriesIndexed']
        assert_equal(indexed, 1,
                    f"Expected 1 entry indexed, got {indexed}")

        # All blocks should be stored physically (no dedupe)
        blocks_used = final_stats['dataBlocksUsed']
        assert_equal(blocks_used, block_count,
                    f"Expected {block_count} blocks used, got {blocks_used}")

        # No valid dedupe advice (all collisions)
        valid = final_stats['hashLock']['dedupeAdviceValid']
        assert_equal(valid, 0,
                    f"Expected 0 valid dedupe advice, got {valid}")

        # All N-1 collisions should be detected as stale or concurrent hash collisions
        # (The first write has neither stale nor concurrent collisions)
        stale = final_stats['hashLock']['dedupeAdviceStale']
        concurrent_collisions = final_stats['hashLock']['concurrentHashCollisions']
        stale_plus_concurrent = stale + concurrent_collisions
        assert_equal(stale_plus_concurrent, block_count - 1,
                    f"Expected {block_count - 1} stale+concurrent collisions, "
                    f"got {stale_plus_concurrent} (stale={stale}, concurrent={concurrent_collisions})")

        # Verify data integrity by reading back all data
        log.info("Dropping caches before verification")
        process.run("echo 1 > /proc/sys/vm/drop_caches")

        log.info("Verifying base block")
        single_range.verify()

        log.info("Verifying chained colliding blocks")
        verify_colliding_blocks(
            source_path=vdo.path,
            verify_path=vdo.path,
            block_count=block_count - 1,
            source_offset=0,
            verify_offset=1,
            block_size=BLOCK_SIZE,
            chain=True)

        log.info("Test completed successfully")


def register(tests):
    tests.register("/vdo/collide/many-collisions", t_many_collisions)
