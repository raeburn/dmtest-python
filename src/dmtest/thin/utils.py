import dmtest.pool_stack as ps
import dmtest.utils as utils


def standard_stack(fix, **opts):
    if "data_size" not in opts:
        opts["data_size"] = utils.dev_size(fix.cfg("data_dev"))
    return ps.PoolStack(fix.cfg("data_dev"), fix.cfg("metadata_dev"), **opts)


def standard_pool(fix, **opts):
    stack = standard_stack(fix, **opts)
    return stack.activate()
