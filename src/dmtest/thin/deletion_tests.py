import pytest
from dmtest.thin.utils import standard_stack, standard_pool
import dmtest.device_mapper.dev as dmdev
import dmtest.pool_stack as ps
import dmtest.thin.status as status
import dmtest.tvm as tvm
import dmtest.units as units
import dmtest.utils as utils
import re


def test_create_delete_cycle(fix):
    with standard_pool(fix) as pool:
        for id in range(1000):
            pool.message(0, "create_thin 0")
            pool.message(0, "delete 0")


def test_create_many_delete_many(fix):
    with standard_pool(fix) as pool:
        for id in range(1000):
            pool.message(0, f"create_thin {id}")

        for id in range(1000):
            pool.message(0, f"delete {id}")


def test_create_delete_rolling(fix):
    with standard_pool(fix) as pool:
        for id in range(1000):
            pool.message(0, f"create_thin {id}")

        for id in range(1000):
            pool.message(0, f"delete {id}")
            pool.message(0, f"create_thin {id}")


def test_delete_provisioned_thin(fix):
    thin_size = units.meg(512)
    block_size = units.kilo(64)

    with standard_pool(fix, block_size=block_size) as pool:
        with ps.new_thin(pool, thin_size, 0) as thin:
            utils.wipe_device(thin)

        s = status.pool_status(pool)
        assert (s["data-used"] * block_size) == thin_size

        pool.message(0, "delete 0")

        s = status.pool_status(pool)
        assert s["data-used"] == 0


def test_delete_unknown_id_fails(fix):
    with standard_pool(fix) as pool:

        def delete():
            pool.message(0, "delete 0")

        with pytest.raises(Exception):
            delete()


def test_delete_active_id_fails(fix):
    with standard_pool(fix) as pool:
        with ps.new_thin(pool, units.gig(4), 0):

            def delete():
                pool.message(0, "delete 0")

            with pytest.raises(Exception):
                delete()


def test_delete_after_out_of_space(fix):
    with standard_pool(fix, error_if_no_space=True, data_size=units.gig(4)) as pool:
        with ps.new_thin(pool, units.gig(8), 0) as thin:
            try:
                utils.wipe_device(thin)
            except Exception:
                pass

        s = status.pool_status(pool)
        assert s["mode"] == "out-of-data-space"

        pool.message(0, "delete 0")

        s = status.pool_status(pool)
        assert s["mode"] == "read-write"
        assert s["data-used"] == 0
