"""Backend-independent receiver errors; details must not reach HTTP responses."""

class StorageUnavailable(RuntimeError):
    pass
