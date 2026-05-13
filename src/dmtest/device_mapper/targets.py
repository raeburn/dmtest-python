from dmtest.process import run


class Target:
    def __init__(self, t, sector_count, *args):
        self.type = t
        self.sector_count = sector_count
        self.args = args

    def post_remove_check(self):
        pass

class DelayTarget(Target):
    def __init__(
        self,
        sector_count,
        dev,
        offset,
        delay_ms,
        write_dev=None,
        write_offset=None,
        write_delay=None,
        flush_dev=None,
        flush_offset=None,
        flush_delay=None,
    ):
        args = [dev, offset, delay_ms]
        if write_dev is not None:
            args += [write_dev, write_offset, write_delay]
            if flush_dev is not None:
                args += [flush_dev, flush_offset, flush_delay]
        super().__init__("delay", sector_count, *args)


class ErrorTarget(Target):
    def __init__(self, sector_count):
        super().__init__("error", sector_count)


class FlakeyTarget(Target):
    def __init__(
        self,
        sector_count,
        dev,
        offset=0,
        up_interval=60,
        down_interval=0,
        drop_writes=False,
    ):
        extra_opts = []
        if drop_writes:
            extra_opts.append("drop_writes")

        super().__init__(
            "flakey",
            sector_count,
            dev,
            offset,
            up_interval,
            down_interval,
            len(extra_opts),
            *extra_opts,
        )


class LinearTarget(Target):
    def __init__(self, sector_count, dev, offset):
        super().__init__("linear", sector_count, dev, offset)


class StripeTarget(Target):
    def __init__(self, sector_count, chunk_size, *pairs):
        super().__init__("striped", sector_count, chunk_size, *sum(pairs, ()))


class ThinPoolTarget(Target):
    def __init__(
        self,
        sector_count,
        metadata_dev,
        data_dev,
        block_size,
        low_water_mark,
        zero=True,
        discard=True,
        discard_pass=True,
        read_only=False,
        error_if_no_space=False,
    ):
        extra_opts = []

        if not zero:
            extra_opts.append("skip_block_zeroing")
        if not discard:
            extra_opts.append("ignore_discard")
        if not discard_pass:
            extra_opts.append("no_discard_passdown")
        if read_only:
            extra_opts.append("read_only")
        if error_if_no_space:
            extra_opts.append("error_if_no_space")

        super().__init__(
            "thin-pool",
            sector_count,
            metadata_dev,
            data_dev,
            block_size,
            low_water_mark,
            len(extra_opts),
            *extra_opts,
        )
        self.metadata_dev = metadata_dev

    def post_remove_check(self):
        run(f"thin_check {self.metadata_dev}")


class ThinTarget(Target):
    def __init__(self, sector_count, pool, id, origin=None):
        if origin is not None:
            super().__init__("thin", sector_count, pool, id, origin)
        else:
            super().__init__("thin", sector_count, pool, id)


class CacheTarget(Target):
    def __init__(
        self,
        sector_count,
        metadata_dev,
        cache_dev,
        origin_dev,
        block_size,
        features,
        policy,
        policy_args,
    ):
        args = (
            [metadata_dev, cache_dev, origin_dev, block_size, len(features)]
            + [str(f) for f in features]
            + [policy, 2 * len(policy_args)]
            + [str(k) + " " + str(v) for k, v in policy_args.items()]
        )

        super().__init__("cache", sector_count, *args)
        self.metadata_dev = metadata_dev

    def post_remove_check(self):
        run(f"cache_check {self.metadata_dev}")


class WriteCacheTarget(Target):
    def __init__(self, sector_count, cache_dev, origin_dev, block_size):
        super().__init__(
            "writecache", sector_count, "s", origin_dev, cache_dev, block_size, 0
        )


class EraTarget(Target):
    def __init__(self, sector_count, metadata_dev, origin_dev, block_size):
        super().__init__("era", sector_count, metadata_dev, origin_dev, block_size)
        self.metadata_dev = metadata_dev

    def post_remove_check(self):
        run(f"era_check {self.metadata_dev}")


class FakeDiscardTarget(Target):
    def __init__(
        self,
        sector_count,
        dev,
        offset,
        granularity,
        max_discard,
        no_discard_support=False,
        discard_zeroes=False,
    ):
        extra_opts = []

        if no_discard_support:
            extra_opts.append("no_discard_support")
        if discard_zeroes:
            extra_opts.append("discard_zeroes_data")

        super().__init__(
            "fake-discard",
            sector_count,
            dev,
            offset,
            granularity,
            max_discard,
            len(extra_opts),
            *extra_opts,
        )


class BufioTestTarget(Target):
    def __init__(self, sector_count, dev):
        super().__init__("bufio_test", sector_count, dev)


class VDOTarget(Target):
    def __init__(
        self,
        sector_count,
        dev,
        physical_blocks,
        mode,
        block_map_cache,
        block_map_period,
        opts,
    ):

        args = (
            ["V4", dev, physical_blocks, mode, block_map_cache, block_map_period]
            + [str(k) + " " + str(v) for k, v in opts.items()]
        )

        super().__init__("vdo", sector_count, *args)

