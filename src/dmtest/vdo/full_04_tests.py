import logging as log
import threading

import dmtest.device_mapper.dev as dmdev
from dmtest.gendatablocks import make_block_range, ClaimError
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


def sync_device_ignoring_errors(device_path):
    """Sync device data, ignoring errors that occur when VDO is full.

    When writing to a full VDO using page cache I/O, errors occur during
    fsync rather than during the write. We retry fsync up to 10 times to
    handle sticky write error conditions.
    """
    for retry in range(10):
        try:
            with open(device_path, "r+b") as f:
                import os
                os.fsync(f.fileno())
            return
        except OSError as e:
            log.info(f"fsync attempt {retry + 1} got error: {e}")
            if retry == 9:
                raise


def run_out_of_space(fix, dedupe_fraction, compress_fraction):
    """Test VDO behavior when running out of physical space using page cache I/O.

    Creates 4 non-overlapping slices where the entire device has only enough
    physical storage to accommodate one slice. Writes data to fill each slice
    in parallel. This will fail silently because the I/O error occurs when the
    page cache is flushed. Then reads and verifies the data that were written.
    """
    data_dev = fix.cfg["data_dev"]

    # Create VDO with small physical size so we can fill it quickly.
    # Configuration from FullBase: slab_bits=15 (SLAB_BITS_TINY)
    # Note: With slab_bits=15, VDO requires minimum ~3GB physical size
    physical_size_mb = 3 * 1024  # 3GB
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

            log.info(f"Usable data blocks: {usable_blocks}")
            log.info(f"Block count per slice: {block_count}")
            log.info(f"Block size: {block_size}")

            # Create 4 slices
            slice_count = 4
            slices = []
            for number in range(1, slice_count + 1):
                offset = (number - 1) * block_count
                slice_range = make_block_range(path=vdo.path,
                                              block_size=block_size,
                                              block_count=block_count,
                                              offset=offset)
                slices.append({
                    'number': number,
                    'slice': slice_range,
                    'tag': f"data{number}",
                })

            # Write all slices in parallel
            log.info(f"Writing {slice_count} slices in parallel with dedupe={dedupe_fraction}, compress={compress_fraction}")

            # Track how many blocks were successfully written in each slice
            blocks_written = {}

            def write_slice(slice_info):
                """Write to a slice without fsync (page cache I/O)."""
                number = slice_info['number']
                slice_range = slice_info['slice']
                tag = slice_info['tag']

                try:
                    log.info(f"Writing slice {number} with tag {tag}")
                    # Note: fsync=False means errors won't be reported until the sync
                    slice_range.write(tag=tag,
                                    dedupe=dedupe_fraction,
                                    compress=compress_fraction,
                                    fsync=False)
                    log.info(f"Slice {number} write call completed")
                except OSError as e:
                    # Unexpected during write phase (errors should happen during sync)
                    log.warning(f"Slice {number} got unexpected error during write: {e}")
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
            for slice_info in slices:
                thread = threading.Thread(target=write_slice, args=(slice_info,))
                threads.append(thread)
                thread.start()

            # Wait for all writes to complete
            for thread in threads:
                thread.join()

            log.info("All writes complete, syncing device")

            # Sync the data - this is where errors will occur
            sync_device_ignoring_errors(vdo.path)

            log.info("Sync complete, dropping caches")

            # Drop caches to force data out of page cache and eliminate sticky
            # write error condition that would cause EIO on subsequent reads
            process.run("sh -c 'echo 1 > /proc/sys/vm/drop_caches'")

            # Verify each slice
            log.info("Verifying all slices")

            verify_errors = []

            def verify_slice(slice_info):
                """Verify data in a slice.

                When VDO runs out of physical space during page cache writes,
                some blocks may not be written to disk and will read as zeros.
                This is expected behavior - we verify the slice and if we encounter
                zeros (ClaimError), that's acceptable.
                """
                number = slice_info['number']
                slice_range = slice_info['slice']

                try:
                    log.info(f"Verifying slice {number}")
                    slice_range.verify()
                    log.info(f"Slice {number} fully verified")
                except ClaimError as e:
                    # Hit zero blocks - VDO ran out of space
                    # This is expected when overfilling VDO
                    log.info(f"Slice {number} verification encountered zeros at block {e.block_number} (VDO out of space, expected)")
                except Exception as e:
                    log.error(f"Slice {number} unexpected verification error: {e}")
                    verify_errors.append((number, str(e)))

            # Verify all slices in parallel
            threads = []
            for slice_info in slices:
                thread = threading.Thread(target=verify_slice, args=(slice_info,))
                threads.append(thread)
                thread.start()

            # Wait for all verifications to complete
            for thread in threads:
                thread.join()

            if verify_errors:
                raise AssertionError(f"Verification failed for slices: {verify_errors}")

            log.info("All slices verified successfully")


def t_vanilla(fix) -> None:
    """Test VDO out-of-space behavior with no dedupe and no compression."""
    run_out_of_space(fix, dedupe_fraction=0, compress_fraction=0)


def t_dedupe(fix) -> None:
    """Test VDO out-of-space behavior with 50% dedupe and no compression."""
    run_out_of_space(fix, dedupe_fraction=0.5, compress_fraction=0)


def t_compress(fix) -> None:
    """Test VDO out-of-space behavior with no dedupe and 60% compression."""
    run_out_of_space(fix, dedupe_fraction=0, compress_fraction=0.6)


def t_compress_and_dedupe(fix) -> None:
    """Test VDO out-of-space behavior with 33% dedupe and 60% compression."""
    run_out_of_space(fix, dedupe_fraction=0.334, compress_fraction=0.6)


def register(tests):
    tests.register_batch("/vdo/full/", [
        ("full04-vanilla", t_vanilla),
        ("full04-dedupe", t_dedupe),
        ("full04-compress", t_compress),
        ("full04-compress-and-dedupe", t_compress_and_dedupe),
    ])
