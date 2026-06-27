import dmtest.process as process
from dmtest.utils import dev_size
import dmtest.vdo.vdo_stack as vs
import dmtest.vdo.stats as stats
import dmtest.vdo.status as status

import code
import json
import logging as log
from math import ceil
import os
import tempfile
import time

# dmtest.units.kilo etc count in sectors, not bytes
kB = 1024
MB = 1024 * kB
GB = 1024 * MB

BLOCK_SIZE = 4 * kB

fio_config_template = """
[stuff]
randrepeat=1
ioengine=libaio
bs=4k
rw=write
direct=1
scramble_buffers=1
refill_buffers=1
buffer_compress_percentage={compress}
buffer_compress_chunk=4k
filename={filename}
iodepth=128
offset={offset}
end_fsync=0
{maybe_verify}
group_reporting=1
"""

def standard_stack(fix, **opts):
    return vs.VDOStack(fix.cfg("data_dev"), **opts)

def standard_vdo(fix, **opts):
    stack = standard_stack(fix, **opts)
    return stack.activate()

def wait_for_index(dev):
    count = 0;
    while (count < 30 and status.vdo_status(dev)["index-state"] != "online"):
        count += 1
        time.sleep(1)
    if status.vdo_status(dev)["index-state"] != "online":
        raise AssertionError("VDO not online within 30 seconds")

def fsync(dev):
    """Sync the specified device or file."""
    with open(dev, 'w') as thing:
        os.fsync(thing.fileno())

def run_fio_with_config(fio_config, raise_on_fail=True):
    """Run fio with the specified config file content.

    On success, return the parsed statistics output. On failure, throw
    an exception if raise_on_fail or return the stderr content.
    """
    with tempfile.NamedTemporaryFile('w') as conf:
        log.info("fio config:\n" + fio_config)
        conf.write(fio_config)
        conf.flush()
        with tempfile.NamedTemporaryFile('w') as out:
            cmd = f"fio {conf.name} --output={out.name} --output-format=json+"
            results = process.run(cmd, raise_on_fail = raise_on_fail)
            (return_code, stdout, stderr) = results
            if return_code:
                return stderr
            with open(out.name, 'r') as fio_out_file:
                log.info(fio_out_file.read())
                fio_out_file.seek(0)
                fio_out = json.load(fio_out_file)
                written = fio_out['jobs'][0]['write']['io_bytes'] # bytes
                duration = fio_out['jobs'][0]['write']['runtime'] # msec
                log.info(f"wrote {written} bytes in {duration} msec")
                return fio_out

def run_fio(dev, size, offset, verify = False, stats = True, compression = 0, randseed = 1,
            duration = 0, raise_on_fail = True):
    """Run fio with the specified values.

    On success, return the parsed statistics output if stats is
    true. On failure, throw an exception if raise_on_fail or return
    the stderr content.
    """
    maybe_verify = "verify_only" if verify else ""
    fio_config = fio_config_template.format(offset=offset,
                                            compress=compression,
                                            filename=str(dev),
                                            maybe_verify=maybe_verify)
    if size:
        fio_config += f"\nsize={size}"
    if duration:
        fio_config += f"\nruntime={duration}"
    fio_stats = run_fio_with_config(fio_config, raise_on_fail = raise_on_fail)
    if stats:
        return fio_stats

block_map_entries_per_page = 812

def populate_block_map(vdo_dev):
    """Make sure the VDO device's block map has been fully allocated,
    by writing zero blocks every so often to force population of the
    tree.

    """
    vdo_size_sectors = dev_size(vdo_dev)
    vdo_size_blocks = int(vdo_size_sectors / 8)
    stats.vdo_stats(vdo_dev)
    bm_leaf_pages = ceil(vdo_size_blocks / block_map_entries_per_page)
    filename = str(vdo_dev)
    populate_block_map_fio_config = f"""
[stuff]
bs=4k
direct=0
end_fsync=1
filename={filename}
group_reporting=1
iodepth=128
ioengine=libaio
rw=write:{4*(block_map_entries_per_page-1)}k
io_size={bm_leaf_pages*4}k
size={int(vdo_size_sectors/2)}k
thread=1
zero_buffers=1
"""
    run_fio_with_config(populate_block_map_fio_config)

# Useful while debugging tests: suspend execution and let the
# developer examine Python variables, system state, etc.
def repl(my_locals):
    """Invoke a Python interactive session with access to the supplied
    local variables. Intended for debugging tests, to pause execution
    and allow examination of the test device and a test's internal
    state.

    Should be invoked as: repl(locals()).

    On exiting the interactive session with ^D, returns to the caller.
    """
    variables = globals().copy()
    variables.update(my_locals)
    code.InteractiveConsole(variables).interact()
