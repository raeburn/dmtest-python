from dmtest.thin.utils import standard_stack, standard_pool
from dmtest.assertions import assert_equal
import dmtest.pool_stack as ps
import dmtest.process as process
import dmtest.thin.status as status
import dmtest.units as units
import dmtest.utils as utils
import logging as log

#---------------------------------

fio_config = """
[provision]
rw=randwrite
bs=64k
ioengine=libaio
iodepth=32
direct=1
verify=crc32c
io_size=10%
"""

def do_provision(path):
    with utils.TempFile() as cfg:
        cfg.file.write(fio_config)
        cfg.file.flush()
        process.run(f"fio {cfg.path} --filename={path} --do_verify=0 --output-format=terse")

def do_verify(path):
    with utils.TempFile() as cfg:
        cfg.file.write(fio_config)
        cfg.file.flush()
        process.run(f"fio {cfg.path} --filename={path} --verify_only --output-format=terse")

#---------------------------------

class ThinMigrate:
    def migrate_to_thin(src_thin, dest_thin):
        process.run(f"thin_migrate --source-dev {src_thin} --dest-dev {dest_thin}")

    def migrate_to_file(src_thin, dest_thin):
        process.run(f"thin_migrate --source-dev {src_thin} --dest-file {dest_thin}")


def t_migrate_thin_to_thin(fix):
    data_dev = fix.cfg("data_dev")
    thin_size = min(units.gig(1), utils.dev_size(data_dev) // 2)

    with standard_pool(fix, block_size = 128, zero = True) as src_pool:
        with ps.new_thin(src_pool, thin_size, 0) as src_thin:
            do_provision(src_thin)

            with ps.new_snap(src_pool, thin_size, 1, 0, pause_dev = src_thin,
                             read_only = True) as src_snap:
                with ps.new_thin(src_pool, thin_size, 2) as dest_thin:
                    src_pool.message(0, f"reserve_metadata_snap")

                    ThinMigrate.migrate_to_thin(src_snap, dest_thin)
                    do_verify(dest_thin)

                    dest_status = status.thin_status(dest_thin)
                    src_status = status.thin_status(src_thin)
                    assert_equal(dest_status['mapped-sectors'],
                                 src_status['mapped-sectors'])
                    assert_equal(dest_status['highest-mapped-sector'],
                                 src_status['highest-mapped-sector'])

                    src_pool.message(0, f"release_metadata_snap")


def t_migrate_thin_to_file(fix):
    data_dev = fix.cfg("data_dev")
    thin_size = min(units.gig(1), utils.dev_size(data_dev) // 2)

    with standard_pool(fix, block_size = 128, zero = True) as src_pool:
        with ps.new_thin(src_pool, thin_size, 0) as src_thin:
            do_provision(src_thin)

            with ps.new_snap(src_pool, thin_size, 1, 0, pause_dev = src_thin,
                             read_only = True) as src_snap:
                src_pool.message(0, f"reserve_metadata_snap")

                ThinMigrate.migrate_to_file(src_snap, "migrate_dest")

                try:
                    do_verify("migrate_dest")
                    process.run(f"unlink migrate_dest")
                except:
                    raise


def t_large_block_size(fix):
    thin_size = units.gig(1)

    data_dev = fix.cfg("data_dev")
    if utils.dev_size(data_dev) < 2 * thin_size:
        raise Exception("insufficient pool size for running the test")

    with standard_pool(fix, block_size = 131072, zero = True) as src_pool:
        with ps.new_thin(src_pool, thin_size, 0) as src_thin:
            do_provision(src_thin)

            with ps.new_snap(src_pool, thin_size, 1, 0, pause_dev = src_thin,
                             read_only = True) as src_snap:
                with ps.new_thin(src_pool, thin_size, 2) as dest_thin:

                    with src_thin.pause():
                        src_pool.message(0, f"reserve_metadata_snap")

                    ThinMigrate.migrate_to_thin(src_snap, dest_thin)
                    do_verify(dest_thin)

                    dest_status = status.thin_status(dest_thin)
                    src_status = status.thin_status(src_thin)
                    assert_equal(dest_status['mapped-sectors'],
                                 src_status['mapped-sectors'])
                    assert_equal(dest_status['highest-mapped-sector'],
                                 src_status['highest-mapped-sector'])

                    src_pool.message(0, f"release_metadata_snap")


def register(tests):
    tests.register_batch(
        "/thin_migrate/migrate",
        [
            ("thin_to_thin", t_migrate_thin_to_thin),
            ("thin_to_file", t_migrate_thin_to_file),
            ("large_block_size", t_large_block_size)
        ],
    )
