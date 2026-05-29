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

from dmtest.blk_archive.common import BlkArchive

# --------------------------------


def t_rolling_snaps(fix):
    fs_type = fs.Ext4
    thin_size = units.gig(8)

    archive_dir = "./test-archive"
    process.run(f"rm -rf {archive_dir}")
    archive = BlkArchive(archive_dir)

    ids = [0]

    kernel_source = os.getenv("DMTEST_KERNEL_SOURCE", "../linux")

    log.info(f"using {kernel_source} as linux kernel directory")
    
    with standard_pool(fix) as pool:
        with ps.new_thin(pool, thin_size, 0) as thin:
            linux_fs = fs_type(thin)
            linux_fs.format()

            with linux_fs.mount_and_chdir("./kernel_builds", discard=False):
                repo = git.Git.clone(kernel_source, "linux")
                repo.checkout(git.TAGS[0])

                index = 1

                # archive this via a snap
                with ps.new_snap(pool, thin_size, index, 0, thin) as snap:
                    id = archive.pack(snap)
                    ids.append(id)
                    archive.verify(snap)
                    index += 1

                # now we start archiving deltas
                for tag in git.TAGS[1:8]:
                    repo.checkout(tag)

                    with thin.pause():
                        pool.message(0, f"create_snap {index} 0")

                    with ps.thin(pool, thin_size, index - 1) as old_snap:
                        with ps.thin(pool, thin_size, index) as new_snap:
                            id = archive.pack_delta(old_snap, ids[index - 1], new_snap)
                            ids.append(id)
                            archive.verify(new_snap)

                    index += 1


# --------------------------------


def register(tests):
    tests.register_batch(
        "/blk-stash/",
        [
            ("rolling-snaps", t_rolling_snaps, [], reg.check_linux_repo),
        ],
    )
