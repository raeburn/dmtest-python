from dmtest.assertions import assert_raises, assert_equal
from dmtest.thin.utils import standard_stack, standard_pool
import dmtest.dataset as dataset
import dmtest.device_mapper.dev as dmdev
import dmtest.fs as fs
import dmtest.git as git
import dmtest.pool_stack as ps
import dmtest.process as process
import dmtest.thin.status as status
import dmtest.tvm as tvm
import dmtest.units as units
import dmtest.utils as utils
import dmtest.pattern_stomper as stomper
import dmtest.test_register as reg

import os
import threading
import logging as log


def run_overwrite(fix, fs_type):
    thin_size = units.gig(4)
    ds1 = dataset.Dataset.read("compile-bench-datasets/dataset-unpatched")
    ds2 = dataset.Dataset.read("compile-bench-datasets/dataset-unpatched-compiled")

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, thin_size, 0) as thin:
            thin_fs = fs_type(thin)
            thin_fs.format()
            dir = "./mnt1"
            with thin_fs.mount_and_chdir(dir):
                ds1.apply(1000)

            with thin_fs.mount_and_chdir(dir):
                ds2.apply(1000)


def t_overwrite_ext4(fix):
    run_overwrite(fix, fs.Ext4)


def t_overwrite_xfs(fix):
    run_overwrite(fix, fs.Xfs)


# ---------------------------------


def run_create_snap(fix, fs_type):
    thin_size = units.gig(4)
    ds1 = dataset.Dataset.read("compile-bench-datasets/dataset-unpatched")
    ds2 = dataset.Dataset.read("compile-bench-datasets/dataset-unpatched-compiled")

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, thin_size, 0) as thin:
            thin_fs = fs_type(thin)
            thin_fs.format()
            dir = "./mnt1"
            with thin_fs.mount_and_chdir(dir):
                ds1.apply(1000)

                with ps.new_snap(pool, thin_size, 1, 0, pause_dev=thin) as snap:
                    dir = "./mnt2"
                    thin_fs2 = fs_type(snap)
                    with thin_fs2.mount_and_chdir(dir):
                        ds2.apply(1000)


def t_create_snap_ext4(fix):
    run_create_snap(fix, fs.Ext4)


def t_create_snap_xfs(fix):
    run_create_snap(fix, fs.Xfs)


# ---------------------------------


def run_break_sharing(fix, fs_type):
    block_size = units.kilo(64)
    thin_size = units.gig(4)
    blocks_per_dev = thin_size / block_size
    ds1 = dataset.Dataset.read("compile-bench-datasets/dataset-unpatched")
    ds2 = dataset.Dataset.read("compile-bench-datasets/dataset-unpatched-compiled")
    dir = "./mnt1"

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, thin_size, 0) as thin:
            thin_fs = fs_type(thin)
            thin_fs.format()
            with thin_fs.mount_and_chdir(dir):
                ds1.apply(1000)

            data_used = status.pool_status(pool)["data-used"]
            print(f"data used: {data_used}, expected: {blocks_per_dev}")

            with ps.new_snap(pool, thin_size, 1, 0) as snap:
                thin_fs2 = fs_type(snap)
                with thin_fs2.mount_and_chdir(dir):
                    ds2.apply(1000)

            data_used = status.pool_status(pool)["data-used"]
            print(f"data used: {data_used}, expected: {blocks_per_dev * 2}")


def t_break_sharing_ext4(fix):
    run_break_sharing(fix, fs.Ext4)


def t_break_sharing_xfs(fix):
    run_break_sharing(fix, fs.Xfs)


# ---------------------------------


def t_space_use(fix):
    block_size = units.kilo(64)
    thin_size = units.gig(4)
    blocks_per_dev = int(thin_size / block_size)

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, thin_size, 0) as thin:
            utils.wipe_device(thin)

        data_used = status.pool_status(pool)["data-used"]
        assert_equal(data_used, blocks_per_dev)

        with ps.new_snap(pool, thin_size, 1, 0) as snap:
            utils.wipe_device(snap)

        data_used = status.pool_status(pool)["data-used"]
        assert_equal(data_used, 2 * blocks_per_dev)


# ---------------------------------


def t_many_snapshots_of_same_volume(fix):
    thin_size = units.meg(256)

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, thin_size, 0) as thin:
            utils.dt_device(thin)

            with thin.pause():
                for id in range(1, 1000):
                    pool.message(0, f"create_snap {id} 0")

            utils.dt_device(thin)

        with ps.thin(pool, thin_size, 1) as thin:
            utils.dt_device(thin, rseed=2345)


def t_parallel_io_to_shared_thins(fix):
    thin_size = units.gig(1)
    with standard_pool(fix) as pool:
        with ps.new_thin(pool, thin_size, 0) as thin:
            utils.wipe_device(thin)

        for id in range(1, 6):
            pool.message(0, f"create_snap {id} 0")

        # Define the function to be run in each thread
        def run_dt(id):
            with ps.thin(pool, thin_size, id) as thin:
                utils.dt_device(thin)

        # Create and start threads for each ID
        threads = []
        for id in range(6):
            thread = threading.Thread(target=run_dt, args=(id,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()


# This test is specifically aimed at exercising the auxillery ref
# count tree in the metadata.
def t_ref_count_tree(fix):
    volume_size = units.meg(256)

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, volume_size, 0) as thin:
            utils.wipe_device(thin)

        for id in range(1, 6):
            pool.message(0, f"create_snap {id} 0")

        with ps.thins(pool, volume_size, *[0, 1, 2, 3, 4, 5]) as thins:
            for thin in thins:
                utils.wipe_device(thin)


# Break sharing by writing to a snapshot
def t_stomp_snap(fix):
    volume_size = units.gig(1)
    block_size = units.kilo(64)

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, volume_size, 0) as thin:
            origin_stomper = stomper.PatternStomper(
                thin.path, block_size, need_zero=True
            )
            origin_stomper.stamp(20)

            with ps.new_snap(pool, volume_size, 1, 0, thin) as snap:
                snap_stomper = origin_stomper.fork(snap.path)
                snap_stomper.verify(0, 1)

                snap_stomper.stamp(10)
                snap_stomper.verify(0, 2)
                origin_stomper.verify(0, 1)


# Break sharing by writing to the origin
def t_stomp_origin(fix):
    """
    Test the snapshot functionality by writing to the origin volume and
    verifying the data.

    This test creates a thin-provisioned storage volume and writes a
    pattern to it using a PatternStomper. It then takes a snapshot of
    the volume and verifies the data on both the origin volume and the
    snapshot. Next, it writes a new pattern to the origin volume and
    verifies that the changes are reflected only in the origin volume,
    while the snapshot retains the previous state.

    Args:
        fix (Fixture): A test fixture object containing volume size,
        data block size, and pool size.
    """

    volume_size = units.gig(1)
    block_size = units.kilo(64)

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, volume_size, 0) as thin:
            origin_stomper = stomper.PatternStomper(
                thin.path, block_size, need_zero=True
            )
            origin_stomper.stamp(20)

            with ps.new_snap(pool, volume_size, 1, 0, thin) as snap:
                snap_stomper = origin_stomper.fork(snap.path)

                origin_stomper.verify(0, 1)

                origin_stomper.stamp(10)
                origin_stomper.verify(0, 2)

                snap_stomper.verify(0, 1)


def t_many_snaps_with_changes(fix):
    fs_type = fs.Ext4

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, units.gig(20), 0) as thin:
            git.prepare(thin, fs_type)

            def mk_snap_(index):
                log.info("about to create snap")
                with thin.pause():
                    pool.message(0, f"create_snap {index + 1} 0")
                log.info("done")

            git.extract_each(thin, fs_type, mk_snap_)


def t_try_and_create_duplicates(fix):
    fs_type = fs.Ext4

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, units.gig(20), 0) as thin:
            git.prepare(thin, fs_type)

            with ps.new_snap(pool, units.gig(20), 1, 0, thin) as snap:
                git.extract(thin, fs_type, git.TAGS[10:11])
                git.extract(thin, fs_type, git.TAGS[0:3])

                git.extract(thin, fs_type, git.TAGS[20:21])
                git.extract(snap, fs_type, git.TAGS[0:3])


# ---------------------------------


def register(tests):
    tests.register_batch(
        "/thin/snapshot",
        [
            ("space-use", t_space_use),
            ("many-snapshots-of-same-volume", t_many_snapshots_of_same_volume),
            ("parallel-io-to-shared-thins", t_parallel_io_to_shared_thins),
            ("ref-count-tree", t_ref_count_tree),
            ("many-snaps-with-changes", t_many_snaps_with_changes, [], reg.check_linux_repo),
            ("try-and-create-duplicates", t_try_and_create_duplicates, [], reg.check_linux_repo),
        ],
    )
    tests.register_batch(
        "/thin/snapshot/ext4",
        [
            ("overwrite", t_overwrite_ext4),
            ("create-snap", t_create_snap_ext4),
            ("break-sharing", t_break_sharing_ext4),
        ],
    )
    tests.register_batch(
        "/thin/snapshot/xfs",
        [
            ("overwrite", t_overwrite_xfs),
            ("create-snap", t_create_snap_xfs),
            ("break-sharing", t_break_sharing_xfs),
        ],
    )
    tests.register_batch(
        "/thin/snapshot/pattern-stomper/",
        [("snap", t_stomp_snap), ("origin", t_stomp_origin)],
    )
