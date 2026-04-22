"""Tests VDO's handling of MurmurHash3 hash collisions.

Verifies that the UDS deduplication index correctly rejects false duplicates
and stores all unique blocks despite hash collisions.
"""

import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo
from dmtest.vdo.murmur3collide import generate_colliding_blocks
import dmtest.gendatablocks as generator
import dmtest.process as process
import dmtest.vdo.stats as stats


def _write_colliding_blocks(source_path: str,
                            dest_path: str,
                            block_count: int,
                            source_offset: int,
                            dest_offset: int,
                            block_size: int = BLOCK_SIZE) -> None:
    """Read blocks from source and write transformed versions to dest.

    Reads each block from source_path starting at source_offset, transforms
    it using murmur3_collide to create a block with different data but the
    same MurmurHash3 hash, and writes it to dest_path at dest_offset.

    This creates independent transformations (not chained), matching the
    Collide01 pattern where each output block is an independent transformation
    of the corresponding input block.

    Args:
        source_path: Device/file to read from
        dest_path: Device/file to write to (may be same as source_path)
        block_count: Number of blocks to process
        source_offset: Starting block offset for reads
        dest_offset: Starting block offset for writes
        block_size: Size of each block in bytes
    """
    log.info(f"Transforming {block_count} blocks from offset {source_offset} to {dest_offset}")

    with open(source_path, 'rb') as src:
        with open(dest_path, 'r+b') as dest:
            for i in range(block_count):
                # Read source block
                src.seek((source_offset + i) * block_size)
                source_block = src.read(block_size)

                if len(source_block) != block_size:
                    raise IOError(f"Failed to read block {i}: got {len(source_block)} bytes")

                # Transform independently (chain=False means each transformation
                # is based on the source block, not the previous output)
                collided_block = next(generate_colliding_blocks(
                    source_block, count=1, block_size=block_size, chain=False))

                # Write to destination
                dest.seek((dest_offset + i) * block_size)
                dest.write(collided_block)

                # Log progress periodically
                if (i + 1) % 100000 == 0:
                    log.info(f"  Processed {i + 1}/{block_count} blocks")

            # Flush to disk
            os.fsync(dest.fileno())

    log.info(f"Completed transformation of {block_count} blocks")


def _verify_colliding_blocks(source_path: str,
                             verify_path: str,
                             block_count: int,
                             source_offset: int,
                             verify_offset: int,
                             block_size: int = BLOCK_SIZE) -> None:
    """Verify that blocks at verify_offset are transformed versions of source blocks.

    Reads each block from source and the corresponding block from verify location,
    transforms the source block, and verifies it matches the verify block.

    Args:
        source_path: Device/file to read source blocks from
        verify_path: Device/file to read verify blocks from
        block_count: Number of blocks to verify
        source_offset: Starting block offset for source reads
        verify_offset: Starting block offset for verify reads
        block_size: Size of each block in bytes
    """
    log.info(f"Verifying {block_count} transformed blocks at offset {verify_offset}")

    with open(source_path, 'rb') as src:
        with open(verify_path, 'rb') as verify:
            for i in range(block_count):
                # Read source block
                src.seek((source_offset + i) * block_size)
                source_block = src.read(block_size)

                # Read verify block
                verify.seek((verify_offset + i) * block_size)
                verify_block = verify.read(block_size)

                if len(source_block) != block_size or len(verify_block) != block_size:
                    raise IOError(f"Failed to read blocks for verification at block {i}")

                # Transform source and compare
                expected_block = next(generate_colliding_blocks(
                    source_block, count=1, block_size=block_size, chain=False))

                if expected_block != verify_block:
                    raise AssertionError(
                        f"Block {i} verification failed: data does not match expected transformation")

                # Log progress periodically
                if (i + 1) % 100000 == 0:
                    log.info(f"  Verified {i + 1}/{block_count} blocks")

    log.info(f"Successfully verified {block_count} transformed blocks")


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
        _write_colliding_blocks(
            source_path=vdo.path,
            dest_path=vdo.path,
            block_count=block_count,
            source_offset=0,
            dest_offset=block_count,
            block_size=BLOCK_SIZE)

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
        _verify_colliding_blocks(
            source_path=vdo.path,
            verify_path=vdo.path,
            block_count=block_count,
            source_offset=0,
            verify_offset=block_count,
            block_size=BLOCK_SIZE)

        log.info("Test completed successfully")


def register(tests):
    tests.register("/vdo/collide/two-sets", t_two_sets)
