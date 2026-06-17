"""Tests VDO's handling of MurmurHash3 hash collisions.

Verifies that the UDS deduplication index correctly rejects false duplicates,
stores all unique blocks despite hash collisions, and handles collisions
correctly even with compression enabled.
"""

import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.vdo.murmur3collide import generate_colliding_blocks
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo, wait_until_io_settled
import dmtest.gendatablocks as generator
import dmtest.process as process
import dmtest.vdo.stats as stats

BLOCK_COUNT = 1000000
LOGICAL_SIZE = 30 * 1024 * 1024 * 1024


def _assert_initial_stats(vdo):
    initial = stats.vdo_stats(vdo)
    assert_equal(initial['dataBlocksUsed'], 0)
    assert_equal(initial['hashLock']['dedupeAdviceValid'], 0)
    assert_equal(initial['hashLock']['dedupeAdviceStale'], 0)
    assert_equal(initial['index']['entriesIndexed'], 0)


def _write_colliding(source_path, dest_path, block_count,
                     source_offset, dest_offset, chain):
    if chain:
        with open(source_path, 'rb') as src:
            src.seek(source_offset * BLOCK_SIZE)
            base = src.read(BLOCK_SIZE)
        with open(dest_path, 'r+b') as dest:
            for i, block in enumerate(generate_colliding_blocks(
                    base, count=block_count, block_size=BLOCK_SIZE, chain=True)):
                dest.seek((dest_offset + i) * BLOCK_SIZE)
                dest.write(block)
                if (i + 1) % 100000 == 0:
                    log.info(f"  Written {i + 1}/{block_count} blocks")
            os.fsync(dest.fileno())
    else:
        with open(source_path, 'rb') as src, open(dest_path, 'r+b') as dest:
            for i in range(block_count):
                src.seek((source_offset + i) * BLOCK_SIZE)
                source_block = src.read(BLOCK_SIZE)
                collided = next(generate_colliding_blocks(
                    source_block, count=1, block_size=BLOCK_SIZE, chain=False))
                dest.seek((dest_offset + i) * BLOCK_SIZE)
                dest.write(collided)
                if (i + 1) % 100000 == 0:
                    log.info(f"  Processed {i + 1}/{block_count} blocks")
            os.fsync(dest.fileno())


def _verify_colliding(source_path, verify_path, block_count,
                      source_offset, verify_offset, chain):
    if chain:
        with open(source_path, 'rb') as src:
            src.seek(source_offset * BLOCK_SIZE)
            base = src.read(BLOCK_SIZE)
        with open(verify_path, 'rb') as vf:
            for i, expected in enumerate(generate_colliding_blocks(
                    base, count=block_count, block_size=BLOCK_SIZE, chain=True)):
                vf.seek((verify_offset + i) * BLOCK_SIZE)
                actual = vf.read(BLOCK_SIZE)
                if expected != actual:
                    raise AssertionError(f"Block {i} verification failed")
                if (i + 1) % 100000 == 0:
                    log.info(f"  Verified {i + 1}/{block_count} blocks")
    else:
        with open(source_path, 'rb') as src, open(verify_path, 'rb') as vf:
            for i in range(block_count):
                src.seek((source_offset + i) * BLOCK_SIZE)
                source_block = src.read(BLOCK_SIZE)
                expected = next(generate_colliding_blocks(
                    source_block, count=1, block_size=BLOCK_SIZE, chain=False))
                vf.seek((verify_offset + i) * BLOCK_SIZE)
                actual = vf.read(BLOCK_SIZE)
                if expected != actual:
                    raise AssertionError(f"Block {i} verification failed")
                if (i + 1) % 100000 == 0:
                    log.info(f"  Verified {i + 1}/{block_count} blocks")


def t_two_sets(fix):
    """Two datasets with colliding hashes but different content.

    Writes unique blocks, then writes a second set where each block has the
    same hash as the corresponding block in the first set but different data.
    Verifies VDO stores all blocks and detects all collisions.
    """
    with standard_vdo(fix, logical_size=LOGICAL_SIZE) as vdo:
        _assert_initial_stats(vdo)

        first_range = generator.make_block_range(
            path=vdo.path, block_size=BLOCK_SIZE,
            block_count=BLOCK_COUNT, offset=0)
        first_range.write(tag="First", dedupe=0, compress=0, fsync=True)

        after_first = stats.vdo_stats(vdo)
        assert_equal(after_first['dedupeAdviceTimeouts'], 0)
        assert_equal(after_first['dataBlocksUsed'], BLOCK_COUNT)
        assert_equal(after_first['hashLock']['dedupeAdviceStale'], 0)
        assert_equal(after_first['index']['entriesIndexed'], BLOCK_COUNT)

        log.info(f"Writing {BLOCK_COUNT} blocks with colliding hashes")
        _write_colliding(vdo.path, vdo.path, BLOCK_COUNT,
                         source_offset=0, dest_offset=BLOCK_COUNT, chain=False)

        after_second = stats.vdo_stats(vdo)
        assert_equal(after_second['dataBlocksUsed'], 2 * BLOCK_COUNT)
        assert_equal(after_second['index']['entriesIndexed'], BLOCK_COUNT)

        stale = after_second['hashLock']['dedupeAdviceStale']
        timeouts = after_second['dedupeAdviceTimeouts']
        assert_equal(stale + timeouts, BLOCK_COUNT)

        process.run("echo 1 > /proc/sys/vm/drop_caches")
        first_range.verify()
        _verify_colliding(vdo.path, vdo.path, BLOCK_COUNT,
                          source_offset=0, verify_offset=BLOCK_COUNT, chain=False)


def t_many_collisions(fix):
    """All blocks share a single hash value.

    Writes one block, then 999,999 more via chained transformation so every
    block has the same hash but different content. Verifies VDO stores all
    blocks and detects all collisions.
    """
    with standard_vdo(fix, logical_size=LOGICAL_SIZE) as vdo:
        _assert_initial_stats(vdo)

        single = generator.make_block_range(
            path=vdo.path, block_size=BLOCK_SIZE,
            block_count=1, offset=0)
        single.write(tag="Single", dedupe=0, compress=0, fsync=True)

        log.info(f"Writing {BLOCK_COUNT - 1} chained colliding blocks")
        _write_colliding(vdo.path, vdo.path, BLOCK_COUNT - 1,
                         source_offset=0, dest_offset=1, chain=True)

        final = stats.vdo_stats(vdo)
        assert_equal(final['dedupeAdviceTimeouts'], 0)
        assert_equal(final['index']['entriesIndexed'], 1)
        assert_equal(final['dataBlocksUsed'], BLOCK_COUNT)
        assert_equal(final['hashLock']['dedupeAdviceValid'], 0)

        stale = final['hashLock']['dedupeAdviceStale']
        concurrent = final['hashLock']['concurrentHashCollisions']
        assert_equal(stale + concurrent, BLOCK_COUNT - 1)

        process.run("echo 1 > /proc/sys/vm/drop_caches")
        single.verify()
        _verify_colliding(vdo.path, vdo.path, BLOCK_COUNT - 1,
                          source_offset=0, verify_offset=1, chain=True)


def t_compressing_collisions(fix):
    """Hash collisions with highly compressible data.

    Writes one zero block, then 999,999 chained collisions. The base block
    is highly compressible and the collision transform preserves most of that
    compressibility. Verifies the interaction between compression and
    collision detection.
    """
    with standard_vdo(fix, logical_size=LOGICAL_SIZE, compression="on") as vdo:
        _assert_initial_stats(vdo)

        with open(vdo.path, 'r+b') as f:
            f.seek(0)
            f.write(b'\x00' * BLOCK_SIZE)
            os.fsync(f.fileno())

        log.info(f"Writing {BLOCK_COUNT - 1} chained colliding blocks")
        _write_colliding(vdo.path, vdo.path, BLOCK_COUNT - 1,
                         source_offset=0, dest_offset=1, chain=True)

        wait_until_io_settled(vdo)
        with open(vdo.path, 'r+b') as f:
            os.fsync(f.fileno())

        final = stats.vdo_stats(vdo)
        assert_equal(final['dedupeAdviceTimeouts'], 0)
        assert_equal(final['hashLock']['dedupeAdviceValid'], 0)
        assert_equal(final['index']['entriesIndexed'], 1)

        process.run("echo 1 > /proc/sys/vm/drop_caches")
        _verify_colliding(vdo.path, vdo.path, BLOCK_COUNT - 1,
                          source_offset=0, verify_offset=1, chain=True)


def register(tests):
    tests.register_batch(
        "/vdo/collide/",
        [
            ("two-sets", t_two_sets),
            ("many-collisions", t_many_collisions),
            ("compressing-collisions", t_compressing_collisions),
        ],
    )
