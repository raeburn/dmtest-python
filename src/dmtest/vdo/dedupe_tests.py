"""VDO deduplication tests.

Tests VDO's deduplication functionality at various dedupe rates (0%, 50%, 75%),
verifying statistics are correct and that duplicate data is properly identified
across different write patterns (same offset, different offsets).
"""
from dmtest.assertions import assert_equal, assert_near
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo, wait_for_index
import dmtest.gendatablocks as generator
import dmtest.process as process
import dmtest.vdo.stats as stats

def verify_dedupe(vdo, dedupe: float):
    # Wait for index to be online
    wait_for_index(vdo)

    # Get stats before any writing
    stats_pre = stats.vdo_stats(vdo)

    # Write 5000 4k blocks of specified dedupe
    br = generator.make_block_range(path=vdo.path, block_size=4096, block_count=5000)
    br.write(tag="tag1", dedupe=dedupe, compress=0.0, fsync=True)
    # Grab the current stats and determine the difference between the two. This
    # will contain only the information related to just the writing. Compare
    # the expected dedupe rate vs the actual from the stats.
    stats_post = stats.vdo_stats(vdo)
    stats_delta = stats.make_delta_stats(stats_post, stats_pre)
    blocks_written = stats_delta["logicalBlocksUsed"]
    blocks_deduped = blocks_written - stats_delta["dataBlocksUsed"]
    actual = float(blocks_deduped / blocks_written)
    assert_near(actual, dedupe, 0.01)
    # Verify that the data on disk is what we wrote
    br.verify()

def t_dedupe0(fix):
    with standard_vdo(fix) as vdo:
        verify_dedupe(vdo, 0.0)

def t_dedupe50(fix):
    with standard_vdo(fix) as vdo:
        verify_dedupe(vdo, 0.50)

def t_dedupe75(fix):
    with standard_vdo(fix) as vdo:
        verify_dedupe(vdo, 0.75)

def t_dedupeWithOffsetAndRestart(fix):
    """
    Write the same data at two offsets and ensure that VDO statistics reflect
    the appropriate values

    After writing the data for the first round:
        dataBlocksUsed should equal the total number of blocks written
        entriesIndexed should equal the total number of blocks written

    After writing the same data a second time:
        dedupeAdviceValid should equal the number of blocks written originally
    """
    block_count = 5000
    size = block_count * BLOCK_SIZE
    with standard_vdo(fix) as vdo:
        range1 = generator.make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                            block_count=block_count)
        range2 = generator.make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                            block_count=block_count,
                                            offset=block_count)
        # Write {size} data at 0 offset
        range1.write(tag="hello", dedupe=0, compress=0, fsync=True)

        # Verify first round statistics equal total data written
        vdo_stats_before = stats.vdo_stats(vdo)
        assert_equal(vdo_stats_before['dataBlocksUsed'], block_count)
        assert_equal(vdo_stats_before['index']['entriesIndexed'], block_count)

        # Write {size} data at {size} offset
        range2.write(tag="hello", dedupe=0, compress=0, fsync=True)

        # Verify second round statistics reflect effective deduplication
        vdo_stats_after = stats.vdo_stats(vdo)
        assert_equal(vdo_stats_after['hashLock']['dedupeAdviceValid'], block_count)

    # Re-assemble the VDO device, but this time without formatting
    with standard_vdo(fix, format=False) as vdo:
        range1.update_path(vdo.path)
        range2.update_path(vdo.path)
        # We don't care about waiting for the index if we're just
        # reading.
        range1.verify()
        range2.verify()

def t_dedupeWithOverwrite(fix):
    """
    Write the same data at the same offset twice and make sure that it verifies
    cleanly.
    """
    block_count = 5000
    size = block_count * BLOCK_SIZE
    with standard_vdo(fix) as vdo:
        range = generator.make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                           block_count=block_count)
        range.write(tag="tomato", dedupe=0, compress=0, fsync=True)

        vdo_stats_before = stats.vdo_stats(vdo)
        assert_equal(vdo_stats_before['dataBlocksUsed'], block_count)
        assert_equal(vdo_stats_before['hashLock']['dedupeAdviceValid'], 0)
        assert_equal(vdo_stats_before['hashLock']['dedupeAdviceStale'], 0)
        assert_equal(vdo_stats_before['dedupeAdviceTimeouts'], 0)
        assert_equal(vdo_stats_before['biosIn']['write'], block_count)
        assert_equal(vdo_stats_before['biosOut']['write'], block_count)

        range.write(tag="tomato", dedupe=0, compress=0, fsync=True)

        vdo_stats_after = stats.vdo_stats(vdo)
        assert_equal(vdo_stats_after['dataBlocksUsed'], block_count)
        assert_equal(vdo_stats_after['hashLock']['dedupeAdviceValid'], block_count)
        assert_equal(vdo_stats_after['hashLock']['dedupeAdviceStale'], 0)
        assert_equal(vdo_stats_after['dedupeAdviceTimeouts'], 0)
        assert_equal(vdo_stats_after['biosIn']['write'], block_count * 2)
        assert_equal(vdo_stats_after['biosOut']['write'], block_count)

def register(tests):
    tests.register_batch(
        "/vdo/dedupe/",
        [
            ("dedupe0", t_dedupe0),
            ("dedupe50", t_dedupe50),
            ("dedupe75", t_dedupe75),
            ("dedupeWithOffsetAndRestart", t_dedupeWithOffsetAndRestart),
            ("dedupeWithOverwrite", t_dedupeWithOverwrite),
        ],
    )
