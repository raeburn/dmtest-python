"""VDO filling and space management test.

Tests VDO device filling, deduplication of data on a full device, and space
reclamation via discard operations. Verifies free space tracking at each step.
"""
from dmtest.assertions import assert_equal
import dmtest.device_mapper.dev as dmdev
from dmtest.gendatablocks import make_block_range
import dmtest.process as process
import dmtest.tvm as tvm
import dmtest.units as units
import dmtest.vdo.stats as stats
from dmtest.vdo.utils import MB, GB, populate_block_map
import dmtest.vdo.vdo_stack as vs

import logging as log
import time

def get_free_space(stats):
    return stats["physicalBlocks"] - stats["overheadBlocksUsed"] - stats["dataBlocksUsed"]

def t_full(fix):
    data_dev = fix.cfg("data_dev")
    # Configure a small device so we can fill it quickly.
    slab_bits = 13
    size_gb = 3
    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("storage", units.gig(size_gb)))
    with dmdev.dev(vm.table("storage")) as storage:
        vdo_volume = vs.VDOStack(storage, logical_size = 3 * size_gb * GB,
                                 slab_bits = slab_bits)
        with vdo_volume.activate() as vdo:
            # Initialize the block map, so we can calculate how many data
            # blocks we still have room for.
            populate_block_map(vdo)
            mapped_stats = stats.vdo_stats(vdo)
            assert_equal(mapped_stats["dataBlocksUsed"], 0)
            free_space = get_free_space(mapped_stats)
            size1 = (free_space - 1) * 4096
            size2 = MB
            # This test assumes size1 > size2 ...
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
            range1.write(tag="tag1")
            new_stats = stats.vdo_stats(vdo)
            free_space = get_free_space(new_stats)
            assert_equal(free_space, 1)
            # New locations but repeated data
            range2.write(tag="tag1")
            new_stats = stats.vdo_stats(vdo)
            free_space = get_free_space(new_stats)
            assert_equal(free_space, 1)
            # Finish filling the device - new location & data
            range3.write(tag="tag2")
            new_stats = stats.vdo_stats(vdo)
            free_space = get_free_space(new_stats)
            assert_equal(free_space, 0)
            # Writing duplicate data should work
            range4.write(tag="tag2", fsync=True)
            # Trimming a never-written location is a no-op, but it'll
            # set up range5 to know to expect to find zero blocks when
            # we verify.
            range5.trim()
            # Writing new data should fail
            gave_error = False
            try:
                range5.write(tag="tag3", fsync=True)
            except OSError as e:
                # VDO should be generating ENOSPC errors here but what
                # we get out is EIO from fsync. Getting back the
                # ENOSPC to Python may require using direct I/O in
                # gendatablocks, which isn't supported currently.
                #
                # For now, just expect some error to have come back.
                gave_error = True
                log.info(f"exception raised! {e}")
            if not gave_error:
                raise AssertionError("writing new data to full VDO should fail")
            # The write failed, so range5 will not have updated its
            # idea of the data we should find there; it still expects
            # zero blocks.
            range1.verify()
            range2.verify()
            range3.verify()
            range4.verify()
            range5.verify()
            # Free some space - discard some unique, some duplicated data
            range1.trim(fsync=True)
            new_stats = stats.vdo_stats(vdo)
            free_space = get_free_space(new_stats)
            assert_equal(free_space, (size1 - MB) // 4096)

def register(tests):
    tests.register("/vdo/full", t_full)
