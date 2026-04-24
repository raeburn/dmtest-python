"""
VDO Instance test - Instance number assignment and management
"""
import logging as log
import tempfile

from dmtest.assertions import assert_equal
from dmtest.vdo.vdo_stack import VDOStack
from dmtest.vdo.stats import vdo_stats
from dmtest.vdo.utils import MB
import dmtest.process as process
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets


def t_multiple_instances(fix) -> None:
    """
    Test instance number selection while creating and tearing down
    multiple VDO devices.
    """
    log.info("Creating backing storage for three VDO devices")

    # Create three loop devices as backing storage for the VDO devices
    # Each needs about 4GB of space (VDO minimum with slab_bits=17)
    loop_size = 4 * 1024 * MB

    with tempfile.TemporaryDirectory() as tmpdir:
        loop_devices = []
        backing_files = []

        try:
            # Create three loop devices
            for i in range(3):
                backing_file = f"{tmpdir}/vdo-backing-{i}"
                log.info(f"Creating backing file {backing_file}")
                process.run(f"truncate -s {loop_size} {backing_file}")

                # Set up loop device
                result = process.run(f"losetup -f --show {backing_file}")
                loop_dev = result[1].strip()
                log.info(f"Created loop device {loop_dev}")

                loop_devices.append(loop_dev)
                backing_files.append(backing_file)

            # Create three VDO stacks with small configuration
            log.info("Creating three VDO stacks")
            stacks = [
                VDOStack(loop_devices[0],
                        physical_size=loop_size,
                        logical_size=512 * MB,
                        albireo_mem=0.25,
                        slab_bits=17),
                VDOStack(loop_devices[1],
                        physical_size=loop_size,
                        logical_size=512 * MB,
                        albireo_mem=0.25,
                        slab_bits=17),
                VDOStack(loop_devices[2],
                        physical_size=loop_size,
                        logical_size=512 * MB,
                        albireo_mem=0.25,
                        slab_bits=17)
            ]

            # Activate all three devices
            log.info("Activating three VDO devices")
            vdo_a = stacks[0].activate()
            vdo_b = stacks[1].activate()
            vdo_c = stacks[2].activate()

            try:
                # Check initial instance numbers
                log.info("Checking initial instance numbers")
                instance_a = vdo_stats(vdo_a)['instance']
                instance_b = vdo_stats(vdo_b)['instance']
                instance_c = vdo_stats(vdo_c)['instance']

                log.info(f"Initial instances: A={instance_a}, B={instance_b}, C={instance_c}")
                # Instance numbers are sequential (global counter)
                assert_equal(instance_b, instance_a + 1,
                           f"Device B should have instance {instance_a + 1}")
                assert_equal(instance_c, instance_a + 2,
                           f"Device C should have instance {instance_a + 2}")

                # Remember the base instance for later checks
                base_instance = instance_a

                # Instance numbers aren't permanent for the device; each "start"
                # uses the next available at the time.
                log.info("Testing instance number reassignment after stop/start")
                vdo_a.remove()
                vdo_b.remove()

                # Recreate stacks without formatting
                stacks[1] = VDOStack(loop_devices[1],
                                    physical_size=loop_size,
                                    logical_size=512 * MB,
                                    albireo_mem=0.25,
                                    slab_bits=17,
                                    format=False)
                stacks[0] = VDOStack(loop_devices[0],
                                    physical_size=loop_size,
                                    logical_size=512 * MB,
                                    albireo_mem=0.25,
                                    slab_bits=17,
                                    format=False)

                vdo_b = stacks[1].activate()
                vdo_a = stacks[0].activate()

                instance_a = vdo_stats(vdo_a)['instance']
                instance_b = vdo_stats(vdo_b)['instance']
                instance_c = vdo_stats(vdo_c)['instance']

                log.info(f"After restart: A={instance_a}, B={instance_b}, C={instance_c}")
                # A was stopped first, restarted last, should get base+4
                assert_equal(instance_a, base_instance + 4,
                           f"Device A should have instance {base_instance + 4}")
                # B was stopped second, restarted first, should get base+3
                assert_equal(instance_b, base_instance + 3,
                           f"Device B should have instance {base_instance + 3}")
                # C was never stopped, should keep base+2
                assert_equal(instance_c, base_instance + 2,
                           f"Device C should still have instance {base_instance + 2}")

                vdo_b.remove()

                # Changing characteristics of the device, implemented through
                # reloading the table entry, shouldn't change the instance number.
                log.info("Testing that growLogical doesn't change instance number")
                old_instance = instance_a

                # Grow the logical size
                new_logical_size = 512 * MB + 100 * MB
                log.info(f"Growing device A logical size to {new_logical_size}")

                # Create new table with increased logical size
                new_table = table.Table(
                    targets.VDOTarget(
                        new_logical_size // 512,  # sector count
                        loop_devices[0],
                        loop_size // 4096,  # physical blocks
                        4096,  # mode
                        128 * MB // 4096,  # block_map_cache in blocks
                        16380,  # block_map_period
                        {}  # opts
                    )
                )

                # Reload the table (suspend, load, resume)
                vdo_a.suspend()
                vdo_a.load(new_table)
                vdo_a.resume()

                instance_a = vdo_stats(vdo_a)['instance']
                log.info(f"After growLogical: A={instance_a}")
                assert_equal(instance_a, old_instance,
                           "Instance number should not change after table reload")

                # The cycle should continue where we left off instead of being
                # advanced by the reloads.
                log.info("Verifying instance counter continues correctly")
                stacks[1] = VDOStack(loop_devices[1],
                                    physical_size=loop_size,
                                    logical_size=512 * MB,
                                    albireo_mem=0.25,
                                    slab_bits=17,
                                    format=False)
                vdo_b = stacks[1].activate()

                instance_b = vdo_stats(vdo_b)['instance']
                log.info(f"Restarted device B: instance={instance_b}")
                assert_equal(instance_b, base_instance + 5,
                           f"Device B should have instance {base_instance + 5}")

            finally:
                # Clean up VDO devices
                log.info("Cleaning up VDO devices")
                try:
                    vdo_a.remove()
                except:
                    pass
                try:
                    vdo_b.remove()
                except:
                    pass
                try:
                    vdo_c.remove()
                except:
                    pass

        finally:
            # Clean up loop devices
            log.info("Cleaning up loop devices")
            for loop_dev in loop_devices:
                try:
                    process.run(f"losetup -d {loop_dev}")
                except:
                    pass

    log.info("Instance test completed successfully")


def register(tests):
    tests.register("/vdo/instance/multiple-instances", t_multiple_instances)
