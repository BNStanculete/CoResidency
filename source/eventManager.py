from meta.singleton import SingletonMeta
from pyee import EventEmitter
from logging import Logger
from threading import Lock

class EventManager(metaclass=SingletonMeta):
    """
    EventManager is a singleton that propagates events across the system.
    """
    def __init__(self):
        self.__emitter = EventEmitter()
        self.__logger = Logger("EventManager")
        self._lock = Lock()
        self.__logger.info("Initialized EventManager.")
    
    def on(self, event_name, handler):
        with self._lock:
            self.__logger.debug(f"Registered {handler} for the event {event_name}.")
            self.__emitter.on(event_name, handler)

    def emit(self, event_name, *args, **kwargs):
        with self._lock:
            self.__logger.debug(f"Emitting event {event_name}")
            self.__emitter.emit(event_name, *args, **kwargs)
