"""
VDO GrowPhysical01 - Online physical growth tests

Tests online growth of VDO physical storage with concurrent writes, rejection
of too-small-growth, and offline storage resize followed by online VDO
physical growth. Converted from GrowPhysical01.pm, with LVM operations
replaced by dm-linear equivalents.

The original Perl test also tested rejection of no-growth (same-size resize),
but that was enforced by LVM, not VDO. With direct dmsetup, VDO accepts a
same-size table reload as a no-op, so that test is omitted.
"""
import logging as log
import threading
import time

from dmtest.gendatablocks import make_block_range
from dmtest.vdo.utils import GB, MB, BLOCK_SIZE
from dmtest.vdo.vdo_stack import VDOStack
import dmtest.device_mapper.dev as dmdev
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets


BLOCK_COUNT = 5000
PHYSICAL_SIZE = 5 * GB
GROWN_SIZE = 20 * GB
LOGICAL_SIZE = 20 * GB
SLAB_BITS = 15


def _make_linear_table(data_dev: str, size_bytes: int) -> table.Table:
    """Create a dm-linear table mapping size_bytes from data_dev."""
    return table.Table(targets.LinearTarget(size_bytes // 512, data_dev, 0))


def _make_vdo_table(logical_size: int, backing_dev: str,
                    physical_size: int) -> table.Table:
    """Create a VDO device-mapper table."""
    return table.Table(
        targets.VDOTarget(
            logical_size // 512,
            backing_dev,
            physical_size // BLOCK_SIZE,
            4096,
            128 * MB // BLOCK_SIZE,
            16380,
            {}
        )
    )


def _resize_linear(linear_dev, data_dev: str, new_size_bytes: int) -> None:
    """Resize a dm-linear device by reloading its table."""
    log.info(f"Resizing linear device to {new_size_bytes} bytes")
    new_table = _make_linear_table(data_dev, new_size_bytes)
    linear_dev.suspend()
    linear_dev.load(new_table)
    linear_dev.resume()


def _grow_physical(vdo, linear_dev, data_dev: str,
                   new_phys_bytes: int, logical_size: int) -> None:
    """Grow VDO physical size by resizing backing storage and reloading VDO."""
    log.info(f"Growing VDO physical size to {new_phys_bytes // GB}GB")
    vdo.suspend()
    _resize_linear(linear_dev, data_dev, new_phys_bytes)
    new_vdo_table = _make_vdo_table(logical_size, linear_dev.path, new_phys_bytes)
    vdo.load(new_vdo_table)
    vdo.resume()


def t_basic(fix) -> None:
    """Test online growth of VDO physical storage with concurrent writes."""
    data_dev = fix.cfg["data_dev"]
    linear_table = _make_linear_table(data_dev, PHYSICAL_SIZE)

    with dmdev.dev(linear_table) as linear_dev:
        stack = VDOStack(linear_dev.path, physical_size=PHYSICAL_SIZE,
                         logical_size=LOGICAL_SIZE, slab_bits=SLAB_BITS)
        with stack.activate() as vdo:
            slice1 = make_block_range(path=vdo.path, block_count=BLOCK_COUNT,
                                      block_size=BLOCK_SIZE, offset=0)

            write_error = None

            def write_task():
                nonlocal write_error
                try:
                    slice1.write(tag="basic", direct=True)
                except Exception as e:
                    write_error = e

            log.info(f"Starting async write of {BLOCK_COUNT} blocks with direct I/O")
            thread = threading.Thread(target=write_task)
            thread.start()
            time.sleep(1)

            _grow_physical(vdo, linear_dev, data_dev, GROWN_SIZE, LOGICAL_SIZE)

            thread.join()
            if write_error:
                raise write_error

            log.info("Verifying data after physical growth")
            slice1.verify()


def t_offline(fix) -> None:
    """Test physical growth after offline storage resize."""
    data_dev = fix.cfg["data_dev"]
    linear_table = _make_linear_table(data_dev, PHYSICAL_SIZE)

    with dmdev.dev(linear_table) as linear_dev:
        # Create and immediately stop VDO
        stack = VDOStack(linear_dev.path, physical_size=PHYSICAL_SIZE,
                         logical_size=LOGICAL_SIZE, slab_bits=SLAB_BITS)
        vdo = stack.activate()
        log.info("Stopping VDO for offline resize")
        vdo.remove()

        # Resize backing storage while VDO is stopped
        _resize_linear(linear_dev, data_dev, GROWN_SIZE)

        # Restart VDO with original physical size
        log.info("Restarting VDO with original physical size")
        stack2 = VDOStack(linear_dev.path, format=False,
                          physical_size=PHYSICAL_SIZE,
                          logical_size=LOGICAL_SIZE)
        with stack2.activate() as vdo:
            # Grow VDO physical to match the larger backing storage
            log.info("Growing VDO physical to match resized storage")
            vdo.suspend()
            new_table = _make_vdo_table(LOGICAL_SIZE, linear_dev.path,
                                        GROWN_SIZE)
            vdo.load(new_table)
            vdo.resume()
            log.info("Offline resize + online grow completed successfully")


def t_too_small(fix) -> None:
    """Test that VDO rejects a physical grow that is too small."""
    data_dev = fix.cfg["data_dev"]
    linear_table = _make_linear_table(data_dev, PHYSICAL_SIZE)

    with dmdev.dev(linear_table) as linear_dev:
        stack = VDOStack(linear_dev.path, physical_size=PHYSICAL_SIZE,
                         logical_size=LOGICAL_SIZE, slab_bits=SLAB_BITS)
        with stack.activate() as vdo:
            too_small_size = PHYSICAL_SIZE + BLOCK_SIZE
            log.info(f"Attempting grow by one block to {too_small_size} bytes")

            vdo.suspend()
            _resize_linear(linear_dev, data_dev, too_small_size)

            gave_error = False
            try:
                small_table = _make_vdo_table(LOGICAL_SIZE, linear_dev.path,
                                              too_small_size)
                vdo.load(small_table)
                vdo.resume()
            except Exception:
                gave_error = True

            if gave_error:
                try:
                    vdo.resume()
                except Exception:
                    old_table = _make_vdo_table(LOGICAL_SIZE, linear_dev.path,
                                               PHYSICAL_SIZE)
                    vdo.load(old_table)
                    vdo.resume()

            assert gave_error, "Grow by one block should have been rejected"
            log.info("Too-small growth correctly rejected")


def register(tests):
    tests.register_batch("/vdo/grow-physical/", [
        ("basic", t_basic),
        ("offline", t_offline),
        ("too-small", t_too_small),
    ])
