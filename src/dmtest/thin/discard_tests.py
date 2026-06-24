from dmtest.thin.utils import standard_stack, standard_pool
import dmtest.blktrace as bt
import dmtest.device_mapper.dev as dmdev
import dmtest.pool_stack as ps
import dmtest.process as process
import dmtest.tvm as tvm
import dmtest.units as units
import dmtest.utils as utils
import dmtest.exceptions as exceptions

from pathlib import Path
import xml.etree.ElementTree as ET
import xml.dom.minidom


def read_param(dev, param):
    with open(f"/sys/block/{dev}/queue/discard_{param}", "r") as file:
        line = file.readline()

    return int(line.strip())


class DiscardLimits:
    def __init__(self, dev):
        self.dev = Path(dev).resolve().name
        self.granularity = read_param(self.dev, "granularity")
        self.max_bytes = read_param(self.dev, "max_bytes")
        self.supported = self.max_bytes > 0


def ensure_discardable(dev):
    limits = DiscardLimits(dev)
    if not limits.supported:
        raise exceptions.MissingDependency("data dev is not discardable")


def unmapping_check(_discardable: bool, _passdown: bool):
    pass


def read_metadata(metadata_dev):
    (_, metadata, _) = process.run(f"thin_dump {metadata_dev}")
    return ET.fromstring(metadata)


def write_metadata(file, tree: ET.ElementTree):
    tree.write(file, encoding="utf-8", xml_declaration=False)


def test_blktrace(fix):
    with standard_pool(fix, block_size=524288) as pool:
        with ps.new_thin(pool, units.gig(4), 0) as thin:
            trace = bt.BlkTrace([thin.path])
            with trace:
                utils.wipe_device(thin)
    tree = read_metadata(fix.cfg["metadata_dev"])

    print(f"{tree.attrib}")

    # FIXME: we need to handle shared mappings
    for dev in tree:
        print(dev)


def test_unmaps_with_passdown_discardable_pool(fix):
    with standard_pool(fix) as pool:
        with ps.new_thin(pool, units.gig(4), 0) as thin:
            trace = bt.BlkTrace([thin.path])
            with trace:
                utils.wipe_device(thin)


def test_xml(fix):
    with standard_pool(fix) as pool:
        with ps.new_thin(pool, units.gig(4), 0) as thin:
            utils.wipe_device(thin)
