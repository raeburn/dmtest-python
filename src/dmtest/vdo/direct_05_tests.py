"""
VDO Direct05 test - Deduplication of data being overwritten
"""
import logging as log

from dmtest.gendatablocks import make_block_range
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo, settle_devices
import dmtest.process as process


def t_direct_05(fix) -> None:
    """
    Test deduplication of data that is being overwritten.

    This test writes identical data to overlapping addresses to create
    a scenario where block X overwrites Y at address A, while block Y
    overwrites Z at address B. This tests VDO's deduplication behavior
    during concurrent overwrites.
    """
    block_count = 1000

    with standard_vdo(fix, slab_bits=17) as vdo:
        # Wait for udev to settle
        settle_devices()

        # First write and verify: 1000 blocks at offset 0
        log.info(f"First write: {block_count} blocks at offset 0")
        slice0 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=0)
        slice0.write(tag="Direct5", dedupe=0, compress=0, fsync=True)

        log.info("Dropping caches")
        process.run("echo 1 > /proc/sys/vm/drop_caches")

        log.info("Verifying first write")
        slice0.verify()

        # Second write and verify: 1000 blocks at offset 1 (overlaps with first)
        log.info(f"Second write: {block_count} blocks at offset 1")
        slice1 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=1)
        slice1.write(tag="Direct5", dedupe=0, compress=0, fsync=True)

        log.info("Dropping caches")
        process.run("echo 1 > /proc/sys/vm/drop_caches")

        log.info("Verifying second write")
        slice1.verify()

        # Third write and verify: rewrite 1000 blocks at offset 0
        log.info(f"Third write: {block_count} blocks at offset 0 (overwrite)")
        slice0.write(tag="Direct5", dedupe=0, compress=0, fsync=True)

        log.info("Dropping caches")
        process.run("echo 1 > /proc/sys/vm/drop_caches")

        log.info("Verifying third write")
        slice0.verify()

        log.info("Direct05 test completed successfully")


def register(tests):
    tests.register("/vdo/direct/direct-05", t_direct_05)
