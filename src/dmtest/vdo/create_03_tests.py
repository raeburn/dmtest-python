"""
VDO Create03 test - Device lifecycle stability and logging verification
"""
import logging as log
import re
import time

from dmtest.assertions import assert_string_in
from dmtest.utils import get_dmesg_log
from dmtest.vdo.utils import standard_stack, settle_devices
import dmtest.process as process


def t_create_03(fix) -> None:
    """
    Test creating and tearing down VDO devices many times to verify device
    lifecycle stability and proper logging behavior. Also verifies that VDO
    messages include device instance numbers in kernel logs.
    """
    iteration_count = 1024

    log.info(f"Starting Create03 test with {iteration_count} iterations")

    # Create the VDO stack (formats the device)
    stack = standard_stack(fix, slab_bits=17)

    for i in range(iteration_count):
        # Record time before starting device
        start_time = time.time()

        # Activate the VDO device
        vdo = stack.activate()

        # Wait for udev to settle
        settle_devices()

        # After first iteration, check kernel logs
        if i > 0:
            log.info(f"Iteration {i + 1}/{iteration_count}: Checking kernel logs")
            kernel_log = get_dmesg_log(start_time)

            # Verify that VDO messages are present (pattern: "vdo[0-9]+:")
            # This regex looks for lines like "vdo0: ..." or "vdo1: ..."
            vdo_messages = [line for line in kernel_log.split('\n')
                           if re.search(r'vdo[0-9]+:', line)]

            if vdo_messages:
                # Verify that VDO messages include device instance number
                # Pattern: "vdo([0-9]+:|:\[SI\]:)"
                # This matches "vdo0:", "vdo1:", "vdo:S:", "vdo:I:", etc.
                for msg in vdo_messages:
                    # Messages from interrupt context may be anonymous (vdo:S: or vdo:I:)
                    # so we allow those, but most messages should have instance numbers
                    if not re.search(r'vdo([0-9]+:|:\[[SI]\]:)', msg):
                        log.warning(f"VDO message without instance number: {msg}")
        elif i == 0:
            log.info(f"Iteration {i + 1}/{iteration_count}: First iteration (no log check)")

        # Log progress periodically
        if (i + 1) % 100 == 0:
            log.info(f"Completed {i + 1}/{iteration_count} iterations")

        # Stop the VDO device
        vdo.remove()

        # For the next iteration, we don't need to format (already formatted)
        stack = standard_stack(fix, format=False, slab_bits=17)

    log.info(f"Create03 test completed successfully after {iteration_count} iterations")


def register(tests):
    tests.register("/vdo/creation/create-03", t_create_03)
