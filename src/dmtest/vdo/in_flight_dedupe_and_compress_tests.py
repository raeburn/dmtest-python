"""VDO test for in-flight deduplication with compression.

Tests that large numbers of blocks with the same data are written correctly
when multiple copies of the same block are written simultaneously. This tests
VDO's concurrent deduplication capability (VDO-2711).
"""

import logging as log
import tempfile

from dmtest.process import run
from dmtest.vdo.stats import vdo_stats
from dmtest.vdo.utils import BLOCK_SIZE, MB, standard_vdo


def t_in_flight_dedupe_with_compression(fix) -> None:
    """Test concurrent deduplication of identical blocks written simultaneously."""

    with standard_vdo(fix, compression="on") as vdo:
        log.info("Generating unique data blocks")
        data_size = 256 * MB

        with tempfile.NamedTemporaryFile(delete=False) as unique_file:
            unique_file_path = unique_file.name
            # Generate 256 MB of random data
            run(f"dd if=/dev/urandom of={unique_file_path} bs=1M count=256 "
                f"conv=fdatasync")

        try:
            with tempfile.NamedTemporaryFile(delete=False) as input_file:
                input_file_path = input_file.name

                log.info("Creating input file with each 4K block repeated 6 times")
                # Read each 4K block and write it 6 times consecutively
                with open(unique_file_path, 'rb') as src:
                    with open(input_file_path, 'wb') as dst:
                        while True:
                            block = src.read(BLOCK_SIZE)
                            if not block:
                                break
                            # Write the same block 6 times
                            for _ in range(6):
                                dst.write(block)
                        dst.flush()

            # Write the data to VDO
            total_size = 6 * data_size
            block_size = 1 * MB
            blocks = total_size // block_size

            log.info(f"Writing {blocks} blocks of {block_size} bytes to VDO")
            run(f"dd if={input_file_path} of={vdo.path} bs={block_size} "
                f"count={blocks} conv=fdatasync oflag=direct")

            # Read back and verify
            with tempfile.NamedTemporaryFile(delete=False) as output_file:
                output_file_path = output_file.name

                log.info("Reading back data from VDO")
                run(f"dd if={vdo.path} of={output_file_path} bs={block_size} "
                    f"count={blocks}")

                # Compare input and output
                log.info("Verifying data integrity")
                run(f"cmp {input_file_path} {output_file_path}")

            # Check statistics
            stats = vdo_stats(vdo)
            data_blocks_used = stats['dataBlocksUsed']
            logical_blocks_used = stats['logicalBlocksUsed']
            dedupe_advice_valid = stats['hashLock']['dedupeAdviceValid']
            bios_in_write = stats['biosIn']['write']

            # Calculate space savings percentage
            if logical_blocks_used > 0:
                saving_percent = 100 * (1 - data_blocks_used / logical_blocks_used)
            else:
                saving_percent = 0

            log.info(f"Data blocks used: {data_blocks_used}")
            log.info(f"Logical blocks used: {logical_blocks_used}")
            log.info(f"Saving percent: {saving_percent:.2f}%")
            log.info(f"Dedupe advice valid: {dedupe_advice_valid}")
            log.info(f"Bios in write: {bios_in_write}")

            # We should achieve at least 82% space savings
            assert saving_percent >= 82, \
                f"Expected at least 82% space savings, got {saving_percent:.2f}%"

            # With concurrent deduplication, very few index queries should be needed
            # (most blocks should be deduplicated without consulting the index)
            max_index_queries = bios_in_write // 100
            assert dedupe_advice_valid <= max_index_queries, \
                f"Expected at most {max_index_queries} index queries, " \
                f"got {dedupe_advice_valid}"

        finally:
            # Clean up temporary files
            run(f"rm -f {unique_file_path} {input_file_path} {output_file_path}",
                raise_on_fail=False)


def register(tests):
    tests.register("/vdo/in-flight-dedupe-and-compress/test",
                   t_in_flight_dedupe_with_compression)
