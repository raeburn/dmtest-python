"""VDO compression tests.

Tests VDO's compression functionality including writing compressible data,
verifying compression ratios, and ensuring deduplication works correctly
against compressed blocks.
"""
import logging as log

from dmtest.assertions import assert_equal, assert_near
from dmtest.gendatablocks import make_block_range
from dmtest.vdo.stats import vdo_stats
from dmtest.vdo.utils import BLOCK_SIZE, MB, fsync, standard_vdo, wait_for_index, wait_until_io_settled

def t_compress(fix):
    """Write compressible data, verify compression ratio and dedup against compressed blocks."""
    size = 4 * MB
    size_in_blocks = size // BLOCK_SIZE
    with standard_vdo(fix, compression="on") as vdo:
        range1 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                  block_count=size_in_blocks)
        range2 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                  block_count=size_in_blocks,
                                  offset=size_in_blocks)
        stats = vdo_stats(vdo)
        assert_equal(stats['dataBlocksUsed'], 0, 'data blocks used (init)')
        assert_equal(stats['hashLock']['dedupeAdviceValid'], 0,
                     'dedupe advice valid (init)')
        assert_equal(stats['hashLock']['dedupeAdviceStale'], 0,
                     'dedupe advice stale (init)')
        assert_equal(stats['biosIn']['write'], 0,
                     'write bios in (init)')
        log.info(f"data blocks used: {stats['dataBlocksUsed']}")
        wait_for_index(vdo)
        range1.write(tag="tag1", dedupe=0, compress=0.74, direct=True,
                     fsync=False)
        wait_until_io_settled(vdo)
        fsync(vdo)
        range1.verify()
        stats = vdo_stats(vdo)
        assert_equal(stats['biosIn']['write'], size_in_blocks,
                     'write bios in (1st write)')
        expected_size = (size_in_blocks + 2) // 3
        assert_near(stats['dataBlocksUsed'], expected_size, 1,
                    'data blocks used (1st write)')
        assert_equal(stats['index']['postsNotFound'], size_in_blocks,
                     'posts not found (1st write)')
        assert_equal(stats['index']['postsFound'], 0,
                     'posts found (1st write)')
        assert_equal(stats['hashLock']['dedupeAdviceValid'], 0,
                     'dedupe advice valid (1st write)')
        assert_equal(stats['hashLock']['dedupeAdviceStale'], 0,
                     'dedupe advice stale (1st write)')
        # Write same data again, different location.
        # Confirm we deduplicate against compressed blocks.
        range2.write(tag="tag1", dedupe=0, compress=0.74, direct=True,
                     fsync=False)
        stats2 = wait_until_io_settled(vdo)
        assert_equal(stats2['dataBlocksUsed'], stats['dataBlocksUsed'],
                     'data blocks used (2nd write)')
        assert_equal(stats2['index']['postsNotFound'], size_in_blocks,
                     'posts not found (2nd write)')
        assert_equal(stats2['index']['postsFound'], size_in_blocks,
                     'posts found (2nd write)')
        assert_equal(stats2['hashLock']['dedupeAdviceValid'],
                     size_in_blocks, 'dedupe advice valid (2nd write)')
        assert_equal(stats2['hashLock']['dedupeAdviceStale'], 0,
                     'dedupe advice stale (2nd write)')
        # Confirm we can read back compressed data correctly.
        range1.verify()
        # Check recovery of unreferenced compressed data.
        range1.trim()
        range2.trim(fsync=True)
        range1.verify()
        range2.verify()
        stats = vdo_stats(vdo)
        assert_equal(stats['dataBlocksUsed'], 0,
                     'data blocks used (discard)')

def register(tests):
    tests.register("/vdo/compress/compress", t_compress)
