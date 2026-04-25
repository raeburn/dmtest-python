"""Tests VDO's handling of hash collisions with compressed data.

Verifies that VDO correctly compresses blocks when every block has the same
MurmurHash3 hash but different content, testing the interaction between
the packer/compression system and collision detection.
"""

import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo, wait_until_packer_only
from dmtest.vdo.collision_helpers import write_colliding_blocks, verify_colliding_blocks
import dmtest.process as process
import dmtest.vdo.stats as stats


def t_compressing_collisions(fix) -> None:
    """Test VDO hash collisions with highly compressible data.

    Writes one highly compressible block, then writes 999,999 more blocks that
    all have the same MurmurHash3 hash as the first but different content.
    Verifies that VDO both compresses the blocks and correctly detects all
    collisions without data loss.
    """
    block_count = 1000000
    logical_size = 30 * 1024 * 1024 * 1024  # 30GB in bytes

    with standard_vdo(fix, logical_size=logical_size, compression="on") as vdo:
        log.info("Verifying initial VDO statistics")
        initial_stats = stats.vdo_stats(vdo)
        assert_equal(initial_stats['dataBlocksUsed'], 0)
        dedupe_valid = initial_stats['hashLock']['dedupeAdviceValid']
        dedupe_stale = initial_stats['hashLock']['dedupeAdviceStale']
        assert_equal(dedupe_valid, 0)
        assert_equal(dedupe_stale, 0)
        assert_equal(initial_stats['index']['entriesIndexed'], 0)

        # Write a single block of zeros (highly compressible)
        log.info("Writing single base block of zeros")
        with open(vdo.path, 'r+b') as f:
            f.seek(0)
            f.write(b'\x00' * BLOCK_SIZE)
            os.fsync(f.fileno())

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

        # Wait for all blocks to reach the packer
        log.info("Waiting for all blocks to reach the packer")
        packer_stats = wait_until_packer_only(vdo)
        log.info(f"Packer contains {packer_stats['packer']['compressedFragmentsInPacker']} fragments")

        # Flush the packer and get final statistics
        log.info("Flushing packer and verifying statistics")
        with open(vdo.path, 'r+b') as f:
            os.fsync(f.fileno())

        final_stats = stats.vdo_stats(vdo)

        # Check for dedupe timeouts first - if any exist, other values will be wrong
        timeouts = final_stats['dedupeAdviceTimeouts']
        assert_equal(timeouts, 0,
                    f"Expected 0 dedupe timeouts, got {timeouts}")

        # No valid dedupe advice (all collisions)
        valid = final_stats['hashLock']['dedupeAdviceValid']
        assert_equal(valid, 0,
                    f"Expected 0 valid dedupe advice, got {valid}")

        # Only 1 entry should be indexed (the first block's hash)
        indexed = final_stats['index']['entriesIndexed']
        assert_equal(indexed, 1,
                    f"Expected 1 entry indexed, got {indexed}")

        # Verify data integrity by reading back all data
        log.info("Dropping caches before verification")
        process.run("echo 1 > /proc/sys/vm/drop_caches")

        log.info("Verifying all blocks")
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
    tests.register("/vdo/collide/compressing-collisions", t_compressing_collisions)
