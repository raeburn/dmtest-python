from dmtest.assertions import assert_raises
from dmtest.thin.utils import standard_stack, standard_pool
import dmtest.device_mapper.dev as dmdev
import dmtest.pool_stack as ps
import dmtest.tvm as tvm
import dmtest.units as units
import dmtest.utils as utils


def t_create_lots_of_empty_thins(fix):
    with standard_pool(fix) as pool:
        for id in range(1000):
            pool.message(0, f"create_thin {id}")


def t_create_lots_of_empty_snaps(fix):
    with standard_pool(fix) as pool:
        pool.message(0, "create_thin 0")
        for id in range(1, 1000):
            pool.message(0, f"create_snap {id} 0")


def t_create_lots_of_recursive_snaps(fix):
    with standard_pool(fix) as pool:
        pool.message(0, "create_thin 0")
        for id in range(1, 1000):
            pool.message(0, f"create_snap {id} {id - 1}")


def t_activate_thin_while_pool_suspended_fails(fix):
    failed = False
    volume_size = units.gig(4)
    with standard_pool(fix) as pool:
        pool.message(0, "create_thin 0")
        with pool.pause():
            try:
                with ps.thin(pool, volume_size, 0):
                    # expect failure
                    pass
            except Exception:
                failed = True

    assert failed


def t_huge_block_size(fix):
    with standard_pool(fix, block_size=524288) as pool:
        with ps.new_thin(pool, units.gig(4), 0) as thin:
            utils.wipe_device(thin)


def assert_bad_table(table):
    failed = False
    try:
        with dmdev.dev(table):
            pass
    except Exception:
        failed = True

    assert failed


def t_non_power_of_2_block_size_fails(fix):
    stack = standard_stack(fix, block_size=128 + 57)
    table = stack._pool_table()
    assert_bad_table(table)


def t_too_small_block_size_fails(fix):
    stack = standard_stack(fix, block_size=64)
    table = stack._pool_table()
    assert_bad_table(table)


def t_too_large_block_size_fails(fix):
    stack = standard_stack(fix, block_size=2**21 + 1)
    table = stack._pool_table()
    assert_bad_table(table)


def t_largest_block_size_succeeds(fix):
    with standard_pool(fix, block_size=2**21):
        pass


def t_too_large_a_thin_id_fails(fix):
    failed = False
    with standard_pool(fix) as pool:
        try:
            with ps.new_thin(pool, units.gig(4), 2**24):
                pass
        except Exception:
            failed = True
    assert failed


def t_largest_thin_id_succeeds(fix):
    with standard_pool(fix) as pool:
        with ps.new_thin(pool, units.gig(4), 2**24 - 1):
            pass


def t_too_small_a_metadata_dev_fails(fix):
    vm = tvm.VM()
    vm.add_allocation_volume(fix.cfg("data_dev"))
    vm.add_volume(tvm.LinearVolume("metadata", units.kilo(16)))
    vm.add_volume(tvm.LinearVolume("data", units.gig(8)))

    with dmdev.dev(vm.table("metadata")) as metadata:
        with dmdev.dev(vm.table("data")) as data:

            def bring_up_pool():
                with standard_pool(fix, data_dev=data, metadata_dev=metadata):
                    pass

            assert_raises(bring_up_pool)


def register(tests):
    tests.register_batch(
        "/thin/creation/",
        [
            ("lots-of-empty-thins", t_create_lots_of_empty_thins),
            ("lots-of-empty-snaps", t_create_lots_of_empty_snaps),
            ("lots-of-recursive-snaps", t_create_lots_of_recursive_snaps),
            (
                "activate-thin-while-pool-suspended-fails",
                t_activate_thin_while_pool_suspended_fails,
            ),
            ("huge-block-size", t_huge_block_size),
            ("non-power-of-2-block-size-fails", t_non_power_of_2_block_size_fails),
            ("too-small-block-size-fails", t_too_small_block_size_fails),
            ("too-large-block-size-fails", t_too_large_block_size_fails),
            ("largest-block-size-succeeds", t_largest_block_size_succeeds),
            ("too-large-a-thin-id-fails", t_too_large_a_thin_id_fails),
            ("largest-thin-id-succeeds", t_largest_thin_id_succeeds),
            ("too-small-a-metadata-dev-fails", t_too_small_a_metadata_dev_fails),
        ],
    )
