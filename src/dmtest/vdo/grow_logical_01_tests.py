"""
VDO GrowLogical01 - Online logical growth tests

Tests online growth of VDO logical space with concurrent I/O during growth,
and validates minimum growth increment. Converted from GrowLogical01.pm.
"""
import logging as log
import threading
import time

from dmtest.assertions import assert_equal
from dmtest.gendatablocks import make_block_range
from dmtest.utils import dev_size
from dmtest.vdo.utils import standard_vdo, GB, MB, BLOCK_SIZE
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.vdo.stats as stats


BLOCK_COUNT = 5000
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
    log.info(f"Growing VDO logical size to {new_logical_size} bytes")
    new_table = _make_vdo_table(new_logical_size, data_dev, physical_size)
    vdo.suspend()
    vdo.load(new_table)
    vdo.resume()


def t_basic(fix) -> None:
    """Test that VDO handles online logical growth with concurrent I/O."""
    data_dev = fix.cfg["data_dev"]
    physical_size = dev_size(data_dev) * 512

    with standard_vdo(fix, logical_size=LOGICAL_SIZE, slab_bits=SLAB_BITS) as vdo:
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

        new_logical_size = LOGICAL_SIZE + LOGICAL_GROWTH
        _grow_logical(vdo, data_dev, new_logical_size, physical_size)

        thread.join()
        if write_error:
            raise write_error

        initial_logical_blocks = LOGICAL_SIZE // BLOCK_SIZE
        log.info(f"Writing {BLOCK_COUNT} blocks at offset {initial_logical_blocks} in grown space")
        slice2 = make_block_range(path=vdo.path, block_count=BLOCK_COUNT,
                                  block_size=BLOCK_SIZE, offset=initial_logical_blocks)
        slice2.write(tag="basic", direct=True)

        log.info("Verifying data in both slices")
        slice1.verify()
        slice2.verify()

        new_logical_blocks = new_logical_size // BLOCK_SIZE
        vdo_st = stats.vdo_stats(vdo)
        log.info(f"VDO logical blocks: {vdo_st['logicalBlocks']}, expected: {new_logical_blocks}")
        assert_equal(vdo_st['logicalBlocks'], new_logical_blocks,
                     "logical blocks should reflect grow operation")


def t_minimum_growth(fix) -> None:
    """Test that VDO rejects non-block-aligned sizes but accepts block-aligned growth."""
    data_dev = fix.cfg["data_dev"]
    physical_size = dev_size(data_dev) * 512

    with standard_vdo(fix, logical_size=LOGICAL_SIZE, slab_bits=SLAB_BITS) as vdo:
        # Growth to a non-block-aligned size should fail
        bad_size = LOGICAL_SIZE + BLOCK_SIZE - 1024
        log.info(f"Attempting invalid growth to {bad_size} bytes (not block-aligned)")

        gave_error = False
        vdo.suspend()
        try:
            bad_table = _make_vdo_table(bad_size, data_dev, physical_size)
            vdo.load(bad_table)
            vdo.resume()
        except Exception:
            gave_error = True

        # Recover the device to an active state
        if gave_error:
            try:
                vdo.resume()
            except Exception:
                old_table = _make_vdo_table(LOGICAL_SIZE, data_dev, physical_size)
                vdo.load(old_table)
                vdo.resume()

        assert gave_error, "Growth to non-block-aligned size should have failed"

        # Growth by exactly one block should succeed
        new_size = LOGICAL_SIZE + BLOCK_SIZE
        log.info(f"Growing by exactly one block to {new_size} bytes")
        _grow_logical(vdo, data_dev, new_size, physical_size)

        log.info("Minimum growth test passed")


def register(tests):
    tests.register_batch("/vdo/grow-logical/", [
        ("basic", t_basic),
        ("minimum-growth", t_minimum_growth),
    ])
