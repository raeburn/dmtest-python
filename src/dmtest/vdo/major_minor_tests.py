"""Tests VDO device creation using major:minor device specification."""

import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.device_mapper.dev import dev
from dmtest.device_mapper.table import Table
from dmtest.device_mapper.targets import LinearTarget, VDOTarget
from dmtest.gendatablocks import BlockRange
from dmtest.process import run
from dmtest.utils import dev_size


def get_major_minor(device_path: str) -> str:
    """Get the major:minor device number for a device path.

    Parameters
    ----------
    device_path : str
        Path to the device (e.g., "/dev/dm-0")

    Returns
    -------
    str
        Device number in "major:minor" format
    """
    stat_info = os.stat(device_path)
    major = os.major(stat_info.st_rdev)
    minor = os.minor(stat_info.st_rdev)
    return f"{major}:{minor}"


def t_basic(fix) -> None:
    """Test VDO device creation with major:minor backing device specification.

    Validates that VDO correctly handles major:minor device numbers instead of
    device paths, and that data persists across device stop/start cycles.
    """
    data_dev = fix.cfg["data_dev"]

    # Create a linear device to use as backing storage
    log.info("Creating linear backing device")
    size = dev_size(data_dev)
    linear_table = Table(LinearTarget(size, data_dev, 0))

    with dev(linear_table) as linear_dev:
        # Get the major:minor of the linear device
        major_minor = get_major_minor(linear_dev.path)
        log.info(f"Linear device {linear_dev.path} has major:minor {major_minor}")

        # Format the VDO device using the linear device path
        logical_size = 20 * 1024 * 1024 * 1024  # 20GB
        physical_size = size * 512  # Convert sectors to bytes

        log.info("Formatting VDO device")
        run(f"vdoformat --force --logical-size={logical_size}B "
            f"--uds-memory-size=0.25 {linear_dev.path}")

        # Create VDO table using major:minor instead of device path
        log.info(f"Creating VDO device with major:minor specification: {major_minor}")
        vdo_table = Table(
            VDOTarget(
                logical_size // 512,  # sector count
                major_minor,  # Use major:minor instead of path
                physical_size // 4096,  # physical blocks
                4096,  # mode (block size)
                128 * 1024 * 1024 // 4096,  # block map cache blocks
                16380,  # block map period
                {}  # additional options
            )
        )

        # Write and verify data with the VDO device
        block_count = 100
        br = None

        # Activate the VDO device
        with dev(vdo_table) as vdo_dev:
            log.info(f"VDO device activated at {vdo_dev.path}")

            # Write 100 blocks of test data
            log.info(f"Writing {block_count} blocks of test data")
            br = BlockRange(vdo_dev.path, block_count=block_count)
            br.write(tag="devnum", dedupe=0.0, compress=0.0, fsync=True)

            # Verify the data
            log.info("Verifying written data")
            br.verify()

        # VDO device is now stopped (exited context manager)
        log.info("VDO device stopped, restarting to verify data persistence")

        # Restart the VDO device with the same table
        with dev(vdo_table) as vdo_dev:
            log.info(f"VDO device restarted at {vdo_dev.path}")

            # Update the BlockRange path to the new device (may have different name)
            br.update_path(vdo_dev.path)

            # Verify the data again to ensure it persisted
            log.info("Verifying data after restart")
            br.verify()

            log.info("Test completed successfully")


def register(tests):
    """Register the MajorMinor tests."""
    tests.register("/vdo/major-minor/basic", t_basic)
