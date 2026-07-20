"""
VDO SlabCount01 test - Verify slab counts with various configurations
"""
import logging as log

from dmtest.assertions import assert_equal
import dmtest.device_mapper.dev as dmdev
import dmtest.tvm as tvm
import dmtest.units as units
from dmtest.vdo.utils import standard_vdo, MB, GB
import dmtest.vdo.stats as stats
import dmtest.vdo.vdo_stack as vs


def t_tiny_tiny(fix) -> None:
    """Test VDO with smallest slab_bits=15 configuration."""
    data_dev = fix.cfg["data_dev"]

    # With current VDO version, 3GB physical with slab_bits=15 gives 2 slabs
    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("vdo_storage", units.gig(3)))

    with dmdev.dev(vm.table("vdo_storage")) as storage:
        vdo_volume = vs.VDOStack(storage,
                                logical_size=2 * GB,
                                slab_bits=15)
        with vdo_volume.activate() as vdo:
            vdo_stats = stats.vdo_stats(vdo)
            slab_count = vdo_stats['allocator']['slabCount']
            log.info(f"Slab count: {slab_count}")
            # Verify we get a small number of slabs with minimal storage
            assert 1 <= slab_count <= 3, f"Expected 1-3 slabs, got {slab_count}"


def t_tiny_multi(fix) -> None:
    """Test VDO with moderate slab_bits=15 configuration."""
    data_dev = fix.cfg["data_dev"]

    # With current VDO version, 4.3GB physical with slab_bits=15 gives ~11 slabs
    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("vdo_storage", units.meg(4300)))

    with dmdev.dev(vm.table("vdo_storage")) as storage:
        vdo_volume = vs.VDOStack(storage,
                                logical_size=4 * GB,
                                slab_bits=15)
        with vdo_volume.activate() as vdo:
            vdo_stats = stats.vdo_stats(vdo)
            slab_count = vdo_stats['allocator']['slabCount']
            log.info(f"Slab count: {slab_count}")
            # Verify we get more slabs than tiny-tiny but not too many
            assert 8 <= slab_count <= 15, f"Expected 8-15 slabs, got {slab_count}"


def t_tiny_small(fix) -> None:
    """Test larger slab_bits=15 configuration."""
    data_dev = fix.cfg["data_dev"]

    # With current VDO version, 12GB physical with slab_bits=15 gives ~74 slabs
    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("vdo_storage", units.gig(12)))

    with dmdev.dev(vm.table("vdo_storage")) as storage:
        vdo_volume = vs.VDOStack(storage, slab_bits=15)
        with vdo_volume.activate() as vdo:
            vdo_stats = stats.vdo_stats(vdo)
            slab_count = vdo_stats['allocator']['slabCount']
            log.info(f"Slab count: {slab_count}")
            # Verify we get significantly more slabs with more storage
            assert 60 <= slab_count <= 80, f"Expected 60-80 slabs, got {slab_count}"


def t_small_small(fix) -> None:
    """Test SLAB_BITS_SMALL with small physical size: minimum 2 slabs."""
    data_dev = fix.cfg["data_dev"]

    vm = tvm.VM()
    vm.add_allocation_volume(data_dev)
    vm.add_volume(tvm.LinearVolume("vdo_storage", units.gig(4)))

    with dmdev.dev(vm.table("vdo_storage")) as storage:
        vdo_volume = vs.VDOStack(storage, slab_bits=17)
        with vdo_volume.activate() as vdo:
            vdo_stats = stats.vdo_stats(vdo)
            slab_count = vdo_stats['allocator']['slabCount']
            log.info(f"Slab count: {slab_count}")
            assert slab_count >= 2, f"Expected at least 2 slabs, got {slab_count}"


# Note: t_small and t_large tests from the Perl test suite are not included here
# because they require storage larger than the 20GB test device:
# - t_small expects 138+ slabs with slab_bits=17 (2GB slabs), requiring 276GB+
# - t_large uses slab_bits=23 (32GB slabs), requiring 35GB+ minimum
# These tests were conditionally run in the Perl suite based on available storage.


def register(tests):
    tests.register_batch(
        "/vdo/slab-count/",
        [
            ("tiny-tiny", t_tiny_tiny),
            ("tiny-multi", t_tiny_multi),
            ("tiny-small", t_tiny_small),
            ("small-small", t_small_small),
        ],
    )
