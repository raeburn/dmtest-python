from dmtest.thin.utils import standard_stack, standard_pool
import dmtest.pool_stack as ps
import dmtest.process as process
import dmtest.units as units
import dmtest.utils as utils
import logging as log
import subprocess

#---------------------------------

def t_insufficient_buffer_size(fix):
    data_dev = fix.cfg("data_dev")
    thin_size = min(units.gig(1), utils.dev_size(data_dev) // 2)

    with standard_pool(fix, block_size = 4096, zero = True) as src_pool:
        with ps.new_thin(src_pool, thin_size, 0, read_only = True) as src_thin:
            with ps.new_thin(src_pool, thin_size, 1) as dest_thin:
                with src_thin.pause():
                    src_pool.message(0, f"reserve_metadata_snap")

                try:
                    process.run(f"thin_migrate --source-dev {src_thin} --dest-dev {dest_thin} --buffer-size-meg 1")
                except subprocess.CalledProcessError:
                    pass
                except:
                    raise
                else:
                    raise Exception("command succeeded without error")

                src_pool.message(0, f"release_metadata_snap")


def t_input_none_thin_device(fix):
    data_dev = fix.cfg("data_dev")
    try:
        process.run(f"thin_migrate --source-dev {data_dev} --dest-file migrate_dest")
    except subprocess.CalledProcessError:
        pass
    except:
        raise
    else:
        raise Exception("command succeeded without error")


def t_device_not_present_in_metadata_snap(fix):
    data_dev = fix.cfg("data_dev")
    thin_size = min(units.gig(1), utils.dev_size(data_dev) // 2)

    with standard_pool(fix, block_size = 128, zero = True) as src_pool:
        src_pool.message(0, f"reserve_metadata_snap")

        with ps.new_thin(src_pool, thin_size, 0, read_only = True) as src_thin:
            with ps.new_thin(src_pool, thin_size, 1) as dest_thin:
                try:
                    process.run(f"thin_migrate --source-dev {src_thin} --dest-dev {dest_thin}")
                except subprocess.CalledProcessError:
                    pass
                except:
                    raise
                else:
                    raise Exception("command succeeded without error")

        src_pool.message(0, f"release_metadata_snap")


def t_output_none_block_device(fix):
    data_dev = fix.cfg("data_dev")
    thin_size = min(units.gig(1), utils.dev_size(data_dev) // 2)

    with standard_pool(fix, block_size = 128, zero = True) as src_pool:
        with ps.new_thin(src_pool, thin_size, 0, read_only = True) as src_thin:
            src_pool.message(0, f"reserve_metadata_snap")
            process.run(f"truncate migrate_dest --size {thin_size * 512}")

            try:
                process.run(f"thin_migrate --source-dev {src_thin} --dest-dev migrate_dest")
            except subprocess.CalledProcessError:
                pass
            except:
                raise
            else:
                raise Exception("command succeeded without error")

            process.run(f"unlink migrate_dest")
            src_pool.message(0, f"release_metadata_snap")


def t_output_unsupported_file_type(fix):
    data_dev = fix.cfg("data_dev")
    thin_size = min(units.gig(1), utils.dev_size(data_dev) // 2)

    with standard_pool(fix, block_size = 128, zero = True) as src_pool:
        with ps.new_thin(src_pool, thin_size, 0, read_only = True) as src_thin:
            src_pool.message(0, f"reserve_metadata_snap")

            try:
                process.run(f"thin_migrate --source-dev {src_thin} --dest-file /dev/null")
            except subprocess.CalledProcessError:
                pass
            except:
                raise
            else:
                raise Exception("command succeeded without error")

            src_pool.message(0, f"release_metadata_snap")


def t_output_device_size_differs(fix):
    data_dev = fix.cfg("data_dev")
    thin_size = min(units.gig(1), utils.dev_size(data_dev) // 2)

    with standard_pool(fix, block_size = 128, zero = True) as src_pool:
        with ps.new_thin(src_pool, thin_size, 0, read_only = True) as src_thin:
            with ps.new_thin(src_pool, thin_size // 2, 1) as dest_thin:
                src_pool.message(0, f"reserve_metadata_snap")

                try:
                    process.run(f"thin_migrate --source-dev {src_thin} --dest-dev {dest_thin}")
                except subprocess.CalledProcessError:
                    pass
                except:
                    raise
                else:
                    raise Exception("command succeeded without error")

                src_pool.message(0, f"release_metadata_snap")


def t_output_device_size_differs_in_file_mode(fix):
    data_dev = fix.cfg("data_dev")
    thin_size = min(units.gig(1), utils.dev_size(data_dev) // 2)

    with standard_pool(fix, block_size = 128, zero = True) as src_pool:
        with ps.new_thin(src_pool, thin_size, 0, read_only = True) as src_thin:
            with ps.new_thin(src_pool, thin_size // 2, 1) as dest_thin:
                src_pool.message(0, f"reserve_metadata_snap")

                try:
                    process.run(f"thin_migrate --source-dev {src_thin} --dest-file {dest_thin}")
                except subprocess.CalledProcessError:
                    pass
                except:
                    raise
                else:
                    raise Exception("command succeeded without error")

                src_pool.message(0, f"release_metadata_snap")


def register(tests):
    tests.register_batch(
        "/thin_migrate/unit",
        [
            ("insufficient_buffer_size", t_insufficient_buffer_size),
            ("input_none_thin_device", t_input_none_thin_device),
            ("device_not_present_in_metadata_snap", t_device_not_present_in_metadata_snap),
            ("output_none_block_device", t_output_none_block_device),
            ("output_unsupported_file_type", t_output_unsupported_file_type),
            ("output_device_size_differs", t_output_device_size_differs),
            ("output_device_size_differs_in_file_mode", t_output_device_size_differs_in_file_mode),
        ],
    )
