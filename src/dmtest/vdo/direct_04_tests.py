"""VDO in-flight deduplication test.

Tests VDO's ability to deduplicate data that is in flight simultaneously
by writing an alternating pattern of two unique 4K blocks and verifying
correct deduplication and compression packing behavior.
"""
import logging as log
import os
import tempfile
from math import ceil

from dmtest.assertions import assert_equal
from dmtest.process import run
from dmtest.vdo import stats
from dmtest.vdo.utils import standard_vdo, SLAB_BITS_SMALL


def t_same_blocks(fix) -> None:
    """Test deduplication of in-flight data with alternating block pattern.

    Writes data consisting of alternating copies of two 4K blocks to test
    that VDO can deduplicate data that are in flight simultaneously.
    """
    block_size = 4096
    target_blocks = 1000

    with standard_vdo(fix, slab_bits=SLAB_BITS_SMALL) as vdo:
        with tempfile.NamedTemporaryFile(delete=False) as data_file:
            data_path = data_file.name

        try:
            # Generate two random 4KB blocks
            log.info(f"Generating initial 2 blocks of random data")
            run(f"dd if=/dev/urandom of={data_path} bs={block_size} count=2")

            # Create alternating pattern by doubling: 2 -> 4 -> 8 -> 16 ... until >= 1000
            # Final block_count will be the first power of 2 >= target_blocks
            block_count = 2
            while block_count < target_blocks:
                log.info(f"Doubling blocks: {block_count} -> {block_count * 2}")
                run(f"dd if={data_path} of={data_path} bs={block_size} "
                    f"count={block_count} seek={block_count} conv=notrunc")
                block_count *= 2

            # Write the alternating blocks to VDO with sync
            log.info(f"Writing {block_count} alternating blocks to VDO")
            run(f"dd if={data_path} of={vdo.path} bs={block_size} "
                f"count={block_count} conv=fdatasync")

            # Drop caches
            log.info("Dropping caches")
            with open("/proc/sys/vm/drop_caches", "w") as f:
                f.write("1\n")

            # Read back and verify
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_path = temp_file.name

            try:
                log.info(f"Reading back {block_count} blocks and verifying")
                run(f"dd if={vdo.path} of={temp_path} bs={block_size} "
                    f"count={block_count}")
                run(f"cmp {data_path} {temp_path}")

                # Check statistics
                vdo_stats = stats.vdo_stats(vdo)

                # Expected blocks used: 2 unique blocks, packed into bins
                # Packer can fit 254 blocks per bin (for 4KB blocks)
                # So: 2 * ceil((block_count/2) / 254)
                expected_blocks_used = 2 * ceil((block_count / 2) / 254)

                log.info(f"Expected data blocks used: {expected_blocks_used}")
                log.info(f"Actual data blocks used: {vdo_stats['dataBlocksUsed']}")

                assert_equal(expected_blocks_used, vdo_stats['dataBlocksUsed'],
                           f"Data blocks used should be {expected_blocks_used}")

                # Verify dedupe statistics
                stale_advice = vdo_stats['hashLock']['dedupeAdviceStale']
                concurrent_collisions = vdo_stats['hashLock']['concurrentHashCollisions']
                total_stale = stale_advice + concurrent_collisions

                assert_equal(0, total_stale, "Dedupe advice stale should be zero")
                assert_equal(0, vdo_stats['dedupeAdviceTimeouts'],
                           "Dedupe advice timeouts should be zero")

                # Check I/O counts
                bios_in_write = vdo_stats['biosIn']['write']
                bios_out_write = vdo_stats['biosOut']['write']

                log.info(f"Inbound writes: {bios_in_write}, expected: {block_count}")
                log.info(f"Outbound writes: {bios_out_write}, expected: {expected_blocks_used}")

                assert_equal(block_count, bios_in_write,
                           "Inbound writes should be block count")
                assert_equal(expected_blocks_used, bios_out_write,
                           "Outbound writes should be unique blocks")

            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        finally:
            if os.path.exists(data_path):
                os.unlink(data_path)


def register(tests):
    tests.register("/vdo/direct/same-blocks", t_same_blocks)
