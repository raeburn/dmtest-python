"""
VDO GrowLogical02 - Online logical growth with mounted filesystem

Tests that VDO correctly handles online logical growth while a filesystem
is mounted and actively receiving writes. After growing, simulates a
power cycle and verifies data integrity. Converted from GrowLogical02.pm.
"""
import logging as log
import os
import tempfile
import threading
import time

from dmtest.assertions import assert_equal
from dmtest.fs import Ext4
from dmtest.utils import dev_size
from dmtest.vdo.dataset_helpers import write_file_dataset, verify_file_dataset
from dmtest.vdo.vdo_stack import VDOStack
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.process as process
import dmtest.vdo.stats as stats


MB = 1024 * 1024
GB = 1024 * MB
BLOCK_SIZE = 4096

LOGICAL_SIZE = 5 * GB
LOGICAL_GROWTH = 40 * GB
SLAB_BITS = 15


def _make_vdo_table(logical_size: int, data_dev: str, physical_size: int) -> table.Table:
    """Construct a VDO dmsetup table for the given logical size."""
    return table.Table(
        targets.VDOTarget(
            logical_size // 512,
            data_dev,
            physical_size // 4096,
            4096,
            128 * MB // 4096,
            16380,
            {}
        )
    )


def _grow_logical(vdo, data_dev: str, new_logical_size: int, physical_size: int) -> None:
    """Grow VDO logical size via dmsetup table reload."""
    log.info(f"Growing VDO logical size to {new_logical_size // GB}GB")
    new_table = _make_vdo_table(new_logical_size, data_dev, physical_size)
    vdo.suspend()
    vdo.load(new_table)
    vdo.resume()


def _safe_umount(mount_point: str) -> None:
    """Unmount a filesystem, ignoring errors if not mounted."""
    try:
        process.run(f"umount {mount_point}", raise_on_fail=False)
    except Exception:
        pass


def t_filesystem(fix) -> None:
    """Test online logical growth with a mounted filesystem and simulated reboot."""
    data_dev = fix.cfg["data_dev"]
    physical_size = dev_size(data_dev) * 512
    new_logical_size = LOGICAL_SIZE + LOGICAL_GROWTH

    stack = VDOStack(data_dev, format=True, logical_size=LOGICAL_SIZE,
                     physical_size=physical_size, slab_bits=SLAB_BITS)

    with tempfile.TemporaryDirectory() as mount_point:
        vdo = stack.activate()
        try:
            fs = Ext4(vdo.path)
            fs.format()
            fs.mount(mount_point)

            # Start async filesystem writes (100 files, 100MB, 25% dedupe)
            write_error = None
            write_result = [None]

            def write_task():
                nonlocal write_error
                try:
                    _, ranges = write_file_dataset(
                        mount_point, "initial", 100,
                        num_bytes=100 * MB, dedupe=0.25
                    )
                    write_result[0] = ranges
                except Exception as e:
                    write_error = e

            log.info("Starting async write of 100 files (100MB, 25%% dedupe)")
            thread = threading.Thread(target=write_task)
            thread.start()

            time.sleep(2)

            # Grow logical while writes are in progress
            _grow_logical(vdo, data_dev, new_logical_size, physical_size)

            # Resize filesystem to use new space
            log.info("Resizing ext4 filesystem")
            process.run(f"resize2fs {vdo.path}")

            thread.join()
            if write_error:
                raise write_error

            # Verify VDO reports the new logical size
            new_logical_blocks = new_logical_size // BLOCK_SIZE
            vdo_st = stats.vdo_stats(vdo)
            log.info(f"VDO logical blocks: {vdo_st['logicalBlocks']}, "
                     f"expected: {new_logical_blocks}")
            assert_equal(vdo_st['logicalBlocks'], new_logical_blocks,
                         "logical blocks should reflect grow operation")

            # Simulate power cycle: unmount, stop VDO, restart, remount
            log.info("Simulating power cycle")
            process.run(f"umount {mount_point}")
            process.run("echo 1 > /proc/sys/vm/drop_caches")
            process.run(f"fsck.ext4 -fn {vdo.path}")
            vdo.remove()

            restart_stack = VDOStack(
                data_dev, format=False, logical_size=new_logical_size,
                physical_size=physical_size, slab_bits=SLAB_BITS
            )
            vdo = restart_stack.activate()

            fs = Ext4(vdo.path)
            os.makedirs(mount_point, exist_ok=True)
            fs.mount(mount_point)

            # Verify data survived the power cycle
            log.info("Verifying data after power cycle")
            verify_file_dataset(write_result[0], "initial")

            # Write more data to confirm the filesystem is fully usable
            log.info("Writing additional data after power cycle")
            write_file_dataset(mount_point, "reboot", 100,
                               num_bytes=100 * MB, dedupe=0.25)

            # Verify logical blocks unchanged after reboot
            vdo_st = stats.vdo_stats(vdo)
            log.info(f"VDO logical blocks after reboot: {vdo_st['logicalBlocks']}")
            assert_equal(vdo_st['logicalBlocks'], new_logical_blocks,
                         "logical blocks should persist across power cycle")

            process.run(f"umount {mount_point}")
            process.run("echo 1 > /proc/sys/vm/drop_caches")
            process.run(f"fsck.ext4 -fn {vdo.path}")
        finally:
            _safe_umount(mount_point)
            try:
                vdo.remove()
            except Exception:
                pass


def register(tests):
    tests.register("/vdo/grow-logical/filesystem", t_filesystem)
