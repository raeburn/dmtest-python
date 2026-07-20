"""VDO thread configuration tests.

Verifies that VDO creates the correct kernel threads for different
thread/zone count configurations, including the single-thread mode
(all zone counts zero) and multi-thread mode (all zone counts nonzero).

Each thread parameter is varied independently while the others stay at
defaults, and the full set of thread counts is verified every time.
"""
from dmtest.vdo.utils import standard_vdo
from dmtest import process
import logging as log
import re

DEFAULT_BIO = 4
DEFAULT_ACK = 1
DEFAULT_CPU = 1


def get_vdo_threads():
    """Return the comm names of all VDO worker threads."""
    _, stdout, _ = process.run("ps -eo comm")
    return [line.strip() for line in stdout.splitlines()
            if re.match(r"vdo\d+:", line.strip())]


def get_queue_names(threads):
    """Extract queue names from full thread comm names.

    "vdo0:logQ1" -> "logQ1"
    """
    return [t.partition(":")[2] for t in threads]


def count_matching(names, prefix):
    return sum(1 for n in names if n.startswith(prefix))


def assert_thread_count(names, prefix, expected):
    actual = count_matching(names, prefix)
    assert actual == expected, (
        f"Expected {expected} '{prefix}' thread(s), found {actual}: "
        f"{[n for n in names if n.startswith(prefix)]}"
    )


def assert_no_threads(names, prefix):
    matching = [n for n in names if n.startswith(prefix)]
    assert not matching, f"Expected no '{prefix}' threads, found: {matching}"


def check_threads(expected):
    """Verify all VDO thread counts match expected values."""
    threads = get_vdo_threads()
    names = get_queue_names(threads)
    log.info(f"VDO threads: {names}")

    for prefix, count in expected.items():
        if count == 0:
            assert_no_threads(names, prefix)
        else:
            assert_thread_count(names, prefix, count)


def single_mode_expected(**overrides):
    """Expected thread counts for single mode (all zone counts zero)."""
    expected = {
        "reqQ": 1,
        "logQ": 0, "physQ": 0, "hashQ": 0,
        "journalQ": 0, "packerQ": 0,
        "dedupeQ": 1,
        "cpuQ": DEFAULT_CPU,
        "ackQ": DEFAULT_ACK,
        "bioQ": DEFAULT_BIO,
    }
    expected.update(overrides)
    return expected


def multi_mode_expected(**overrides):
    """Expected thread counts for multi mode (zone counts > 0).

    Defaults to logical=1, physical=1, hash=1 with default bio/ack/cpu.
    """
    expected = {
        "reqQ": 0,
        "logQ": 1, "physQ": 1, "hashQ": 1,
        "journalQ": 1, "packerQ": 1,
        "dedupeQ": 1,
        "cpuQ": DEFAULT_CPU,
        "ackQ": DEFAULT_ACK,
        "bioQ": DEFAULT_BIO,
    }
    expected.update(overrides)
    return expected


def t_single_thread_mode(fix):
    """Default config (all zone counts zero) uses a single 'reqQ' thread."""
    with standard_vdo(fix) as vdo:
        check_threads(single_mode_expected())


def t_thread_counts(fix):
    """Vary each thread parameter independently and verify all thread counts.

    For zone parameters (logical, physical, hash), the other two are held
    at 1 since they must all be nonzero together.  For independent
    parameters (bio, ack, cpu), they are tested in multi mode so that the
    full set of thread types is present for verification.
    """
    # Format once, reuse for all variations.
    with standard_vdo(fix) as vdo:
        pass

    cases = [
        # Zone parameters — vary one, hold the other two at 1.
        ("logical=3",
         dict(logical=3, physical=1, hash=1),
         multi_mode_expected(logQ=3)),
        ("physical=4",
         dict(logical=1, physical=4, hash=1),
         multi_mode_expected(physQ=4)),
        ("hash=3",
         dict(logical=1, physical=1, hash=3),
         multi_mode_expected(hashQ=3)),

        # Independent parameters — use minimal multi-mode baseline.
        ("bio=2",
         dict(logical=1, physical=1, hash=1, bio=2),
         multi_mode_expected(bioQ=2)),
        ("ack=3",
         dict(logical=1, physical=1, hash=1, ack=3),
         multi_mode_expected(ackQ=3)),
        ("cpu=2",
         dict(logical=1, physical=1, hash=1, cpu=2),
         multi_mode_expected(cpuQ=2)),
    ]

    for desc, opts, expected in cases:
        log.info(f"Testing: {desc}")
        with standard_vdo(fix, format=False, **opts) as vdo:
            check_threads(expected)


def register(tests):
    tests.register_batch(
        "/vdo/thread_config/",
        [
            ("single_thread_mode", t_single_thread_mode),
            ("thread_counts", t_thread_counts),
        ],
    )
