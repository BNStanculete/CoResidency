"""
Provides a thread-safe singleton meta class.

This class only ensures that derived classes have a single instance, but does not keep
said instance alive. It is the responsiblity of the instantiator to keep the instance.
"""
from threading import Lock

class SingletonMeta(type):
    """A thread-safe singleton meta class."""
    _instances = {}
    _lock = Lock()

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    instance = super().__call__(*args, **kwargs)
                    cls._instances[cls] = instance
        return cls._instances[cls]
