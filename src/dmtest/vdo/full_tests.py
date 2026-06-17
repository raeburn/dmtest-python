"""VDO full-device boundary test.

Precisely fills a VDO device to capacity and exercises behavior at the
boundary: deduplication still works on a full device, new allocations fail
with ENOSPC, statistics remain consistent after failed writes, all data
verifies correctly, and trim reclaims space as expected.
"""
import errno

from dmtest.assertions import assert_equal
import dmtest.device_mapper.dev as dmdev
from dmtest.gendatablocks import make_block_range
import dmtest.tvm as tvm
import dmtest.units as units
import dmtest.vdo.stats as stats
from dmtest.vdo.stats import get_free_blocks
from dmtest.vdo.utils import MB, GB, populate_block_map
import dmtest.vdo.vdo_stack as vs


def t_full_boundary(fix):
    data_dev = fix.cfg["data_dev"]
    slab_bits = 13
    size_gb = 3
    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("storage", units.gig(size_gb)))
    with dmdev.dev(vm.table("storage")) as storage:
        vdo_volume = vs.VDOStack(storage, logical_size=3 * size_gb * GB,
                                 slab_bits=slab_bits)
        with vdo_volume.activate() as vdo:
            populate_block_map(vdo)
            mapped_stats = stats.vdo_stats(vdo)
            assert_equal(mapped_stats["dataBlocksUsed"], 0)
            free_space = get_free_blocks(mapped_stats)

            size1 = (free_space - 1) * 4096
            size2 = MB

            range1 = make_block_range(path=vdo.path, block_size=4096,
                                      block_count=size1 // 4096)
            range2 = make_block_range(path=vdo.path, block_size=4096,
                                      block_count=size2 // 4096,
                                      offset=size1 // 4096)
            range3 = make_block_range(path=vdo.path, block_size=4096,
                                      block_count=1,
                                      offset=(size1 + size2) // 4096)
            range4 = make_block_range(path=vdo.path, block_size=4096,
                                      block_count=1,
                                      offset=(size1 + size2) // 4096 + 1)
            range5 = make_block_range(path=vdo.path, block_size=4096,
                                      block_count=1,
                                      offset=(size1 + size2) // 4096 + 2)

            # Fill all blocks but one.
            range1.write(tag="tag1", direct=True)
            new_stats = stats.vdo_stats(vdo)
            assert_equal(get_free_blocks(new_stats), 1)

            # New locations but repeated data — dedup means no new allocation.
            range2.write(tag="tag1", direct=True)
            new_stats = stats.vdo_stats(vdo)
            assert_equal(get_free_blocks(new_stats), 1)

            # Finish filling the device — new location & data.
            range3.write(tag="tag2", direct=True)
            new_stats = stats.vdo_stats(vdo)
            assert_equal(get_free_blocks(new_stats), 0)

            # Writing duplicate data should still work on a full device.
            range4.write(tag="tag2", direct=True)

            # Snapshot stats before the expected failure.
            before_fail = stats.vdo_stats(vdo)

            # Trimming a never-written location sets up range5 to expect zeros.
            range5.trim()

            # Writing new data should fail with ENOSPC.
            try:
                range5.write(tag="tag3", direct=True)
                raise AssertionError("writing new data to full VDO should fail")
            except OSError as e:
                assert e.errno == errno.ENOSPC, f"expected ENOSPC, got {e}"

            # Stats should be unchanged after the failed write.
            after_fail = stats.vdo_stats(vdo)
            for key in ("physicalBlocks", "overheadBlocksUsed",
                        "dataBlocksUsed", "logicalBlocks"):
                assert_equal(after_fail[key], before_fail[key], key)

            # Verify all data.
            range1.verify()
            range2.verify()
            range3.verify()
            range4.verify()
            range5.verify()

            # Free some space — discard range1 which contains unique data
            # plus the duplicated portion shared with range2.
            range1.trim(fsync=True)
            new_stats = stats.vdo_stats(vdo)
            assert_equal(get_free_blocks(new_stats), (size1 - MB) // 4096)


def register(tests):
    tests.register("/vdo/full/boundary", t_full_boundary)
