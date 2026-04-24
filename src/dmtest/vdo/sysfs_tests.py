"""Tests VDO sysfs interface."""

import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.process import run
from dmtest.vdo.utils import standard_vdo


def read_sysfs(path: str) -> str:
    """Read a sysfs file and return its content (stripped).

    Parameters
    ----------
    path : str
        Path to the sysfs file

    Returns
    -------
    str
        Content of the file with whitespace stripped
    """
    log.info(f"Reading sysfs file: {path}")
    _, stdout, _ = run(f"cat {path}")
    return stdout


def write_sysfs(path: str, value: str, should_succeed: bool = True) -> bool:
    """Write a value to a sysfs file.

    Parameters
    ----------
    path : str
        Path to the sysfs file
    value : str
        Value to write
    should_succeed : bool
        Whether the write should succeed

    Returns
    -------
    bool
        True if write succeeded, False otherwise
    """
    log.info(f"Writing '{value}' to {path}")
    returncode, _, _ = run(f"echo {value} > {path}", raise_on_fail=False)
    success = (returncode == 0)

    if should_succeed and not success:
        raise RuntimeError(f"Failed to write '{value}' to {path}")
    elif not should_succeed and success:
        raise RuntimeError(f"Write to {path} succeeded but was expected to fail")

    if not success:
        log.info(f"Write failed as expected")

    return success


def get_major_minor(device_path: str) -> tuple[int, int]:
    """Get the major and minor device numbers.

    Parameters
    ----------
    device_path : str
        Path to the device

    Returns
    -------
    tuple[int, int]
        (major, minor) device numbers
    """
    stat_info = os.stat(device_path)
    major = os.major(stat_info.st_rdev)
    minor = os.minor(stat_info.st_rdev)
    return (major, minor)


def path_exists(path: str) -> bool:
    """Check if a path exists.

    Parameters
    ----------
    path : str
        Path to check

    Returns
    -------
    bool
        True if path exists
    """
    return os.path.exists(path)


def read_check(path: str, expected: str = None) -> None:
    """Read and optionally verify a sysfs file value.

    Parameters
    ----------
    path : str
        Path to the sysfs file
    expected : str, optional
        Expected value (if None, just read and log)
    """
    value = read_sysfs(path)
    if expected is not None:
        assert_equal(expected, value, f"Value from {path}")
    else:
        log.info(f"{path}: {value}")


def readonly_check(path: str, expected: str = None) -> None:
    """Verify a sysfs file is read-only.

    Parameters
    ----------
    path : str
        Path to the sysfs file
    expected : str, optional
        Expected value
    """
    read_check(path, expected)
    test_value = expected if expected is not None else "0"
    # Try to write (should fail)
    write_sysfs(path, test_value, should_succeed=False)


def write_check(path: str, expected: str, trial: str) -> None:
    """Verify a sysfs file is writable (by root).

    Parameters
    ----------
    path : str
        Path to the sysfs file
    expected : str
        Initial expected value
    trial : str
        Value to write as test
    """
    # Check initial value
    read_check(path, expected)

    # Write trial value
    write_sysfs(path, trial, should_succeed=True)
    read_check(path, trial)

    # Restore original value
    write_sysfs(path, expected, should_succeed=True)
    read_check(path, expected)


def write_check_if_exists(path: str, expected: str, trial: str) -> None:
    """Like write_check, but only if the file exists.

    Parameters
    ----------
    path : str
        Path to the sysfs file
    expected : str
        Initial expected value
    trial : str
        Value to write as test
    """
    if path_exists(path):
        write_check(path, expected, trial)
    else:
        log.info(f"Skipping {path} (does not exist)")


def t_sysfs(fix) -> None:
    """Test VDO sysfs interface for module parameters and block device attributes.

    Verifies that VDO exposes correct sysfs attributes including module version,
    tunable parameters, and block device characteristics.
    """
    with standard_vdo(fix) as vdo:
        major, minor = get_major_minor(vdo.path)
        major_minor = f"{major}:{minor}"
        log.info(f"VDO device {vdo.path} has major:minor {major_minor}")

        # Determine the module name (kvdo or dm-vdo)
        # Check both possible module names
        module_name = None
        for name in ["kvdo", "dm_vdo", "dm-vdo"]:
            mod_dir = f"/sys/module/{name}"
            if path_exists(mod_dir):
                module_name = name
                break

        if module_name is None:
            raise RuntimeError("Could not find VDO module in /sys/module")

        log.info(f"Using module name: {module_name}")
        sys_mod_dir = f"/sys/module/{module_name}"

        # Check version in module directory
        version_path = f"{sys_mod_dir}/version"
        if path_exists(version_path):
            version = read_sysfs(version_path)
            log.info(f"VDO module version: {version}")
        else:
            log.info(f"Version file {version_path} does not exist")

        # Module parameters directory
        sys_mod_parm_dir = f"{sys_mod_dir}/parameters"

        # Check tunable parameters
        # Note: These parameters may not exist on all VDO versions
        if path_exists(sys_mod_parm_dir):
            # deduplication_timeout_interval
            param_path = f"{sys_mod_parm_dir}/deduplication_timeout_interval"
            if path_exists(param_path):
                current = read_sysfs(param_path)
                write_check(param_path, current, "4000")

            # log_level
            param_path = f"{sys_mod_parm_dir}/log_level"
            if path_exists(param_path):
                current = read_sysfs(param_path)
                write_check(param_path, current, "7")

            # max_discard_sectors (may not exist)
            param_path = f"{sys_mod_parm_dir}/max_discard_sectors"
            write_check_if_exists(param_path, "8", "64")

            # max_requests_active
            param_path = f"{sys_mod_parm_dir}/max_requests_active"
            if path_exists(param_path):
                current = read_sysfs(param_path)
                write_check(param_path, current, "1000")

            # min_deduplication_timer_interval
            param_path = f"{sys_mod_parm_dir}/min_deduplication_timer_interval"
            if path_exists(param_path):
                current = read_sysfs(param_path)
                write_check(param_path, current, "200")

        # Block device directory
        block_dev_dir = f"/sys/dev/block/{major_minor}"

        # Check block device parameters
        read_check(f"{block_dev_dir}/alignment_offset", "0")
        read_check(f"{block_dev_dir}/discard_alignment", "0")
        read_check(f"{block_dev_dir}/ro", "0")
        read_check(f"{block_dev_dir}/dm/suspended", "0")
        read_check(f"{block_dev_dir}/queue/discard_granularity", "4096")
        read_check(f"{block_dev_dir}/queue/discard_max_bytes", "4096")
        read_check(f"{block_dev_dir}/queue/hw_sector_size", "4096")
        read_check(f"{block_dev_dir}/queue/logical_block_size", "4096")
        read_check(f"{block_dev_dir}/queue/minimum_io_size", "4096")
        read_check(f"{block_dev_dir}/queue/optimal_io_size", "4096")
        read_check(f"{block_dev_dir}/queue/physical_block_size", "4096")


def t_sysfs_length(fix) -> None:
    """Test VDO sysfs parameter boundary checking.

    Verifies that VDO module parameters correctly validate numeric ranges
    and reject values that overflow their data types.
    """
    with standard_vdo(fix) as vdo:
        # Determine the module name
        module_name = None
        for name in ["kvdo", "dm_vdo", "dm-vdo"]:
            mod_dir = f"/sys/module/{name}"
            if path_exists(mod_dir):
                module_name = name
                break

        if module_name is None:
            raise RuntimeError("Could not find VDO module in /sys/module")

        sys_mod_parm_dir = f"/sys/module/{module_name}/parameters"

        if not path_exists(sys_mod_parm_dir):
            log.info(f"Parameters directory {sys_mod_parm_dir} does not exist, skipping test")
            return

        # Test max_requests_active (signed 32-bit int)
        param_path = f"{sys_mod_parm_dir}/max_requests_active"
        if path_exists(param_path):
            original = read_sysfs(param_path)

            # Should accept 2^31 - 1
            write_sysfs(param_path, "2147483647", should_succeed=True)

            # Should reject 2^31
            write_sysfs(param_path, "2147483648", should_succeed=False)

            # Restore original
            write_sysfs(param_path, original, should_succeed=True)
        else:
            log.info(f"Parameter {param_path} does not exist, skipping")

        # Test min_deduplication_timer_interval (unsigned 32-bit int)
        param_path = f"{sys_mod_parm_dir}/min_deduplication_timer_interval"
        if path_exists(param_path):
            original = read_sysfs(param_path)

            # Should accept 2^32 - 1
            write_sysfs(param_path, "4294967295", should_succeed=True)

            # Should reject 2^32
            write_sysfs(param_path, "4294967296", should_succeed=False)

            # Restore original
            write_sysfs(param_path, original, should_succeed=True)
        else:
            log.info(f"Parameter {param_path} does not exist, skipping")

        # Test deduplication_timeout_interval (unsigned 32-bit int)
        param_path = f"{sys_mod_parm_dir}/deduplication_timeout_interval"
        if path_exists(param_path):
            original = read_sysfs(param_path)

            # Should accept 2^32 - 1
            write_sysfs(param_path, "4294967295", should_succeed=True)

            # Should reject 2^32
            write_sysfs(param_path, "4294967296", should_succeed=False)

            # Restore original
            write_sysfs(param_path, original, should_succeed=True)
        else:
            log.info(f"Parameter {param_path} does not exist, skipping")


def register(tests):
    """Register the Sysfs tests."""
    tests.register_batch("/vdo/sysfs/", [
        ("basic", t_sysfs),
        ("length-check", t_sysfs_length),
    ])
