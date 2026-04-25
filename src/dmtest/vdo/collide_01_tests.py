"""Tests VDO's handling of MurmurHash3 hash collisions.

Verifies that the UDS deduplication index correctly rejects false duplicates
and stores all unique blocks despite hash collisions.
"""

import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo
from dmtest.vdo.collision_helpers import write_colliding_blocks, verify_colliding_blocks
import dmtest.gendatablocks as generator
import dmtest.process as process
import dmtest.vdo.stats as stats


def t_two_sets(fix) -> None:
    """Test that VDO correctly handles hash collisions without data corruption.

    Writes two datasets with identical MurmurHash3 hashes but different content.
    Verifies that VDO stores all blocks physically and does not perform false
    deduplication despite 100% hash collision rate.
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

        # Write first dataset of unique blocks
        log.info(f"Writing first dataset of {block_count} blocks")
        first_range = generator.make_block_range(
            path=vdo.path,
            block_size=BLOCK_SIZE,
            block_count=block_count,
            offset=0)
        first_range.write(tag="First", dedupe=0, compress=0, fsync=True)

        # Verify first dataset statistics
        log.info("Verifying statistics after first dataset")
        stats_after_first = stats.vdo_stats(vdo)

        # Check for dedupe timeouts first - if any exist, other values will be wrong
        timeouts = stats_after_first['dedupeAdviceTimeouts']
        assert_equal(timeouts, 0,
                    f"Expected 0 dedupe timeouts, got {timeouts}")

        # All blocks should be stored uniquely
        blocks_used = stats_after_first['dataBlocksUsed']
        assert_equal(blocks_used, block_count,
                    f"Expected {block_count} blocks used, got {blocks_used}")

        # No stale advice (no hash collisions yet)
        stale = stats_after_first['hashLock']['dedupeAdviceStale']
        assert_equal(stale, 0,
                    f"Expected 0 stale advice, got {stale}")

        # All blocks should be indexed
        indexed = stats_after_first['index']['entriesIndexed']
        assert_equal(indexed, block_count,
                    f"Expected {block_count} entries indexed, got {indexed}")

        # Write second dataset with colliding hashes
        log.info(f"Writing second dataset of {block_count} blocks with colliding hashes")
        write_colliding_blocks(
            source_path=vdo.path,
            dest_path=vdo.path,
            block_count=block_count,
            source_offset=0,
            dest_offset=block_count,
            block_size=BLOCK_SIZE,
            chain=False)

        # Verify second dataset statistics
        log.info("Verifying statistics after second dataset")
        stats_after_second = stats.vdo_stats(vdo)

        # All blocks from both datasets should be stored physically
        blocks_used_final = stats_after_second['dataBlocksUsed']
        assert_equal(blocks_used_final, 2 * block_count,
                    f"Expected {2 * block_count} blocks used, got {blocks_used_final}")

        # Index should still only have entries for first dataset (second had hash collisions)
        indexed_final = stats_after_second['index']['entriesIndexed']
        assert_equal(indexed_final, block_count,
                    f"Expected {block_count} entries indexed, got {indexed_final}")

        # Every block in second set should generate either stale advice or a timeout
        stale_final = stats_after_second['hashLock']['dedupeAdviceStale']
        timeouts_final = stats_after_second['dedupeAdviceTimeouts']
        stale_plus_timeouts = stale_final + timeouts_final
        assert_equal(stale_plus_timeouts, block_count,
                    f"Expected {block_count} stale+timeouts, got {stale_plus_timeouts} "
                    f"(stale={stale_final}, timeouts={timeouts_final})")

        # Verify data integrity by reading back both datasets
        log.info("Dropping caches before verification")
        process.run("echo 1 > /proc/sys/vm/drop_caches")

        log.info("Verifying first dataset")
        first_range.verify()

        log.info("Verifying second dataset")
        verify_colliding_blocks(
            source_path=vdo.path,
            verify_path=vdo.path,
            block_count=block_count,
            source_offset=0,
            verify_offset=block_count,
            block_size=BLOCK_SIZE,
            chain=False)

        log.info("Test completed successfully")


def register(tests):
    tests.register("/vdo/collide/two-sets", t_two_sets)
