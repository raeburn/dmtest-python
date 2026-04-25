"""Shared helper functions for VDO hash collision testing.

Provides utilities for writing and verifying datasets with MurmurHash3
collisions, supporting both independent and chained transformations.
"""

import logging as log
import os

from dmtest.vdo.murmur3collide import generate_colliding_blocks
from dmtest.vdo.utils import BLOCK_SIZE


def write_colliding_blocks(source_path, dest_path, block_count,
                           source_offset, dest_offset,
                           block_size=BLOCK_SIZE, chain=False):
    """Write hash-colliding blocks to destination.

    For independent transformations (chain=False), reads each block from
    source and transforms it independently. For chained transformations
    (chain=True), reads a single base block and generates a chain where
    each output transforms the previous output.

    Args:
        source_path: Device/file to read from
        dest_path: Device/file to write to (may be same as source_path)
        block_count: Number of blocks to process/generate
        source_offset: Starting block offset for reads (for chain=False, reads
            multiple blocks; for chain=True, reads single base block)
        dest_offset: Starting block offset for writes
        block_size: Size of each block in bytes
        chain: If True, use chained transformations; if False, independent transformations
    """
    if chain:
        _write_chained(source_path, dest_path, block_count, source_offset,
                      dest_offset, block_size)
    else:
        _write_independent(source_path, dest_path, block_count, source_offset,
                          dest_offset, block_size)


def verify_colliding_blocks(source_path, verify_path, block_count,
                            source_offset, verify_offset,
                            block_size=BLOCK_SIZE, chain=False):
    """Verify hash-colliding blocks match expected transformations.

    For independent transformations (chain=False), verifies each output
    block is an independent transformation of the corresponding source block.
    For chained transformations (chain=True), verifies blocks form a chain
    starting from a single base block.

    Args:
        source_path: Device/file to read source/base blocks from
        verify_path: Device/file to read verification blocks from
        block_count: Number of blocks to verify
        source_offset: Starting block offset for source reads (for chain=False,
            reads multiple blocks; for chain=True, reads single base block)
        verify_offset: Starting block offset for verification reads
        block_size: Size of each block in bytes
        chain: If True, verify chained transformations; if False, independent
    """
    if chain:
        _verify_chained(source_path, verify_path, block_count, source_offset,
                       verify_offset, block_size)
    else:
        _verify_independent(source_path, verify_path, block_count, source_offset,
                           verify_offset, block_size)


def _write_independent(source_path, dest_path, block_count, source_offset,
                      dest_offset, block_size):
    """Write independent collision transformations (Collide01 pattern)."""
    log.info(f"Transforming {block_count} blocks independently from offset "
             f"{source_offset} to {dest_offset}")

    with open(source_path, 'rb') as src:
        with open(dest_path, 'r+b') as dest:
            for i in range(block_count):
                # Read source block
                src.seek((source_offset + i) * block_size)
                source_block = src.read(block_size)

                if len(source_block) != block_size:
                    raise IOError(f"Failed to read block {i}: got {len(source_block)} bytes")

                # Transform independently
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

    log.info(f"Completed independent transformation of {block_count} blocks")


def _write_chained(base_path, dest_path, block_count, base_offset,
                  dest_offset, block_size):
    """Write chained collision transformations (Collide02/03 pattern)."""
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

        # Flush to disk
        os.fsync(dest.fileno())

    log.info(f"Completed writing {block_count} chained colliding blocks")


def _verify_independent(source_path, verify_path, block_count, source_offset,
                       verify_offset, block_size):
    """Verify independent collision transformations."""
    log.info(f"Verifying {block_count} independently transformed blocks at offset {verify_offset}")

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

    log.info(f"Successfully verified {block_count} independently transformed blocks")


def _verify_chained(base_path, verify_path, block_count, base_offset,
                   verify_offset, block_size):
    """Verify chained collision transformations."""
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
