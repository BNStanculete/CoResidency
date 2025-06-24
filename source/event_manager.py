"""
This module provides a simple event bus system using the pyee libary
to allow different parts of the application to communicate through events.
"""

from logging import Logger
from threading import Lock
from pyee import EventEmitter
from source.meta.singleton import SingletonMeta

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
        """
        Registers a handler for a specific event.
        
        :param event_name: The name of the event to listen for.
        :param handler: The function to call when the event is emitted.
        """
        with self._lock:
            self.__logger.debug("Registered %s for the event %s.", handler.__name__, event_name)
            self.__emitter.on(event_name, handler)

    def emit(self, event_name, *args, **kwargs):
        """
        Emits an event with the given name and optional arguments.
        """
        with self._lock:
            self.__logger.debug("Emitting event %s", event_name)
            self.__emitter.emit(event_name, *args, **kwargs)
