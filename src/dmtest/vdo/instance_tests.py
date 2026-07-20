"""
VDO Instance test - Instance number assignment and management
"""
import logging as log

from dmtest.assertions import assert_equal
from dmtest.device_mapper.dev import dev
from dmtest.tvm import VM, LinearVolume
from dmtest.vdo.vdo_stack import VDOStack
from dmtest.vdo.stats import vdo_stats
from dmtest.vdo.utils import MB
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets


def t_multiple_instances(fix) -> None:
    """
    Test instance number selection while creating and tearing down
    multiple VDO devices.
    """
    log.info("Creating backing storage for three VDO devices")

    data_dev = fix.cfg["data_dev"]

    # Each VDO needs about 4GB of space (VDO minimum with slab_bits=17)
    vdo_size = 4 * 1024 * MB
    vdo_sectors = vdo_size // 512

    # Create volume manager and allocate three linear volumes from data_dev
    log.info("Setting up volume manager for three VDO backing devices")
    vm = VM()
    vm.add_allocation_volume(data_dev)

    # Create three linear volumes
    vm.add_volume(LinearVolume("vdo_backing_0", vdo_sectors))
    vm.add_volume(LinearVolume("vdo_backing_1", vdo_sectors))
    vm.add_volume(LinearVolume("vdo_backing_2", vdo_sectors))

    # Activate the linear devices
    with dev(vm.table("vdo_backing_0")) as linear_dev_0, \
         dev(vm.table("vdo_backing_1")) as linear_dev_1, \
         dev(vm.table("vdo_backing_2")) as linear_dev_2:

        linear_devices = [linear_dev_0.path, linear_dev_1.path, linear_dev_2.path]
        log.info(f"Created linear devices: {linear_devices}")

        # Create three VDO stacks with small configuration
        log.info("Creating three VDO stacks")
        stacks = [
            VDOStack(linear_devices[0],
                    physical_size=vdo_size,
                    logical_size=512 * MB,
                    albireo_mem=0.25,
                    slab_bits=17),
            VDOStack(linear_devices[1],
                    physical_size=vdo_size,
                    logical_size=512 * MB,
                    albireo_mem=0.25,
                    slab_bits=17),
            VDOStack(linear_devices[2],
                    physical_size=vdo_size,
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
            stacks[1] = VDOStack(linear_devices[1],
                                physical_size=vdo_size,
                                logical_size=512 * MB,
                                albireo_mem=0.25,
                                slab_bits=17,
                                format=False)
            stacks[0] = VDOStack(linear_devices[0],
                                physical_size=vdo_size,
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
                    linear_devices[0],
                    vdo_size // 4096,  # physical blocks
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
            stacks[1] = VDOStack(linear_devices[1],
                                physical_size=vdo_size,
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

    # Linear devices are automatically cleaned up by context managers
    log.info("Instance test completed successfully")


def register(tests):
    tests.register("/vdo/instance/multiple-instances", t_multiple_instances)
