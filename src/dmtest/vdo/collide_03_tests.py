"""Tests VDO's handling of hash collisions with compressed data.

Verifies that VDO correctly compresses blocks when every block has the same
MurmurHash3 hash but different content, testing the interaction between
the packer/compression system and collision detection.
"""

import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo
from dmtest.vdo.murmur3collide import generate_colliding_blocks
import dmtest.process as process
import dmtest.vdo.stats as stats


def _write_chained_colliding_blocks(base_path: str,
                                    dest_path: str,
                                    block_count: int,
                                    base_offset: int,
                                    dest_offset: int,
                                    block_size: int = BLOCK_SIZE,
                                    fsync: bool = True) -> None:
    """Read base block and write chained colliding blocks to dest.

    Reads a single block from base_path at base_offset, then generates
    block_count colliding blocks using chaining (each block transforms the
    previous output). Writes the results to dest_path starting at dest_offset.

    Args:
        base_path: Device/file to read base block from
        dest_path: Device/file to write to (may be same as base_path)
        block_count: Number of colliding blocks to generate
        base_offset: Block offset for reading the base block
        dest_offset: Starting block offset for writes
        block_size: Size of each block in bytes
        fsync: Whether to fsync after all writes
    """
    log.info(f"Generating {block_count} chained colliding blocks from offset {base_offset}")

    # Read the base block
    with open(base_path, 'rb') as src:
        src.seek(base_offset * block_size)
        base_block = src.read(block_size)

        if len(base_block) != block_size:
            raise IOError(f"Failed to read base block: got {len(base_block)} bytes")

    # Generate and write colliding blocks with chaining
    with open(dest_path, 'r+b') as dest:
        for i, collided_block in enumerate(generate_colliding_blocks(
                base_block, count=block_count, block_size=block_size, chain=True)):

            # Write to destination
            dest.seek((dest_offset + i) * block_size)
            dest.write(collided_block)

            # Log progress periodically
            if (i + 1) % 100000 == 0:
                log.info(f"  Written {i + 1}/{block_count} blocks")

        # Flush to disk if requested
        if fsync:
            os.fsync(dest.fileno())

    log.info(f"Completed writing {block_count} chained colliding blocks")


def _verify_chained_colliding_blocks(base_path: str,
                                     verify_path: str,
                                     block_count: int,
                                     base_offset: int,
                                     verify_offset: int,
                                     block_size: int = BLOCK_SIZE) -> None:
    """Verify that blocks are chained transformations of the base block.

    Reads the base block and generates the expected chain of colliding blocks,
    then verifies each matches what's stored on disk.

    Args:
        base_path: Device/file to read base block from
        verify_path: Device/file to read verify blocks from
        block_count: Number of blocks to verify
        base_offset: Block offset for reading the base block
        verify_offset: Starting block offset for verify reads
        block_size: Size of each block in bytes
    """
    log.info(f"Verifying {block_count} chained colliding blocks at offset {verify_offset}")

    # Read the base block
    with open(base_path, 'rb') as src:
        src.seek(base_offset * block_size)
        base_block = src.read(block_size)

        if len(base_block) != block_size:
            raise IOError(f"Failed to read base block: got {len(base_block)} bytes")

    # Verify each block in the chain
    with open(verify_path, 'rb') as verify:
        for i, expected_block in enumerate(generate_colliding_blocks(
                base_block, count=block_count, block_size=block_size, chain=True)):

            # Read verify block
            verify.seek((verify_offset + i) * block_size)
            verify_block = verify.read(block_size)

            if len(verify_block) != block_size:
                raise IOError(f"Failed to read verify block {i}: got {len(verify_block)} bytes")

            if expected_block != verify_block:
                raise AssertionError(
                    f"Block {i} verification failed: data does not match expected chained transformation")

            # Log progress periodically
            if (i + 1) % 100000 == 0:
                log.info(f"  Verified {i + 1}/{block_count} blocks")

    log.info(f"Successfully verified {block_count} chained colliding blocks")


def _wait_until_packer_only(vdo):
    """Wait until all VDO I/Os are completed or waiting in the packer.

    Returns VDO stats collected after waiting.
    """
    import time
    while True:
        current_stats = stats.vdo_stats(vdo)
        working_blocks = (current_stats['currentVIOsInProgress']
                         - current_stats['packer']['compressedFragmentsInPacker'])
        if working_blocks == 0:
            return current_stats
        log.info(f"{working_blocks} blocks have not gotten to the packer yet")
        time.sleep(0.1)


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
        _write_chained_colliding_blocks(
            base_path=vdo.path,
            dest_path=vdo.path,
            block_count=block_count - 1,
            base_offset=0,
            dest_offset=1,
            block_size=BLOCK_SIZE,
            fsync=True)

        # Wait for all blocks to reach the packer
        log.info("Waiting for all blocks to reach the packer")
        packer_stats = _wait_until_packer_only(vdo)
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
        _verify_chained_colliding_blocks(
            base_path=vdo.path,
            verify_path=vdo.path,
            block_count=block_count - 1,
            base_offset=0,
            verify_offset=1,
            block_size=BLOCK_SIZE)

        log.info("Test completed successfully")


def register(tests):
    tests.register("/vdo/collide/compressing-collisions", t_compressing_collisions)
