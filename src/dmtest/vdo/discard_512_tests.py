"""VDO discard tests with 512-byte emulation.

Tests TRIM/discard operations at 512-byte granularity while VDO maintains
4KB internal blocks. Verifies that logical block accounting correctly tracks
fully vs. partially discarded blocks across all sector alignments, both
with and without compression.
"""
import logging as log
import os

from dmtest.vdo.utils import standard_vdo, BLOCK_SIZE
from dmtest.vdo import stats
from dmtest.assertions import assert_equal
import dmtest.process as process


SECTOR_SIZE = 512
SECTORS_PER_BLOCK = BLOCK_SIZE // SECTOR_SIZE  # 8 sectors per 4KB block


def construct_discard_list():
    """Construct a comprehensive list of discard extents that test all sector alignments.

    Returns:
        tuple: (discard_list, total_blocks, fully_discarded_blocks)
               discard_list: list of (sector_offset, sector_count) tuples
               total_blocks: total number of 4KB blocks written
               fully_discarded_blocks: number of 4KB blocks that will be fully discarded
    """
    discard_list = []

    # Extent lengths to test (in sectors)
    # Tests various boundary conditions: too small for full block, exactly one block,
    # slightly over one block, and multiple blocks with various alignments
    extent_lengths = [
        1, 2, SECTORS_PER_BLOCK - 2,  # Short extents
        7, 8, 9,                        # ~1 block
        2 * SECTORS_PER_BLOCK - 1, 2 * SECTORS_PER_BLOCK, 2 * SECTORS_PER_BLOCK + 1,  # ~2 blocks
        3 * SECTORS_PER_BLOCK - 1, 3 * SECTORS_PER_BLOCK, 3 * SECTORS_PER_BLOCK + 1,  # ~3 blocks
        4 * SECTORS_PER_BLOCK - 1, 4 * SECTORS_PER_BLOCK, 4 * SECTORS_PER_BLOCK + 1,  # ~4 blocks
    ]

    current_offset = 0
    spacing = 2 * SECTORS_PER_BLOCK  # 2 full blocks between extents

    # Test all possible sector alignments within a 4KB block
    # Each shift iteration continues from where the previous one left off
    for shift in range(SECTORS_PER_BLOCK):
        for extent_idx, length in enumerate(extent_lengths):
            # For the first extent in each shift, apply the shift offset
            if extent_idx == 0:
                current_offset += shift

            # Record this discard extent
            discard_list.append((current_offset, length))

            # Move to next extent position (include extent length and spacing)
            current_offset += length + spacing

    # Calculate total blocks written (round up to include partial blocks)
    total_blocks = (current_offset + SECTORS_PER_BLOCK - 1) // SECTORS_PER_BLOCK

    # Calculate how many blocks will be fully discarded
    # We need to check each 4KB block to see if all 8 sectors are covered by discards
    fully_discarded_blocks = 0
    for block_idx in range(total_blocks):
        block_start_sector = block_idx * SECTORS_PER_BLOCK
        block_end_sector = block_start_sector + SECTORS_PER_BLOCK

        # Check if all sectors in this block are covered by at least one discard extent
        sectors_discarded = [False] * SECTORS_PER_BLOCK
        for discard_offset, discard_length in discard_list:
            discard_end = discard_offset + discard_length
            # Check overlap with this block
            if discard_offset < block_end_sector and discard_end > block_start_sector:
                # Calculate which sectors in this block are discarded
                overlap_start = max(discard_offset, block_start_sector)
                overlap_end = min(discard_end, block_end_sector)
                for sector in range(overlap_start, overlap_end):
                    sector_in_block = sector - block_start_sector
                    sectors_discarded[sector_in_block] = True

        # If all 8 sectors are discarded, this block is fully discarded
        if all(sectors_discarded):
            fully_discarded_blocks += 1

    return discard_list, total_blocks, fully_discarded_blocks


def _generate_compressible_data(output_path, total_bytes, compressibility):
    """Generate compressible test data.

    Creates a file with the specified compressibility by writing a mix of
    compressible (0xFF bytes) and non-compressible (random) data.
    """
    log.info(f"Generating {total_bytes} bytes of {compressibility*100}% compressible data")

    compressible_bytes_per_sector = int(SECTOR_SIZE * compressibility)
    random_bytes_per_sector = SECTOR_SIZE - compressible_bytes_per_sector

    total_sectors = total_bytes // SECTOR_SIZE

    with open(output_path, 'wb') as f:
        for _ in range(total_sectors):
            f.write(b'\xFF' * compressible_bytes_per_sector)
            f.write(os.urandom(random_bytes_per_sector))


def _run_discard_test(fix, compressed):
    """Run a 512-byte discard test, optionally with compression enabled."""
    label = "compressed" if compressed else "uncompressed"
    vdo_opts = {"512_mode": 512, "compression": "on"} if compressed else {"512_mode": 512}

    log.info(f"Creating VDO device with 512-byte emulation ({label})")
    with standard_vdo(fix, slab_bits=17, **vdo_opts) as vdo:
        log.info(f"VDO device created: {vdo.path}")

        discard_list, total_blocks, fully_discarded_blocks = construct_discard_list()
        log.info(f"Total blocks to write: {total_blocks}")
        log.info(f"Total extents to discard: {len(discard_list)}")
        log.info(f"Fully discarded blocks expected: {fully_discarded_blocks}")

        total_bytes = total_blocks * BLOCK_SIZE
        test_data_path = f"/tmp/discard512_{label}_{process.run('date +%s')[1].strip()}"

        if compressed:
            _generate_compressible_data(test_data_path, total_bytes, compressibility=0.90)
        else:
            log.info(f"Generating {total_bytes} bytes of non-compressible test data")
            process.run(f"dd if=/dev/urandom of={test_data_path} bs={BLOCK_SIZE} count={total_blocks} 2>/dev/null")

        device_copy_path = None
        try:
            log.info(f"Writing test data to VDO device at {vdo.path}")
            process.run(f"dd if={test_data_path} of={vdo.path} bs={SECTOR_SIZE} count={total_blocks * SECTORS_PER_BLOCK} oflag=direct 2>/dev/null")
            process.run(f"sync -d {vdo.path}")

            vdo_stats = stats.vdo_stats(vdo)
            initial_blocks_used = vdo_stats['logicalBlocksUsed']
            log.info(f"Initial logical blocks used: {initial_blocks_used}")
            assert_equal(initial_blocks_used, total_blocks, "Initial blocks used should equal blocks written")

            if compressed:
                compressed_blocks = vdo_stats['packer']['compressedBlocksWritten']
                log.info(f"Compressed blocks written: {compressed_blocks}")
                if compressed_blocks == 0:
                    log.warning("No blocks were compressed - compression may not be working as expected")

            log.info(f"Executing {len(discard_list)} discard operations")
            for sector_offset, sector_count in discard_list:
                byte_offset = sector_offset * SECTOR_SIZE
                byte_length = sector_count * SECTOR_SIZE
                process.run(f"blkdiscard -o {byte_offset} -l {byte_length} {vdo.path}")

            process.run(f"sync -d {vdo.path}")

            vdo_stats = stats.vdo_stats(vdo)
            final_blocks_used = vdo_stats['logicalBlocksUsed']
            expected_blocks_used = total_blocks - fully_discarded_blocks
            log.info(f"Final logical blocks used: {final_blocks_used}")
            log.info(f"Expected logical blocks used: {expected_blocks_used}")
            assert_equal(final_blocks_used, expected_blocks_used,
                        "Logical blocks used should decrease only for fully discarded blocks")

            log.info("Preparing expected data by zeroing discarded regions")
            for sector_offset, sector_count in discard_list:
                byte_offset = sector_offset * SECTOR_SIZE
                byte_length = sector_count * SECTOR_SIZE
                process.run(f"dd if=/dev/zero of={test_data_path} bs=1 count={byte_length} seek={byte_offset} conv=notrunc 2>/dev/null")

            device_copy_path = f"/tmp/discard512_{label}_copy_{process.run('date +%s')[1].strip()}"
            log.info("Reading back VDO device contents")
            process.run(f"dd if={vdo.path} of={device_copy_path} bs={SECTOR_SIZE} count={total_blocks * SECTORS_PER_BLOCK} iflag=direct 2>/dev/null")

            log.info("Verifying data integrity")
            process.run(f"cmp {test_data_path} {device_copy_path}")
            log.info("Data integrity verified - discarded regions are zero, non-discarded data matches")

        finally:
            process.run(f"rm -f {test_data_path}", raise_on_fail=False)
            if device_copy_path:
                process.run(f"rm -f {device_copy_path}", raise_on_fail=False)


def t_discard_512(fix) -> None:
    """Test block discard with 512-byte logical block size emulation."""
    _run_discard_test(fix, compressed=False)


def t_discard_512_compressed(fix) -> None:
    """Test block discard with 512-byte emulation on compressed data."""
    _run_discard_test(fix, compressed=True)


def register(tests):
    tests.register("/vdo/discard/discard-512", t_discard_512)
    tests.register("/vdo/discard/discard-512-compressed", t_discard_512_compressed)
