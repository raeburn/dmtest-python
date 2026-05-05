"""
VDO Logical Growth Test - GrowLogical03

Tests VDO's auto-grow-logical feature which allows VDO to automatically expand
its logical size when the device table specifies a larger size than what is
stored in the VDO superblock.
"""
import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import standard_vdo, GB
from dmtest.vdo.stats import vdo_stats
from dmtest.vdo.vdo_stack import VDOStack
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.process as process


def write_and_verify_at_end(vdo, logical_size, tag: str) -> None:
    """Write and verify data at the end of the logical space.

    Args:
        vdo: VDO device
        logical_size: Current logical size in bytes
        tag: Data tag for generating unique data patterns
    """
    logical_blocks = logical_size // 4096

    # Write 20 blocks near the end of the device (offset at logicalBlocks - 21)
    offset = (logical_blocks - 21) * 4096
    size = 20 * 4096

    log.info(f"Writing {size} bytes at offset {offset} with tag '{tag}'")

    # Generate test data and write it
    test_data = (tag * ((size // len(tag)) + 1))[:size].encode('utf-8')

    with open(vdo.path, 'r+b') as f:
        f.seek(offset)
        f.write(test_data)
        f.flush()
        os.fsync(f.fileno())

    # Verify the data
    log.info(f"Verifying data with tag '{tag}'")
    with open(vdo.path, 'rb') as f:
        f.seek(offset)
        read_data = f.read(size)

    assert_equal(read_data, test_data, f"Data mismatch for tag '{tag}'")
    log.info(f"Data verification successful for tag '{tag}'")


def t_auto_grow_logical(fix) -> None:
    """Test VDO auto-grow-logical feature by restarting with larger table size.

    Tests that VDO correctly detects and applies a larger logical size when the
    device table specifies a larger size than what is stored in the VDO superblock.
    Performs this auto-grow operation twice in sequence to verify the feature works
    reliably across multiple expansions.
    """
    data_dev = fix.cfg["data_dev"]
    initial_logical_size = 5 * GB

    # Get physical configuration
    physical_size_result = process.run(f"blockdev --getsize64 {data_dev}")
    physical_size = int(physical_size_result[1].strip())

    # Create VDO device with 5GB logical size
    log.info(f"Creating VDO with initial logical size of {initial_logical_size} bytes (5GB)")
    stack = VDOStack(
        data_dev,
        format=True,
        logical_size=initial_logical_size,
        physical_size=physical_size,
        albireo_mem=0.25
    )
    vdo = stack.activate()

    try:
        # First auto-grow: 5GB → 10GB
        log.info("Testing first auto-grow: 5GB → 10GB")
        vdo.remove()

        new_logical_size = 10 * GB
        log.info(f"Restarting VDO with logical size {new_logical_size} bytes (10GB)")

        # Create new stack without formatting, with larger logical size
        stack = VDOStack(
            data_dev,
            format=False,
            logical_size=new_logical_size,
            physical_size=physical_size,
            albireo_mem=0.25
        )
        vdo = stack.activate()

        # Verify the logical size was auto-grown
        stats = vdo_stats(vdo)
        logical_blocks = stats['logicalBlocks']
        expected_blocks = new_logical_size // 4096

        log.info(f"VDO reports {logical_blocks} logical blocks, expected {expected_blocks}")
        assert_equal(logical_blocks, expected_blocks,
                    "Logical blocks should match new size after auto-grow")

        # Write and verify data at the end of the newly available space
        write_and_verify_at_end(vdo, new_logical_size, "basic")

        # Restart VDO to verify data persists
        log.info("Restarting VDO to verify data persistence")
        vdo.remove()
        vdo = stack.activate()

        # Verify data persisted across restart
        write_and_verify_at_end(vdo, new_logical_size, "basic")

        # Second auto-grow: 10GB → 20GB
        log.info("Testing second auto-grow: 10GB → 20GB")
        vdo.remove()

        new_logical_size = 20 * GB
        log.info(f"Restarting VDO with logical size {new_logical_size} bytes (20GB)")

        stack = VDOStack(
            data_dev,
            format=False,
            logical_size=new_logical_size,
            physical_size=physical_size,
            albireo_mem=0.25
        )
        vdo = stack.activate()

        # Verify the logical size was auto-grown again
        stats = vdo_stats(vdo)
        logical_blocks = stats['logicalBlocks']
        expected_blocks = new_logical_size // 4096

        log.info(f"VDO reports {logical_blocks} logical blocks, expected {expected_blocks}")
        assert_equal(logical_blocks, expected_blocks,
                    "Logical blocks should match new size after second auto-grow")

        # Write and verify new data at the new end
        write_and_verify_at_end(vdo, new_logical_size, "basic2")

        # Verify both old and new data are intact
        log.info("Verifying both data regions are intact")
        write_and_verify_at_end(vdo, 10 * GB, "basic")  # Old data at 10GB end
        write_and_verify_at_end(vdo, new_logical_size, "basic2")  # New data at 20GB end

        # Final restart to verify both datasets persist
        log.info("Final restart to verify both datasets persist")
        vdo.remove()
        vdo = stack.activate()

        write_and_verify_at_end(vdo, 10 * GB, "basic")
        write_and_verify_at_end(vdo, new_logical_size, "basic2")

        log.info("Auto-grow-logical test completed successfully")

    finally:
        try:
            vdo.remove()
        except:
            pass


def register(tests):
    tests.register("/vdo/grow-logical/auto-grow-logical", t_auto_grow_logical)
