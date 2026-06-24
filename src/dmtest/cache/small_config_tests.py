import unittest
import dmtest.units as units

from dmtest.cache_stack import ManagedCacheStack, CachePolicy

#----------------------------------------------------------------

def test_small_config(fix):
    cfg = fix.cfg
    fast_dev = cfg["metadata_dev"]
    origin_dev = cfg["data_dev"]
    cache_dev = cfg.get("cache_dev", None)
    policy_name = cfg.get("cache_policy", "smq")

    stack = ManagedCacheStack(
        fast_dev,
        origin_dev,
        cache_dev = cache_dev,
        format = True,
        metadata_size = units.meg(4),
        block_size = units.kilo(32),
        cache_size = units.kilo(50),
        target_len = units.kilo(50),
        policy = CachePolicy(policy_name),
    )
    with stack.activate():
        pass

