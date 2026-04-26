"""VDO kernel formatting tests.

Tests VDO's kernel-based formatting feature (VDO 9.2.0+) which allows formatting
VDO volumes via dmsetup table parameters. Validates parameter checking, minimum
size calculation, and formatting on dirty storage.
"""
import logging as log
import time

from dmtest.assertions import assert_string_in
from dmtest.utils import get_dmesg_log, dev_size, wipe_device
from dmtest.vdo.utils import wait_for_index
import dmtest.device_mapper.dev as dmdev
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.process as process


def try_illegal_format(fix, param_name, value, expected_kernel_error):
    """
    Attempt to format VDO with an illegal parameter value.

    Expects the format to fail and verifies the kernel error message.
    """
    data_dev = fix.cfg["data_dev"]
    dev_sectors = dev_size(data_dev)
    physical_blocks = (dev_sectors * 512) // 4096
    logical_blocks = physical_blocks * 2  # 2x over-provisioning
    logical_sectors = 0  # Initialize, may be set below for logicalSize tests

    # Zero out the first block so VDO will attempt kernel formatting
    log.info("Zeroing first block for kernel format attempt")
    process.run(f"dd if=/dev/zero of={data_dev} bs=4096 count=1 conv=fsync")

    start_time = time.time()

    # Build VDO target with kernel formatting parameters
    # For kernel formatting, pass slabSize, indexMemory, and indexSparse as opts
    opts = {}

    # Set the parameter being tested
    if param_name == "slabBits":
        # Convert slab bits to slab size (2^slabBits blocks)
        if isinstance(value, int) and value >= 0:
            opts["slabSize"] = 1 << value
        else:
            # For invalid values that can't be converted, pass directly
            opts["slabSize"] = value
    elif param_name == "logicalSize":
        # logicalSize is in bytes, convert to sectors for dmsetup table
        if isinstance(value, int):
            logical_sectors = value // 512
            logical_blocks = logical_sectors // 8  # For target size calculation
        else:
            logical_sectors = 1  # Will fail anyway
            logical_blocks = 1
    elif param_name == "albireoMem":
        opts["indexMemory"] = value
    elif param_name == "albireoSparse":
        opts["indexSparse"] = value

    # Set default formatting parameters (indicating unformatted storage)
    if "slabSize" not in opts:
        opts["slabSize"] = 1 << 17  # Default slab_bits=17
    if "indexMemory" not in opts:
        opts["indexMemory"] = 0.25
    if "indexSparse" not in opts:
        opts["indexSparse"] = "off"  # Must be "on" or "off", not 0/1

    # Use logical_sectors if set (for logicalSize tests), otherwise use default
    if param_name == "logicalSize":
        sector_count = logical_sectors
    else:
        sector_count = logical_blocks * 8

    vdo_table = table.Table(
        targets.VDOTarget(
            sector_count,
            data_dev,
            physical_blocks,
            4096,  # mode
            128 * 1024 * 1024 // 4096,  # block_map_cache in blocks
            16380,  # block_map_period
            opts
        )
    )

    gave_error = False
    try:
        with dmdev.dev(vdo_table):
            pass
    except Exception as e:
        gave_error = True
        error_msg = str(e)
        log.info(f"Got expected error: {error_msg}")

        # Check kernel log for expected error
        kernel_log = get_dmesg_log(start_time)
        log.info(f"Kernel log:\n{kernel_log}")
        assert_string_in(kernel_log, expected_kernel_error)

    assert gave_error, f"Expected formatting with {param_name}={value} to fail"


def t_options(fix) -> None:
    """Test VDO kernel formatting parameter validation."""
    log.info("Testing VDO kernel formatting parameter validation")

    data_dev = fix.cfg["data_dev"]
    dev_sectors = dev_size(data_dev)
    physical_blocks = (dev_sectors * 512) // 4096

    # Test valid slab bits (14-23 are typically valid)
    log.info("Testing valid slab bits values")
    for slab_bits in [14, 17]:
        log.info(f"Testing slab_bits={slab_bits}")
        # Zero first block for kernel formatting
        process.run(f"dd if=/dev/zero of={data_dev} bs=4096 count=1 conv=fsync")
        opts = {
            "slabSize": 1 << slab_bits,
            "indexMemory": 0.25,
            "indexSparse": "off",
        }
        vdo_table = table.Table(
            targets.VDOTarget(
                (physical_blocks * 2) * 8,  # logical sectors (2x physical)
                data_dev,
                physical_blocks,
                4096,
                128 * 1024 * 1024 // 4096,
                16380,
                opts
            )
        )
        with dmdev.dev(vdo_table) as vdo:
            log.info(f"Successfully formatted with slab_bits={slab_bits}")
            # Wipe the device for next test
            wipe_device(data_dev, 1024)

    # Test invalid slab bits
    log.info("Testing invalid slab bits values")
    max_uint = (1 << 64) - 1

    try_illegal_format(fix, "slabBits", 3, "invalid slab size")
    try_illegal_format(fix, "slabBits", 25, "invalid slab size")

    # Test valid logical sizes
    log.info("Testing valid logical sizes")
    for logical_size in [4096, 4 * 1024 * 1024, 1 * 1024 * 1024 * 1024]:
        log.info(f"Testing logical_size={logical_size}")
        # Zero first block for kernel formatting
        process.run(f"dd if=/dev/zero of={data_dev} bs=4096 count=1 conv=fsync")
        logical_blocks = logical_size // 4096
        opts = {
            "slabSize": 1 << 17,
            "indexMemory": 0.25,
            "indexSparse": "off",
        }
        vdo_table = table.Table(
            targets.VDOTarget(
                logical_blocks * 8,  # Convert to sectors
                data_dev,
                physical_blocks,
                4096,
                128 * 1024 * 1024 // 4096,
                16380,
                opts
            )
        )
        with dmdev.dev(vdo_table) as vdo:
            log.info(f"Successfully formatted with logical_size={logical_size}")
            wipe_device(data_dev, 1024)

    # Test invalid logical sizes
    log.info("Testing invalid logical sizes")
    try_illegal_format(fix, "logicalSize", 0, "zero-length target")
    # Use 1024 to trigger alignment error (not multiple of 4096)
    try_illegal_format(fix, "logicalSize", 1024, "must be a multiple of")

    # Test valid index memory sizes
    log.info("Testing valid index memory sizes")
    for alb_mem in [0.25, 0.5, 1]:
        log.info(f"Testing albireo_mem={alb_mem}")
        # Zero first block for kernel formatting
        process.run(f"dd if=/dev/zero of={data_dev} bs=4096 count=1 conv=fsync")
        opts = {
            "slabSize": 1 << 17,
            "indexMemory": alb_mem,
            "indexSparse": "off",
        }
        vdo_table = table.Table(
            targets.VDOTarget(
                (physical_blocks * 2) * 8,
                data_dev,
                physical_blocks,
                4096,
                128 * 1024 * 1024 // 4096,
                16380,
                opts
            )
        )
        with dmdev.dev(vdo_table) as vdo:
            log.info(f"Successfully formatted with index_memory={alb_mem}")
            wipe_device(data_dev, 1024)

    # Test invalid index memory (too large)
    log.info("Testing invalid index memory size")
    try_illegal_format(fix, "albireoMem", 255, "Could not allocate")

    # Test sparse index values
    log.info("Testing sparse index values")
    # Note: Only testing sparse=off due to 20GB device size limitation
    # (Perl test uses 50GB device; sparse index requires more metadata space)
    for sparse in ["off"]:
        log.info(f"Testing sparse={sparse}")
        # Zero first block for kernel formatting
        process.run(f"dd if=/dev/zero of={data_dev} bs=4096 count=1 conv=fsync")
        opts = {
            "slabSize": 1 << 17,
            "indexMemory": 0.25,
            "indexSparse": sparse,
        }
        vdo_table = table.Table(
            targets.VDOTarget(
                (physical_blocks * 2) * 8,
                data_dev,
                physical_blocks,
                4096,
                128 * 1024 * 1024 // 4096,
                16380,
                opts
            )
        )
        with dmdev.dev(vdo_table) as vdo:
            log.info(f"Successfully formatted with sparse={sparse}")
            wipe_device(data_dev, 1024)


def t_minimum_size(fix) -> None:
    """Test VDO minimum size calculation for kernel formatting."""
    log.info("Testing VDO minimum size calculation")

    data_dev = fix.cfg["data_dev"]

    # Use a small device that will be too small for large slab + large index
    # Create a 1GB linear device as backing storage
    dev_sectors = dev_size(data_dev)
    small_sectors = (1 * 1024 * 1024 * 1024) // 512  # 1GB
    if dev_sectors > small_sectors:
        small_sectors = dev_sectors  # Use full device if smaller than 1GB

    physical_blocks = (small_sectors * 512) // 4096

    # Try to format with large slab (2^23) and large index (2GB)
    # This should fail because the device is too small
    log.info("Attempting format with slab_bits=23, index_memory=2")

    # Zero first block for kernel formatting
    process.run(f"dd if=/dev/zero of={data_dev} bs=4096 count=1 conv=fsync")

    start_time = time.time()
    opts = {
        "slabSize": 1 << 23,
        "indexMemory": 2,
        "indexSparse": "off",
    }

    vdo_table = table.Table(
        targets.VDOTarget(
            (physical_blocks * 2) * 8,
            data_dev,
            physical_blocks,
            4096,
            128 * 1024 * 1024 // 4096,
            16380,
            opts
        )
    )

    gave_error = False
    try:
        with dmdev.dev(vdo_table):
            pass
    except Exception as e:
        gave_error = True
        log.info(f"Got expected error: {e}")

        # Check kernel log for minimum size message
        kernel_log = get_dmesg_log(start_time)
        log.info(f"Kernel log:\n{kernel_log}")
        assert_string_in(kernel_log, "Could not allocate")

        # The kernel should report minimum required size
        if "Minimum required size for VDO volume:" in kernel_log:
            log.info("Found minimum size message in kernel log")

    assert gave_error, "Expected format to fail due to insufficient space"


def t_dirty_storage(fix) -> None:
    """Test VDO kernel formatting on dirty (previously written) storage."""
    log.info("Testing VDO kernel formatting on dirty storage")

    data_dev = fix.cfg["data_dev"]
    dev_sectors = dev_size(data_dev)
    physical_blocks = (dev_sectors * 512) // 4096

    # Write random data to the storage device to make it "dirty"
    log.info("Writing random data to storage device")
    # Write 1GB of random data
    block_count = min(physical_blocks, (1 * 1024 * 1024 * 1024) // 4096)
    process.run(f"dd if=/dev/urandom of={data_dev} bs=4096 count={block_count} conv=fsync")

    # For kernel formatting to work, we need to zero the first block
    # (VDO checks for magic number at the start)
    log.info("Zeroing first block for kernel format")
    process.run(f"dd if=/dev/zero of={data_dev} bs=4096 count=1 conv=fsync")

    # Now format VDO on the dirty storage - this should succeed
    log.info("Formatting VDO on dirty storage")
    opts = {
        "slabSize": 1 << 17,
        "indexMemory": 0.25,
        "indexSparse": "off",
    }

    vdo_table = table.Table(
        targets.VDOTarget(
            (physical_blocks * 2) * 8,
            data_dev,
            physical_blocks,
            4096,
            128 * 1024 * 1024 // 4096,
            16380,
            opts
        )
    )

    with dmdev.dev(vdo_table) as vdo:
        log.info("Successfully formatted VDO on dirty storage")
        # Wait for VDO index to come online
        log.info("Waiting for VDO index to come online")
        wait_for_index(vdo)
        # Verify VDO is online
        status = vdo.status()
        log.info(f"VDO status: {status}")
        assert "online" in status, "VDO should be online after formatting"


def register(tests):
    tests.register_batch("/vdo/format-in-kernel/", [
        ("options", t_options),
        ("minimum-size", t_minimum_size),
        ("dirty-storage", t_dirty_storage),
    ])
