"""
VDO GrowPhysical03 - Test growing VDO physical storage with a filesystem

Grows the physical backing of a VDO device and writes enough non-unique data
to confirm the new space is usable. Converted from GrowPhysical03.pm, with
LVM operations replaced by dm-linear equivalents.
"""
import logging as log

from dmtest.vdo.utils import GB, BLOCK_SIZE, mounted_fs
from dmtest.vdo.vdo_stack import VDOStack
from dmtest.vdo.dataset_helpers import write_file_dataset, verify_file_dataset
import dmtest.device_mapper.dev as dmdev
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.process as process


PHYSICAL_SIZE = 5 * GB
GROWN_PHYSICAL_SIZE = 10 * GB
LOGICAL_SIZE = 20 * GB
SLAB_BITS = 15
DATA_SIZE = 6 * GB
NUM_FILES = 6


def _make_linear_table(data_dev: str, size_bytes: int) -> table.Table:
    """Create a dm-linear table mapping size_bytes from data_dev."""
    return table.Table(targets.LinearTarget(size_bytes // 512, data_dev, 0))


def _grow_physical(vdo, linear_dev, data_dev: str,
                   new_phys_bytes: int, logical_size: int) -> None:
    """Grow VDO physical size by resizing backing storage and reloading VDO."""
    log.info(f"Growing VDO physical size from {PHYSICAL_SIZE // GB}GB "
             f"to {new_phys_bytes // GB}GB")
    vdo.suspend()
    new_linear = _make_linear_table(data_dev, new_phys_bytes)
    linear_dev.suspend()
    linear_dev.load(new_linear)
    linear_dev.resume()
    new_vdo_table = table.Table(
        targets.VDOTarget(
            logical_size // 512,
            linear_dev.path,
            new_phys_bytes // BLOCK_SIZE,
            4096,
            128 * 1024 * 1024 // BLOCK_SIZE,
            16380,
            {}
        )
    )
    vdo.load(new_vdo_table)
    vdo.resume()


def t_use_new(fix) -> None:
    """Test that non-unique data uses new physical space after growth."""
    data_dev = fix.cfg["data_dev"]
    linear_table = _make_linear_table(data_dev, PHYSICAL_SIZE)

    with dmdev.dev(linear_table) as linear_dev:
        stack = VDOStack(linear_dev.path, physical_size=PHYSICAL_SIZE,
                         logical_size=LOGICAL_SIZE, slab_bits=SLAB_BITS)
        with stack.activate() as vdo:
            _grow_physical(vdo, linear_dev, data_dev,
                           GROWN_PHYSICAL_SIZE, LOGICAL_SIZE)

            with mounted_fs(vdo.path, format=True) as mount_point:
                log.info(f"Writing {DATA_SIZE // GB}GB of non-unique data "
                         f"({NUM_FILES} files)")
                _, ranges = write_file_dataset(
                    mount_point, "grow", NUM_FILES,
                    num_bytes=DATA_SIZE,
                )
                process.run("sync")

                log.info("Verifying data")
                verify_file_dataset(ranges, "grow")


def register(tests):
    tests.register("/vdo/grow-physical/use-new", t_use_new)
