import sys
import tomllib


_CONFIG_KEYS = {
    "metadata_dev":        ("devices", None),
    "data_dev":            ("devices", None),
    "cache_dev":           ("devices", None),
    "disable_by_id_check": ("devices", False),
    "tags":                ("filter", None),
    "cache_policy":        ("run", "smq"),
}


class Config:
    def __init__(self, raw):
        self._raw = raw

    def get(self, key):
        entry = _CONFIG_KEYS.get(key)
        if entry is None:
            raise KeyError(key)
        header, default = entry
        return self._raw.get(header, {}).get(key, default)

    def _check_layout(self):
        known_headers = {header for header, _ in _CONFIG_KEYS.values()}

        for header, table in self._raw.items():
            if header not in known_headers:
                print(f"warning: unexpected top-level entry '{header}'", file=sys.stderr)
                continue
            if not isinstance(table, dict):
                raise TypeError(f"'{header}' must be a table ([{header}])")

            for key in table:
                entry = _CONFIG_KEYS.get(key)
                if entry is None:
                    print(f"warning: unknown config key '{key}'", file=sys.stderr)
                    continue
                if entry[0] != header:
                    raise ValueError(
                            f"misplaced key '{key}': expected in [{entry[0]}], "
                            f"found in [{header}]"
                    )

    def _check_required_keys(self):
        for key in ("metadata_dev", "data_dev"):
            if self.get(key) is None:
                header = _CONFIG_KEYS[key][0]
                raise ValueError(f"missing required key '{key}' in [{header}]")

    # Device names like /dev/nvme0 can change across reboots, so we require
    # /dev/disk/by-id/ paths to prevent testing the wrong device.
    # This check does not apply to virtual devices (/dev/vd*) as they typically
    # lack /dev/disk/by-id/ entries by default. DM devices (/dev/mapper/*)
    # are skipped as they already use stable names.
    def _check_dev(self, key):
        value = self.get(key)
        if not (
            value.startswith("/dev/vd") or value.startswith("/dev/mapper/")
        ) and not value.startswith("/dev/disk/by-id/"):
            raise ValueError(f"config value '{key}' does not begin with /dev/disk/by-id/")

    def validate(self):
        if "devices" not in self._raw:
            raise ValueError(
                "config.toml must have a [devices] table; "
                "see config.toml.example for the expected format"
            )

        self._check_layout()
        self._check_required_keys()

        if not self.get("disable_by_id_check"):
            self._check_dev("metadata_dev")
            self._check_dev("data_dev")


def read_config(path="config.toml"):
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    config = Config(raw)
    config.validate()
    return config
