"""
Proivdes the ConfigurationManager class to manage application configuration.
"""

from os.path import dirname, abspath, isabs
from logging import Logger
from json import load, JSONDecodeError
from threading import Thread
from magic import Magic

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from source.event_manager import EventManager

class ConfigurationManager(FileSystemEventHandler):
    """
    Manages the configuration of the application by reading from a JSON file.

    It watches for changes in the configuration file and reloads the configuration
    when the file is modified. It also provides a structured way to access configuration.
    """
    def __init__(self, config_path: str = "configuration.json", event_manager = EventManager()):

        """
        Intializes the ConfigurationManager with a path to the configuration file.

        :param config_path: Path to the configuration file. If not absolute, it will be converted
                            to an absolute path.
        :param event_manager: An instance of EventManager to emit events when the configuration
                            is reloaded. Can be replaced with a custom event manager if needed.
        """
        if not isabs(config_path):
            self.__config_path = abspath(config_path)
        else:
            self.__config_path = config_path
        self.__watch_dir = dirname(self.__config_path)
        self.__event_manager = event_manager
        self.__configuration = {}
        self.__logger = Logger("ConfigurationManager")

        # Launch observer
        self.__observer = Observer()
        self.__observer.schedule(self, path=self.__watch_dir, recursive=False)
        self.__observer_thread = Thread(target=self.__observer.start)
        self.__observer_thread.daemon = True
        self.__observer_thread.start()

        self.__build_configuration()

    def stop(self):
        """
        Stops the watchdog observer thread to allow graceful shutdown.
        """
        self.__observer.stop()
        self.__observer.join()

    def on_modified(self, event):
        """
        Triggered when the configuration file is modified.
        
        On trigger, it re-reads the configuration and emits a reload event
        to let interested parties know of the changes in the configuration.
        """
        if abspath(event.src_path) == self.__config_path:
            self.__logger.info("Configuration file changed. Reloading...")
            self.__build_configuration()
            self.__event_manager.emit(
                self.__configuration["EventNames"]["ConfigurationReloaded"], self.__configuration)

    def __build_configuration(self):
        # Local object so that JSON root node is released after extraction
        parser = ConfigurationManager.JSONParser(self.__config_path)
        self.__configuration = {
            "Mitigation": None if not parser["EnableMitigation"]
                               else {
                                    "FlagsBeforeActivation": parser["MitigationConfiguration"]
                                                                   ["FlagsBeforeActivation"]
                                                                   ["Value"],
                                    "DeflagsBeforeDeactivation": parser["MitigationConfiguration"]
                                                                       ["DeflagsBeforeDeactivation"]
                                                                       ["Value"],
                               },
            "Thresholds": { },
            "EventNames": { }
        }

        # Initialize thresholds
        for key in parser["Thresholds"]:
            self.__configuration["Thresholds"][key] = parser["Thresholds"][key]["Value"]

        # Initialize performance configuration
        for key in parser["Performance"]:
            self.__configuration[key] = parser["Performance"][key]["Value"]

        # Initialize event names
        for key in parser["EventNames"]:
            self.__configuration["EventNames"][key] = parser["EventNames"][key]["Value"]

    class JSONParser:
        """
        Inner class to handle JSON parsing and configuration extraction.
        """
        def __init__(self, config_file: str = ""):
            self.__logger = Logger("ConfigurationManager.JSONParser")
            self.__logger.debug("Creating JSONParser for file: %s", config_file)
            self.__loaded_json = False

            mime = Magic(mime=True)
            if mime.from_file(config_file) not in ["application/json", "text/plain"]:
                self.__logger.error("Invalid file type for configuration: %s!", config_file)
                return

            try:
                with open(config_file, "r", encoding='utf-8') as file:
                    if not file.readable():
                        self.__logger.error("Configuration file is not readable: %s!", config_file)
                        return

                    self.__config = load(file)
                    self.__loaded_json = True
            except JSONDecodeError as err:
                self.__logger.error("Error parsing configuration file: %s!", config_file)
                self.__logger.error("%s at line %d column %d.", err.msg, err.lineno, err.colno)

        def __getitem__(self, name: str = ""):
            """
            Retrieves a specific configuration option.
            """
            if not self.__loaded_json:
                self.__logger.warning("Attempt to load data from missing configuration.")
                return None

            if name not in self.__config:
                self.__logger.warning("Invalid configuration option: %s", name)
                return None

            return self.__config[name]
