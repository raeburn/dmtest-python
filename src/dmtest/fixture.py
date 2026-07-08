import dmtest.config as config


class Fixture:

    def __init__(self):
        self._cfg = config.read_config()

    def cfg(self, key):
        return self._cfg.get(key)
