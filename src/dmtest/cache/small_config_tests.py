import unittest
import dmtest.units as units

from dmtest.cache_stack import ManagedCacheStack, CachePolicy

#----------------------------------------------------------------

def t_small_config(fix):
    fast_dev = fix.cfg("metadata_dev")
    origin_dev = fix.cfg("data_dev")
    cache_dev = fix.cfg("cache_dev")
    policy_name = fix.cfg("cache_policy")

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

#----------------------------------------------------------------

def register(tests):
    tests.register_batch(
        "/cache/creation/",
        [
            ("small_config", t_small_config),
        ],
    )
