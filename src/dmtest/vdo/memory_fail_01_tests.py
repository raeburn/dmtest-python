"""VDO memory allocation failure test.

Tests VDO robustness during startup when memory allocations fail, verifying
proper error handling and checking for memory leaks. Requires kvdo module
with memory fault injection sysfs interface.
"""
import logging as log
import os

from dmtest.assertions import assert_equal
from dmtest.test_register import MissingTestDep
from dmtest.vdo.utils import standard_vdo
from dmtest.vdo.status import vdo_status
import dmtest.process as process


# Sysfs paths for memory fault injection
ALLOC_COUNTER = "/sys/uds/memory/allocation_counter"
BYTES_USED = "/sys/uds/memory/bytes_used"
CANCEL_ALLOC_FAILURE = "/sys/uds/memory/cancel_allocation_failure"
ERROR_INJECTION_COUNTER = "/sys/uds/memory/error_injection_counter"
LOG_ALLOCATIONS = "/sys/uds/memory/log_allocations"
SCHEDULE_ALLOC_FAILURE = "/sys/uds/memory/schedule_allocation_failure"
TRACK_ALLOCATIONS = "/sys/uds/memory/track_allocations"

# Maximum number of allocation failure injection passes to test.
# Set to a number to cap the test duration, or None to test exhaustively.
MAX_ALLOCATION_FAILURE_PASSES = None


def read_sysfs_int(path: str) -> int:
    """Read an integer value from a sysfs file."""
    with open(path, 'r') as f:
        return int(f.read().strip())


def write_sysfs(path: str, value: str) -> None:
    """Write a value to a sysfs file."""
    with open(path, 'w') as f:
        f.write(value)


def get_bytes_used() -> int:
    """Return the number of bytes allocated by the uds module."""
    return read_sysfs_int(BYTES_USED)


def schedule_allocation_failure(count: int) -> None:
    """Schedule a future memory allocation failure at position count."""
    log.info(f"Scheduling allocation failure at position {count}")
    write_sysfs(SCHEDULE_ALLOC_FAILURE, str(count))


def cancel_allocation_failure() -> None:
    """Cancel any future memory allocation failure."""
    write_sysfs(CANCEL_ALLOC_FAILURE, "0")


def is_allocation_failure_pending() -> bool:
    """Return True if an allocation failure is scheduled but hasn't occurred yet."""
    alloc_counter = read_sysfs_int(ALLOC_COUNTER)
    error_injection_counter = read_sysfs_int(ERROR_INJECTION_COUNTER)
    return alloc_counter < error_injection_counter


def track_allocations(enable: bool) -> None:
    """Enable or disable memory allocation tracking (disabled by default)."""
    # Only enable if we want detailed leak debugging
    pass


def log_allocations() -> None:
    """Log currently tracked allocations to kernel log (if tracking enabled)."""
    # Only used for debugging, not enabled in standard test runs
    pass


def check_kvdo_memory_interface():
    """Check for kvdo memory fault injection sysfs interface.

    Raises MissingTestDep if /sys/uds/memory directory doesn't exist,
    which indicates the development kvdo module with memory fault injection
    is not loaded.
    """
    if not os.path.isdir("/sys/uds/memory"):
        raise MissingTestDep(
            "kvdo module with memory fault injection support "
            "(/sys/uds/memory not found)"
        )


def t_memory_fail_start(fix) -> None:
    """Test VDO device robustness when memory allocations fail during startup.

    Systematically injects memory allocation failures at each position during
    VDO device initialization to verify proper error handling and no memory leaks.

    The number of injection passes is controlled by MAX_ALLOCATION_FAILURE_PASSES.
    If set to None, the test runs exhaustively until all allocations are tested
    (matching the Perl version's behavior). If set to a number, the test is capped
    at that many passes.

    The test also measures and logs the total number of memory allocations required
    for successful VDO device startup by detecting when an allocation failure at
    position N doesn't trigger (meaning startup completed with fewer than N allocations).
    """
    log.info("Creating and formatting VDO device")

    # Create and format the VDO device, then stop it to get baseline
    with standard_vdo(fix) as initial_vdo:
        pass

    # Record baseline memory overhead after a clean start-stop cycle
    allocation_overhead = get_bytes_used()
    log.info(f"Allocation overhead is {allocation_overhead} bytes")

    # Verify memory usage is stable after a start-stop cycle
    log.info("Verifying memory stability with clean start-stop cycle")
    with standard_vdo(fix, format=False) as vdo:
        pass

    assert_equal(allocation_overhead, get_bytes_used(), "Memory leak during start+stop")

    # Main test loop: inject allocation failures at each position
    pass_num = 1
    while True:
        # Check if we've hit the cap (if one is configured)
        if MAX_ALLOCATION_FAILURE_PASSES is not None and pass_num > MAX_ALLOCATION_FAILURE_PASSES:
            break
        log.info(f"=== Pass {pass_num}: Failing allocation #{pass_num} ===")

        # Schedule allocation failure at position pass_num
        schedule_allocation_failure(pass_num)
        track_allocations(True)

        # Record allocation counter before start attempt
        alloc_count_before = read_sysfs_int(ALLOC_COUNTER)

        # Attempt to start the VDO device
        start_error = None
        vdo_mode = None
        vdo = None
        status = None

        try:
            vdo = standard_vdo(fix, format=False).__enter__()
            # If we get here, the device started (possibly in degraded state)
            status = vdo_status(vdo)
            vdo_mode = status["mode"]
            log.info(f"VDO started in {vdo_mode} mode")

            # If not in read-only mode, check index state
            if vdo_mode != "read-only":
                index_state = status["index-state"]
                log.info(f"Index state: {index_state}")
        except Exception as e:
            start_error = e
            log.info(f"VDO start failed as expected: {e}")

        # Check if the scheduled allocation failure actually occurred
        if is_allocation_failure_pending():
            # Allocation failure didn't trigger - we've exhausted all allocations
            # Calculate the actual number of allocations that occurred during startup
            alloc_count_after = read_sysfs_int(ALLOC_COUNTER)
            actual_allocations = alloc_count_after - alloc_count_before
            log.info(f"Allocation failure #{pass_num} did not trigger - startup needs fewer allocations")
            log.info(f"VDO device startup required {actual_allocations} allocations")

            cancel_allocation_failure()
            track_allocations(False)

            # VDO should have started successfully
            if start_error:
                if vdo:
                    vdo.__exit__(None, None, None)
                raise AssertionError(f"VDO should have started successfully but got: {start_error}")

            # Verify VDO is online (unless in read-only mode)
            if vdo_mode != "read-only" and status is not None:
                index_state = status["index-state"]
                if index_state not in ["online", "opening"]:
                    if vdo:
                        vdo.__exit__(None, None, None)
                    raise AssertionError(f"Expected index online, got {index_state}")

            # Clean up and finish
            if vdo:
                vdo.__exit__(None, None, None)
            log.info(f"Test complete - all {actual_allocations} allocations tested")
            break

        # Allocation failure did occur
        log.info(f"Allocation failure #{pass_num} triggered successfully")

        # Stop the VDO device if it started
        if vdo:
            log.info("Stopping VDO device after failed allocation")
            vdo.__exit__(None, None, None)

        # Check for memory leaks
        track_allocations(False)
        current_bytes = get_bytes_used()
        if allocation_overhead != current_bytes:
            log_allocations()
            leak_size = current_bytes - allocation_overhead
            raise AssertionError(f"Memory leak in pass {pass_num}: {leak_size} bytes leaked")

        log.info(f"Pass {pass_num} complete - no memory leak detected")

        # Move to next allocation position
        pass_num += 1

    # If we get here, we hit the cap without completing all allocations
    if MAX_ALLOCATION_FAILURE_PASSES is not None:
        log.info(f"Test capped at {MAX_ALLOCATION_FAILURE_PASSES} passes - device requires more than {MAX_ALLOCATION_FAILURE_PASSES} allocations to start")


def register(tests):
    tests.register("/vdo/memory-fail/start", t_memory_fail_start, [], check_kvdo_memory_interface)
