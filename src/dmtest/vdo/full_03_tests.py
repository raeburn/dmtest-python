"""VDO out-of-space stress test with iterative writes and trims.

Stress tests VDO behavior when running out of physical space by performing
10 iterations of parallel writes with random dedupe/compress ratios, then
verifying and randomly trimming 50% of slices to test space reclamation.
"""
import logging as log
import random
import threading

from dmtest.assertions import assert_equal
import dmtest.device_mapper.dev as dmdev
from dmtest.gendatablocks import make_block_range
import dmtest.process as process
import dmtest.tvm as tvm
import dmtest.units as units
from dmtest.vdo.utils import MB, GB, settle_devices
import dmtest.vdo.vdo_stack as vs
import dmtest.vdo.stats as stats
from dmtest.vdo.stats import get_usable_data_blocks


def rand_harmonic():
    """Compute a random number with a harmonic distribution.

    This gives us numbers that make 3:1 dedupe just as likely as 10:1 dedupe.
    And similarly for compression.

    Returns a random fraction between 0 and ~0.95 with a harmonic distribution,
    suitable for compression or dedupe fractions.
    """
    return 1 - 1 / (1 + random.random() * 19)


def t_stress_out_of_space(fix) -> None:
    """Stress test VDO behavior when running out of physical space with direct I/O.

    Creates multiple slices where only one slice worth of data can fit in
    physical space. Runs 10 iterations where each iteration writes to all
    slices in parallel (choosing randomly between primary and secondary data
    streams), verifies all data, and trims half the slices randomly.
    """
    data_dev = fix.cfg["data_dev"]

    # Create VDO with small physical size so we can fill it quickly.
    # Configuration from FullBase: slab_bits=15 (SLAB_BITS_TINY)
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
            slice_count = int(initial_stats["logicalBlocks"] / block_count)

            log.info(f"Physical Blocks: {initial_stats['physicalBlocks']}")
            log.info(f"Overhead Blocks: {initial_stats['overheadBlocksUsed']}")
            log.info(f"Logical Blocks:  {initial_stats['logicalBlocks']}")
            log.info(f"Usable Blocks:   {usable_blocks}")
            log.info(f"Block Count per slice: {block_count}")
            log.info(f"Block Size:      {block_size}")
            log.info(f"Slice Count:     {slice_count}")

            if slice_count < 5:
                raise AssertionError(f"Expected at least 5 slices, got {slice_count}")

            # Create sections. Each section gets its own slice and a primary
            # data description for its slice.
            # First 4 slices use fixed dedupe/compress ratios:
            # (1) no dedupe/no compress, (2) 75% dedupe/no compress,
            # (3) no dedupe/75% compress, (4) 75% dedupe/75% compress
            # Remaining slices use random harmonic distribution
            compress_ratios = [0, 0, 0.75, 0.75] + [rand_harmonic() for _ in range(5, slice_count + 1)]
            dedupe_ratios = [0, 0.75, 0, 0.75] + [rand_harmonic() for _ in range(5, slice_count + 1)]

            sections = []
            for number in range(1, slice_count + 1):
                offset = (number - 1) * block_count
                slice_range = make_block_range(path=vdo.path,
                                              block_size=block_size,
                                              block_count=block_count,
                                              offset=offset)
                section = {
                    'number': number,
                    'slice': slice_range,
                    'primary': {
                        'compress': compress_ratios[number - 1],
                        'dedupe': dedupe_ratios[number - 1],
                        'tag': f"data{number}",
                    }
                }
                sections.append(section)
                log.info(f"Slice {number}: offset={offset}, dedupe={section['primary']['dedupe']:.3f}, "
                        f"compress={section['primary']['compress']:.3f}")

            # Assign a secondary data description to each section, using a
            # primary data description from a random section
            for section in sections:
                section['secondary'] = random.choice(sections)['primary']
                log.info(f"Slice {section['number']} secondary: tag={section['secondary']['tag']}, "
                        f"dedupe={section['secondary']['dedupe']:.3f}, "
                        f"compress={section['secondary']['compress']:.3f}")

            # Run the test loop 10 times
            for iteration in range(1, 11):
                log.info(f"Iteration {iteration} write")

                # Track how many blocks were successfully written in each slice
                blocks_written = {}
                write_errors = {}

                # Note: gendatablocks doesn't support direct I/O yet, so we use
                # fsync=True (same approach as Full01 and Full02)
                def write_slice(section):
                    """Write to a slice until ENOSPC occurs."""
                    number = section['number']
                    slice_range = section['slice']

                    # Use secondary data for 25% of the sections
                    data = section['secondary'] if random.randint(0, 3) == 0 else section['primary']
                    tag = data['tag']
                    dedupe = data['dedupe']
                    compress = data['compress']

                    try:
                        log.info(f"Writing slice {number} with tag {tag}, "
                               f"dedupe={dedupe:.3f}, compress={compress:.3f}")
                        slice_range.write(tag=tag,
                                        dedupe=dedupe,
                                        compress=compress,
                                        fsync=True)
                        log.info(f"Slice {number} write completed without error")
                    except OSError as e:
                        # Expected to get ENOSPC or EIO when device fills
                        log.info(f"Slice {number} got expected error: {e}")
                        write_errors[number] = str(e)
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
                for section in sections:
                    thread = threading.Thread(target=write_slice, args=(section,))
                    threads.append(thread)
                    thread.start()

                # Wait for all writes to complete
                for thread in threads:
                    thread.join()

                log.info(f"Iteration {iteration} write completed")

                # Sync to ensure all pending writes are processed
                settle_devices()

                # Get statistics after filling
                filled_stats = stats.vdo_stats(vdo)
                log.info(f"Data Blocks Used: {filled_stats['dataBlocksUsed']}")

                # Verify and optionally trim each section
                log.info(f"Iteration {iteration} verify and trim")

                verify_errors = []

                def verify_and_maybe_trim_slice(section, should_trim):
                    """Verify data in a slice, and optionally trim it."""
                    number = section['number']
                    slice_range = section['slice']
                    written_count = blocks_written.get(number, 0)

                    if written_count == 0:
                        log.info(f"Slice {number} had no blocks written, skipping verification")
                        return

                    try:
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

                        # Trim 50% of the slices
                        if should_trim:
                            log.info(f"Trimming slice {number}")
                            slice_range.trim(fsync=True)
                            log.info(f"Slice {number} trim complete")
                    except Exception as e:
                        log.error(f"Slice {number} verification/trim failed: {e}")
                        verify_errors.append((number, str(e)))

                # Start all verify threads
                # Randomly decide which slices to trim (50% of them)
                threads = []
                for section in sections:
                    should_trim = random.randint(0, 1) == 1
                    thread = threading.Thread(target=verify_and_maybe_trim_slice,
                                            args=(section, should_trim))
                    threads.append(thread)
                    thread.start()

                # Wait for all verifications to complete
                for thread in threads:
                    thread.join()

                if verify_errors:
                    raise AssertionError(f"Verification failed for slices: {verify_errors}")

                log.info(f"Iteration {iteration} verify and trim completed")

                # Drop caches between iterations
                process.run("sh -c 'echo 1 > /proc/sys/vm/drop_caches'")

            log.info("All 10 iterations completed successfully")


def register(tests):
    tests.register("/vdo/full/full03", t_stress_out_of_space)
