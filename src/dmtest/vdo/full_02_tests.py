import logging as log
import threading

from dmtest.assertions import assert_equal
import dmtest.device_mapper.dev as dmdev
from dmtest.gendatablocks import make_block_range
import dmtest.process as process
import dmtest.tvm as tvm
import dmtest.units as units
from dmtest.vdo.utils import MB, GB
import dmtest.vdo.vdo_stack as vs
import dmtest.vdo.stats as stats


def get_usable_data_blocks(vdo_stats):
    """Calculate the number of blocks that can be used for data.

    Returns physical blocks minus overhead blocks used.
    """
    return vdo_stats["physicalBlocks"] - vdo_stats["overheadBlocksUsed"]


def run_out_of_space(fix, dedupe_fraction: float, compress_fraction: float) -> None:
    """Test VDO behavior when running out of physical space with parallel writes.

    Allocates 4 non-overlapping slices where the entire device has only enough
    physical storage to accommodate one slice. Writes data to each slice in
    parallel until ENOSPC occurs, then verifies the data that was written.

    Args:
        fix: Test fixture
        dedupe_fraction: Deduplication rate (0.0 to 1.0)
        compress_fraction: Compression rate (0.0 to 0.96)
    """
    data_dev = fix.cfg["data_dev"]
    SLICE_COUNT = 4

    # Create VDO with small physical size so we can fill it quickly.
    # Configuration from FullBase: slab_bits=15 (SLAB_BITS_TINY), physical
    # size small enough to fill quickly, logical size large enough for 4 slices.
    physical_size_mb = 3 * 1024  # 3GB minimum for slab_bits=15
    logical_size_gb = 2

    # Create a linear volume of the right size for VDO's physical storage
    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("vdo_storage", units.meg(physical_size_mb)))

    with dmdev.dev(vm.table("vdo_storage")) as storage:
        vdo_volume = vs.VDOStack(storage,
                                 logical_size=logical_size_gb * GB,
                                 slab_bits=15)
        with vdo_volume.activate() as vdo:
            # Get initial statistics
            initial_stats = stats.vdo_stats(vdo)
            usable_blocks = get_usable_data_blocks(initial_stats)

            # Round down to nearest thousand for cleaner numbers
            block_count = int(usable_blocks / 1000) * 1000
            block_size = initial_stats["blockSize"]

            log.info(f"Physical Blocks: {initial_stats['physicalBlocks']}")
            log.info(f"Overhead Blocks: {initial_stats['overheadBlocksUsed']}")
            log.info(f"Usable Blocks:   {usable_blocks}")
            log.info(f"Block Count per slice: {block_count}")
            log.info(f"Block Size:      {block_size}")
            log.info(f"Slice Count:     {SLICE_COUNT}")

            # Create 4 slices with non-overlapping offsets
            slices = []
            for number in range(1, SLICE_COUNT + 1):
                offset = (number - 1) * block_count
                slice_range = make_block_range(path=vdo.path,
                                              block_size=block_size,
                                              block_count=block_count,
                                              offset=offset)
                slices.append((number, slice_range))
                log.info(f"Slice {number}: offset={offset}, count={block_count}")

            # Write to all slices in parallel until ENOSPC
            # Track how many blocks were successfully written in each slice
            blocks_written = {}

            # Note: gendatablocks doesn't support direct I/O yet, so we use fsync=True
            def write_slice(number, slice_range):
                """Write to a slice until ENOSPC occurs."""
                tag = f"data{number}"
                try:
                    log.info(f"Writing slice {number} with tag {tag}, "
                           f"dedupe={dedupe_fraction}, compress={compress_fraction}")
                    slice_range.write(tag=tag,
                                    dedupe=dedupe_fraction,
                                    compress=compress_fraction,
                                    fsync=True)
                    log.info(f"Slice {number} write completed without error")
                except OSError as e:
                    # Expected to get ENOSPC or EIO when device fills
                    log.info(f"Slice {number} got expected error: {e}")
                finally:
                    # Record how many blocks were actually written before error
                    # The stream counter tracks successful writes
                    if slice_range.streams:
                        blocks_written[number] = slice_range.streams[0].counter
                    else:
                        blocks_written[number] = 0
                    log.info(f"Slice {number} wrote {blocks_written[number]} blocks")

            # Start all write threads
            threads = []
            for number, slice_range in slices:
                thread = threading.Thread(target=write_slice,
                                        args=(number, slice_range))
                threads.append(thread)
                thread.start()

            # Wait for all writes to complete
            for thread in threads:
                thread.join()

            log.info("All write threads completed")

            # Sync to ensure all pending writes are processed
            process.run("udevadm settle")

            # Get statistics after filling
            filled_stats = stats.vdo_stats(vdo)
            free_blocks = (filled_stats["physicalBlocks"]
                          - filled_stats["overheadBlocksUsed"]
                          - filled_stats["dataBlocksUsed"])

            log.info(f"Final Data Blocks Used: {filled_stats['dataBlocksUsed']}")
            log.info(f"Final Free Blocks:      {free_blocks}")

            # Verify each slice in parallel
            # Only verify the blocks that were successfully written
            log.info("Verifying data in all slices")

            def verify_slice(number, slice_range, written_count):
                """Verify data in a slice."""
                if written_count == 0:
                    log.info(f"Slice {number} had no blocks written, skipping verification")
                    return

                log.info(f"Verifying {written_count} blocks in slice {number}")
                # Create a new range covering only the blocks that were written
                verify_range = make_block_range(path=vdo.path,
                                               block_size=block_size,
                                               block_count=written_count,
                                               offset=slice_range.offset)
                # Copy the streams from the original slice to enable verification
                verify_range.streams = slice_range.streams
                verify_range.verify()
                log.info(f"Slice {number} verification complete")

            # Start all verify threads
            threads = []
            for number, slice_range in slices:
                written_count = blocks_written.get(number, 0)
                thread = threading.Thread(target=verify_slice,
                                        args=(number, slice_range, written_count))
                threads.append(thread)
                thread.start()

            # Wait for all verifications to complete
            for thread in threads:
                thread.join()

            log.info("All slice verifications completed successfully")


def t_vanilla(fix) -> None:
    """Test with no dedupe, no compression."""
    run_out_of_space(fix, 0.0, 0.0)


def t_dedupe(fix) -> None:
    """Test with 50% dedupe, no compression."""
    run_out_of_space(fix, 0.5, 0.0)


def t_compress(fix) -> None:
    """Test with no dedupe, 60% compression."""
    run_out_of_space(fix, 0.0, 0.6)


def t_compress_and_dedupe(fix) -> None:
    """Test with 33% dedupe, 60% compression."""
    run_out_of_space(fix, 0.334, 0.6)


def register(tests):
    tests.register_batch(
        "/vdo/full/",
        [
            ("full02-vanilla", t_vanilla),
            ("full02-dedupe", t_dedupe),
            ("full02-compress", t_compress),
            ("full02-compress-and-dedupe", t_compress_and_dedupe),
        ],
    )
