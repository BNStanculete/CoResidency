from logging import Logger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from json import load, JSONDecodeError
from threading import Thread
from magic import Magic

from os.path import dirname, abspath, isabs

from eventManager import EventManager

class ConfigurationManager(FileSystemEventHandler):
    def __init__(self, configPath: str = "configuration.json", eventManager = EventManager()):
        """
        Intializes the ConfigurationManager with a path to the configuration file.

        :param configPath: Path to the configuration file. If not absolute, it will be converted to an absolute path.
        :param eventManager: An instance of EventManager to emit events when the configuration is reloaded. Can be replaced with a custom event manager if needed.
        """
        if not isabs(configPath):
            self.__configPath = abspath(configPath)
        else:
            self.__configPath = configPath
        self.__watchDir = dirname(self.__configPath)
        self.__eventManager = eventManager
        self.__configuration = {}
        self.__logger = Logger("ConfigurationManager")

        # Launch observer
        self.__observer = Observer()
        self.__observer.schedule(self, path=self.__watchDir, recursive=False)
        self.__observer_thread = Thread(target=self.__observer.start)
        self.__observer_thread.daemon = True
        self.__observer_thread.start()

        self.__buildConfiguration()

    def stop(self):
        """
        Stops the watchdog observer thread to allow graceful shutdown.
        """
        self.__observer.stop()
        self.__observer.join()

    def on_modified(self, event):
        """
        Triggered when the configuration file is modified.
        
        On trigger, it re-reads the configuration and emits a reload event to let interested parties know
        the changes in the configuration.
        """
        if abspath(event.src_path) == self.__configPath:
            self.__logger.info("Configuration file changed. Reloading...")
            self.__buildConfiguration()
            self.__eventManager.emit(self.__configuration["EventNames"]["ConfigurationReloaded"], self.__configuration)

    def __buildConfiguration(self):
        # Local object so that JSON root node is released after extraction
        parser = ConfigurationManager.JSONParser(self.__configPath)
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
        for key in parser["Thresholds"].keys():
            self.__configuration["Thresholds"][key] = parser["Thresholds"][key]["Value"]
        
        # Initialize performance configuration
        for key in parser["Performance"].keys():
            self.__configuration[key] = parser["Performance"][key]["Value"]

        # Initialize event names
        for key in parser["EventNames"].keys():
            self.__configuration["EventNames"][key] = parser["EventNames"][key]["Value"]

    class JSONParser:
        """
        Inner class to handle JSON parsing and configuration extraction.
        """
        def __init__(self, configFile: str = ""):
            self.__logger.debug(message=f"Creating JSONParser for file: {configFile}")
            self.__loadedJSON = False

            mime = Magic(mime=True)
            if not mime.from_file(configFile) in ["application/json", "text/plain"]:
                self.__logger.error(f"Invalid file type for configuration: {configFile} !")
                return

            try:
                with open(configFile, "r") as file:
                    if not file.readable():
                        self.__logger.error(f"Configuration file is not readable: {configFile} !")
                        return

                    self.__config = load(file)
                    self.__loadedJSON = True
            except JSONDecodeError as err:
                self.__logger.error(f"Error parsing configuration file: {configFile} !")
                self.__logger.error(f"{err.msg} at line {err.lineno} column {err.colno}.")

        def __getitem__(self, name: str = ""):
            """
            Retrieves a specific configuration option.
            """
            if not self.__loadedJSON:
                self.__logger.warning(message="Attempt to load data from missing configuration.")
                return None

            if name not in self.__config:
                self.__logger.warning(f"Invalid configuration option: {name}")
                return None
            
            return self.__config[name]
