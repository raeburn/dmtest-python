import dmtest.vdo.compress_tests as vdo_compress
import dmtest.vdo.creation_tests as vdo_creation
import dmtest.vdo.dedupe_tests as vdo_dedupe
import dmtest.vdo.full_tests as vdo_full
import dmtest.vdo.load_failure_tests as vdo_load_failure
import dmtest.vdo.recovery_tests as vdo_recovery

def register(tests):
    vdo_creation.register(tests)
    vdo_dedupe.register(tests)
    vdo_compress.register(tests)
    vdo_full.register(tests)
    vdo_load_failure.register(tests)
    vdo_recovery.register(tests)
