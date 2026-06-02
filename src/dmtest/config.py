import tomllib


# Linux reordered my nvme drives once and I ran tests across
# /boot.  This check tries to avoid that.  The exception is
# virt devices, which don't seem to have an id.
def check_dev(cfg, name):
    if cfg.get("disable_by_id_check", False):
        return

    value = cfg[name]
    if not (
        value.startswith("/dev/vd") or value.startswith("/dev/mapper/")
    ) and not value.startswith("/dev/disk/by-id/"):
        raise ValueError(f"config value '{name}' does not begin with /dev/disk/by-id")


def validate(cfg):
    check_dev(cfg, "metadata_dev")
    check_dev(cfg, "data_dev")


def read_config(path="config.toml"):
    with open(path, "rb") as f:
        config = tomllib.load(f)
        validate(config)
        return config
