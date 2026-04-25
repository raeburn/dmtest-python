"""VDO test using compressed data (Compress01)

Basic compression and deduplication validation test that writes compressible
data, verifies the expected compression ratio, writes identical data again to
verify complete deduplication, and finally trims all data to verify that
compressed blocks are properly reclaimed.
"""

import logging as log

from dmtest.assertions import assert_equal, assert_near
from dmtest.gendatablocks import make_block_range
from dmtest.vdo.stats import vdo_stats
from dmtest.vdo.utils import BLOCK_SIZE, fsync, standard_vdo, wait_for_index, wait_until_packer_only

def testBasic(fix):
    """Basic VDO testing with compressible data"""
    block_count = 5000

    with standard_vdo(fix, compression="on", slab_bits=17) as vdo:
        # Create two block ranges for testing
        range1 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                  block_count=block_count)
        range2 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                  block_count=block_count,
                                  offset=block_count)

        # Check initial statistics
        stats = vdo_stats(vdo)
        assert_equal(stats['dataBlocksUsed'], 0,
                     'Starting data blocks used should be zero')
        assert_equal(stats['hashLock']['dedupeAdviceValid'], 0,
                     'Starting dedupe advice valid should be zero')
        assert_equal(stats['hashLock']['dedupeAdviceStale'], 0,
                     'Starting dedupe advice stale should be zero')

        wait_for_index(vdo)

        # Write some blocks with 74% compressible data (compresses ~3:1 with overhead)
        range1.write(tag="d1", compress=0.74, fsync=False)

        # Wait for all I/Os to complete or reach the packer for predictable compression
        wait_until_packer_only(vdo)
        fsync(vdo)

        # Verify the data
        range1.verify()

        # At 3:1 compression, blocks used should be approximately 1/3 of the total
        stats = vdo_stats(vdo)
        blocks_used = stats['dataBlocksUsed']
        assert_near(blocks_used, block_count / 3, 2,
                   'Number of data blocks that should be compressed')
        assert_equal(stats['hashLock']['dedupeAdviceValid'], 0,
                     'Dedupe advice valid should be zero')
        assert_equal(stats['hashLock']['dedupeAdviceStale'], 0,
                     'Dedupe advice stale should be zero')

        log.info(f"After first write: data blocks used = {blocks_used}")

        # Write the blocks again to a different location, expecting complete deduplication
        range2.write(tag="d1", compress=0.74, fsync=False)

        stats2 = wait_until_packer_only(vdo)
        log.info(f"After second write: data blocks used = {stats2['dataBlocksUsed']}")

        assert_equal(stats2['dataBlocksUsed'], blocks_used,
                     'Data blocks used should not change')
        assert_equal(stats2['hashLock']['dedupeAdviceValid'], block_count,
                     f'Dedupe advice valid should be {block_count}')
        assert_equal(stats2['hashLock']['dedupeAdviceStale'], 0,
                     'Dedupe advice stale should be zero')

        # Trim all the data and verify that the compressed blocks are reclaimed
        range1.trim()
        range2.trim(fsync=True)

        stats = vdo_stats(vdo)
        assert_equal(stats['dataBlocksUsed'], 0,
                     'Data blocks used should be zero after trim')

def register(tests):
    tests.register_batch(
        "/vdo/compress_01/",
        [
            ("basic", testBasic),
        ],
    )
