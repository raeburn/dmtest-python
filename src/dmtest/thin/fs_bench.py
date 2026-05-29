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
import dmtest.tvm as tvm

import os
import threading
import logging as log
import time

#---------------------------------

fio_config = """
[global]
randrepeat=1
ioengine=libaio
bs=4k
ba=4k
size=5G
direct=1
gtod_reduce=1
norandommap
iodepth=64
numjobs=16
runtime=60
 
[mix]
rw=randrw
stonewall
timeout=30
"""

def run_fio(dev, fs_type, fio_config, out_file):
    # convert out_file to be absolute since we're about to chdir
    out_file = os.path.abspath(out_file)

    fs = fs_type(dev)
    fs.format(discard=True)

    with fs.mount_and_chdir("./mnt", discard=False):
        with open("fio.config", "w") as f:
            f.write(fio_config)
        process.run(f"fio fio.config --output={out_file}")

def t_fio_thick(fix):
    size = units.gig(90)

    vm = tvm.VM()
    vm.add_allocation_volume(fix.cfg("data_dev"))
    vm.add_volume(tvm.LinearVolume("thick", size))

    with dmdev.dev(vm.table("thick")) as thick:
        time.sleep(1)

        outfile = "fio.out"
        run_fio(thick, fs.Ext4, fio_config, outfile)        

def t_fio_thin(fix):
    size = units.gig(90)

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, size, 0) as thin:
            time.sleep(1)
            outfile = "fio.out"
            run_fio(thin, fs.Ext4, fio_config, outfile)

def t_fio_thin_preallocated(fix):
    size = units.gig(90)

    with standard_pool(fix) as pool:
        with ps.new_thin(pool, size, 0) as thin:
            utils.wipe_device(thin)
            outfile = "fio.out"
            run_fio(thin, fs.Ext4, fio_config, outfile)

def register(tests):
    tests.register_batch(
        "/thin/fs-bench/",
        [
            ("fio/thick", t_fio_thick),
            ("fio/thin", t_fio_thin),
            ("fio/thin-preallocated", t_fio_thin_preallocated),
        ],
    )
