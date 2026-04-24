import logging as log

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


def get_free_blocks(vdo_stats):
    """Calculate the number of free blocks."""
    return (vdo_stats["physicalBlocks"]
            - vdo_stats["overheadBlocksUsed"]
            - vdo_stats["dataBlocksUsed"])


def t_no_space(fix) -> None:
    """Test system behavior when VDO nears running out of physical space.

    Fills the VDO device completely and verifies that both VDO statistics
    and dmsetup status correctly report the device as full.
    """
    data_dev = fix.cfg["data_dev"]

    # Create VDO with small physical size so we can fill it quickly
    physical_size_mb = 3 * 1024
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
            block_count = get_usable_data_blocks(initial_stats)

            log.info(f"Block Size:      {initial_stats['blockSize']}")
            log.info(f"Physical Blocks: {initial_stats['physicalBlocks']}")
            log.info(f"Overhead Blocks: {initial_stats['overheadBlocksUsed']}")
            log.info(f"Data Blocks:     {initial_stats['dataBlocksUsed']}")
            log.info(f"Usable Blocks:   {block_count}")

            # Fill the device using direct writes
            data_range = make_block_range(path=vdo.path,
                                          block_size=initial_stats["blockSize"],
                                          block_count=block_count)

            # Write until ENOSPC
            gave_error = False
            try:
                data_range.write(tag="data", fsync=True)
            except OSError as e:
                gave_error = True
                log.info(f"Got expected error while filling device: {e}")

            # Sync to ensure all pending writes are processed
            process.run("udevadm settle")

            # Get statistics after filling
            filled_stats = stats.vdo_stats(vdo)
            free_blocks = get_free_blocks(filled_stats)

            log.info(f"Overhead Blocks: {filled_stats['overheadBlocksUsed']}")
            log.info(f"Data Blocks:     {filled_stats['dataBlocksUsed']}")
            log.info(f"Usable Blocks:   {get_usable_data_blocks(filled_stats)}")

            # Device should be full
            assert_equal(free_blocks, 0, "Device is full")

            # Now make sure dmsetup status shows it is full too
            result = process.run(f"dmsetup status {vdo.name}")
            status_output = result[1]  # Get stdout from tuple (returncode, stdout, stderr)
            log.info(f"dmsetup status: {status_output}")

            # Parse dmsetup status output
            # Format: <start> <length> <type> <dev> <mode> <recovery> <index>
            # <compress> <used> <total>
            fields = status_output.split()
            used = int(fields[8])
            total = int(fields[9])
            status_free_blocks = total - used

            log.info(f"dmsetup status: used={used}, total={total}, free={status_free_blocks}")
            assert_equal(status_free_blocks, 0, "dmsetup status says device is full")


def register(tests):
    tests.register("/vdo/full/full-warn", t_no_space)
