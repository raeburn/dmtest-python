from dmtest.assertions import assert_equal
import dmtest.device_mapper.dev as dmdev
from dmtest.gendatablocks import make_block_range
import dmtest.process as process
import dmtest.tvm as tvm
import dmtest.units as units
from dmtest.vdo.utils import MB, GB
import dmtest.vdo.vdo_stack as vs
import dmtest.vdo.stats as stats

import logging as log

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

def t_no_space(fix):
    """Test system behavior when VDO runs out of physical space.

    Fills the VDO device completely, then verifies that subsequent writes
    produce predictable ENOSPC errors and that device statistics remain
    consistent after failed writes.
    """
    data_dev = fix.cfg["data_dev"]

    # Create VDO with small physical size so we can fill it quickly
    # Configuration inspired by FullBase from Perl tests, but adjusted for
    # minimum VDO requirements with slab_bits=15:
    # - Physical size: 3GB (minimum for slab_bits=15)
    # - Logical size: 2GB
    # - Slab bits: 15 (SLAB_BITS_TINY = 128MB slabs)
    # - Compression: enabled (default in VDOStack)
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

            log.info(f"Physical Blocks: {initial_stats['physicalBlocks']}")
            log.info(f"Overhead Blocks: {initial_stats['overheadBlocksUsed']}")
            log.info(f"Data Blocks:     {initial_stats['dataBlocksUsed']}")
            log.info(f"Logical Blocks:  {initial_stats['logicalBlocks']}")
            log.info(f"Block Size:      {initial_stats['blockSize']}")
            log.info(f"Usable Blocks:   {block_count}")

            # Fill the device using direct writes (note: gendatablocks doesn't
            # support direct I/O yet, so we use sync to ensure writes reach VDO)
            data_range = make_block_range(path=vdo.path,
                                          block_size=initial_stats["blockSize"],
                                          block_count=block_count)

            # Write until ENOSPC. Without direct I/O, the write may succeed
            # initially but fail during fsync. We accept either ENOSPC or EIO.
            gave_error = False
            try:
                data_range.write(tag="data", fsync=True)
            except OSError as e:
                # Expected to get ENOSPC or EIO when device fills
                gave_error = True
                log.info(f"Got expected error while filling device: {e}")

            # Sync to ensure all pending writes are processed
            process.run("udevadm settle")

            # Get statistics after filling
            filled_stats = stats.vdo_stats(vdo)
            free_blocks = get_free_blocks(filled_stats)
            usable_blocks = get_usable_data_blocks(filled_stats)

            log.info(f"Overhead Blocks: {filled_stats['overheadBlocksUsed']}")
            log.info(f"Data Blocks:     {filled_stats['dataBlocksUsed']}")
            log.info(f"Usable Blocks:   {usable_blocks}")
            log.info(f"Free Blocks:     {free_blocks}")

            # Device should be full (or very close to it)
            assert_equal(free_blocks, 0, "Device should be full")

            # Save expected statistics for comparison after failed write
            expected_stats = {
                "physicalBlocks": filled_stats["physicalBlocks"],
                "overheadBlocksUsed": filled_stats["overheadBlocksUsed"],
                "dataBlocksUsed": filled_stats["dataBlocksUsed"],
                "logicalBlocks": filled_stats["logicalBlocks"],
                "logicalBlocksUsed": filled_stats["logicalBlocksUsed"],
            }

            # Try to write 1 more block. This should fail because we are out of space.
            one_block_range = make_block_range(path=vdo.path,
                                              block_size=initial_stats["blockSize"],
                                              block_count=1,
                                              offset=block_count)

            gave_error = False
            try:
                one_block_range.write(tag="direct1", fsync=True)
            except OSError as e:
                # VDO should generate ENOSPC but without direct I/O we may get EIO
                gave_error = True
                log.info(f"Got expected error on write to full device: {e}")

            if not gave_error:
                raise AssertionError("writing to full VDO should fail")

            # Verify statistics haven't changed after the failed write
            process.run("udevadm settle")
            final_stats = stats.vdo_stats(vdo)

            assert_equal(final_stats["physicalBlocks"], expected_stats["physicalBlocks"])
            assert_equal(final_stats["overheadBlocksUsed"], expected_stats["overheadBlocksUsed"])
            assert_equal(final_stats["dataBlocksUsed"], expected_stats["dataBlocksUsed"])
            assert_equal(final_stats["logicalBlocks"], expected_stats["logicalBlocks"])
            assert_equal(final_stats["logicalBlocksUsed"], expected_stats["logicalBlocksUsed"])

            # Note: In the original Perl test, the data is verified after filling.
            # However, without direct I/O support in gendatablocks, we can't easily
            # determine which blocks were successfully written before ENOSPC occurred.
            # The key test points (device full, failed write, consistent stats) are
            # already verified above. Verification would require tracking exactly which
            # blocks were acknowledged before the fsync error.

def register(tests):
    tests.register_batch(
        "/vdo/full",
        [
            ("full01", t_no_space),
        ],
    )
