from logging import Logger
from collections import deque
from threading import Lock
from typing import Dict

from meta.singleton import SingletonMeta
from eventManager import EventManager

class CoResidencyDetector(metaclass=SingletonMeta):
    def __init__(self, configuration: Dict, eventManager = EventManager()):
        """
        Initializes the CoResidencyDetector with a configuration and an event manager.

        :param configuration: A dictionary containing the configuration parameters for the detector.
        :param eventManager: An instance of EventManager to enable communication with other components of the control plane.
                             This can be replaced with a custom event manager if needed.
        """
        self.__eventManager = eventManager
        self.__configuration = configuration
        self.__lock = Lock()
        self.__logger = Logger("CoResidencyDetector")

        # Global variables exposed to be used in the rest of the control plane
        self.hostMetrics = {}
        self.hostFlags = {}
        self.hostDeflags = {}
        self.globalMetrics = {}
        self.mitigatedHostIDs = set()

        # Subscribe to events
        self.__eventManager.on(self.__configuration["EventNames"]["ConfigurationReloaded"], self.__updateConfig)
        self.__eventManager.on(self.__configuration["EventNames"]["SampleEvent"], self.__updateMetrics)
        self.__logger.info("Initialized Co-Residency Detector.")

    def __updateConfig(self, newConfiguration: Dict):
        self.__logger.debug("Reloading configuration in response to `ConfigurationReloaded` event.")
        self.__configuration = newConfiguration

        for key in self.hostMetrics:
            self.hostMetrics[key].reconfigure(self.__configuration["MaxSamples"],
                                                self.__configuration["SamplesBeforeInclusion"],
                                                self.__configuration["SamplesBeforeExclusion"],
                                                self.__configuration["NormalizeSamples"])

    def __updateMetrics(self, metrics: Dict):
        """
        Updates the metrics for each host based on the provided metrics dictionary.

        :param metrics: A dictionary where keys are host IDs and values are dictionaries of metrics for each host.
        
        :note: The name of the metrics must match the keys defined in the configuration.
        :note: The `Activity` metric is mandatory for each host. It is used to determine the activity status of the host.
        """
        with self.__lock:
            for hostID in metrics.keys():
                try:
                    self.hostMetrics[hostID].recordSample(metrics[hostID])
                except KeyError:
                    self.hostMetrics[hostID] = CoResidencyDetector.HostMetrics(metrics[hostID],
                                                                                self.__configuration["MaxSamples"],
                                                                                self.__configuration["SamplesBeforeInclusion"],
                                                                                self.__configuration["SamplesBeforeExclusion"],
                                                                                self.__configuration["NormalizeSamples"])
            self.__updateGlobalMetrics()
            self.__updateHostDeltas()
            self.__updateHostFlags()

            if not self.__configuration["Mitigation"]:
                return
            
            # Start / Stop mitigation measures.
            for hostID, value in self.hostFlags.items():
                if value > self.__configuration["Mitigation"]["FlagsBeforeActivation"]:
                    self.__logger.info(f"Initiating mitigation on host {hostID}.")
                    self.mitigatedHostIDs.add(hostID)
                    self.__eventManager.emit(self.__configuration["EventNames"]["StartMitigation"], hostID)
                    self.hostFlags[hostID] = 0

            for hostID, value in self.hostDeflags.items():
                if value > self.__configuration["Mitigation"]["DeflagsBeforeDeactivation"]:
                    self.__logger.info(f"Stopping mitigation on host {hostID}.")
                    self.mitigatedHostIDs.discard(hostID)
                    self.__eventManager.emit(self.__configuration["EventNames"]["StopMitigation"], hostID)
                    self.hostDeflags[hostID] = 0

    def __updateHostFlags(self):
        """
        Updates the flags for each host based on the deltas of their metrics.
        """
        for hostID in self.hostMetrics.keys():
            # Skip inactive hosts from flagging
            if not self.hostMetrics[hostID].isActive():
                continue

            deltas = self.hostMetrics[hostID].getDeltas()
            triggerFlag = 1
            for key in deltas.keys():
                if deltas[key] <= self.__configuration["Thresholds"][key]:
                    triggerFlag = 0
                    break
            
            if triggerFlag:
                self.__logger.debug(f"Host {hostID} flagged for exceeding thresholds in all deltas: {deltas}.")
                # Host exceeds in all deltas. Reset deflags.
                if hostID in self.mitigatedHostIDs:
                    self.hostDeflags[hostID] = 0
                else:
                    try:
                        self.hostFlags[hostID] += 1
                    except KeyError:
                        self.hostFlags[hostID] = 1
            elif hostID in self.mitigatedHostIDs:
                try:
                    self.hostDeflags[hostID] += 1
                except KeyError:
                    self.hostDeflags[hostID] = 1

    def __updateGlobalMetrics(self):
        """
        Calculates the global metrics by averaging the metrics of all active hosts.

        It skips hosts that are either suspect (in the mitigated list) or inactive.
        """
        self.globalMetrics.clear()
        benignHosts = 0

        for hostID in self.hostMetrics.keys():
            # As per the design, skip the inclusion of suspect hosts or inactive hosts.
            if hostID in self.mitigatedHostIDs or not self.hostMetrics[hostID].isActive():
                continue
            
            metrics = self.hostMetrics[hostID].getMetrics()
            benignHosts += 1

            for key, value in metrics.items():
                try:
                    self.globalMetrics[key] += value
                except KeyError:
                    self.globalMetrics[key] = value
        if benignHosts != 0:
            for key in self.globalMetrics:
                self.globalMetrics[key] /= benignHosts

    def __updateHostDeltas(self):
        for hostID in self.hostMetrics.keys():
            # Hosts that are not active do not participate in threshold checking
            if not self.hostMetrics[hostID].isActive():
                continue

            self.hostMetrics[hostID].updateDeltas(self.globalMetrics)

    class HostMetrics:
        """
        Inner class to handle metrics for each host.

        It maintains a rolling window of metrics samples, calculates normalized metrics, and stores host metadata such as activity status and deltas.
        """
        def __init__(self, initialMetrics: Dict, maxSamples: int = 0, activityThreshold: int = 0, inactivityThreshold: int = 0, prefNormalized: bool = True):
            self.__metrics = {}
            self.__normalizedMetrics = {}
            self.__maxSamples = maxSamples
            self.__currentSamples = 1
            self.__activityThreshold = activityThreshold if activityThreshold > 0 else maxSamples - 1
            self.__inactivityThreshold = inactivityThreshold if inactivityThreshold > 0 else 1
            self.__prefNormalized = prefNormalized
            self.__deltas = {}
            self.__active = False
            
            if 'Activity' not in initialMetrics.keys():
                self.__logger.error("Activity metric is missing from initial metrics. Cannot determine activity status.")
                exit(1)

            for key, value in initialMetrics.items():
                self.__metrics[key] = deque()
                self.__metrics[key].append(value)

                self.__normalizedMetrics[key] = deque()
                self.__normalizedMetrics[key].append(value)

            # Adjust activity status
            if sum(self.__metrics['Activity']) > self.__activityThreshold:
                self.__active = True
            elif sum(self.__metrics['Activity']) < self.__inactivityThreshold:
                self.__active = False

        def recordSample(self, sampleMetrics: Dict):
            """
            Records a new sample of metrics for the host.
            """
            if self.__currentSamples == self.__maxSamples:
                for key, value in sampleMetrics.items():
                    self.__metrics[key].popleft()
                    self.__normalizedMetrics[key].popleft()
            else:
                self.__currentSamples += 1

            for key, value in sampleMetrics.items():
                self.__metrics[key].append(value)
                self.__normalizedMetrics[key].append(
                    self.__metrics[key][-1] - self.__metrics[key][-2])

            # Activity metric is by definition normalized.
            self.__normalizedMetrics['Activity'] = self.__metrics['Activity']

            # Adjust activity status
            if sum(self.__metrics['Activity']) > self.__activityThreshold:
                self.__active = True
            elif sum(self.__metrics['Activity']) < self.__inactivityThreshold:
                self.__active = False

        def getMetrics(self) -> Dict[str, float]:
            """
            Returns the average metrics for the host.

            If `prefNormalized` is set to True, it returns the normalized metrics, otherwise it returns the raw metrics.
            """
            metricReport = {}
            collection = self.__metrics if not self.__prefNormalized else self.__normalizedMetrics

            for key in self.__metrics.keys():
                metricReport[key] = round(sum(collection[key]) / self.__currentSamples)
            
            return metricReport

        def getDeltas(self):
            return self.__deltas

        def isActive(self) -> bool:
            return self.__active

        def reconfigure(self, newMaxSamples: int, newActivityThreshold: int, newInactivityThreshold: int, newPrefNormalized: bool):
            self.__activityThreshold = newActivityThreshold
            self.__inactivityThreshold = newInactivityThreshold
            self.__prefNormalized = newPrefNormalized
            self.__adjustSampleSize(newMaxSamples)

        def updateDeltas(self, globalMetrics: Dict[str, float]):
            metrics = self.getMetrics()

            # The deviation in each metric is expressed in percentages
            for key in metrics.keys():
                self.__deltas[key] = abs(1.0 - metrics[key] / globalMetrics[key])

        def __adjustSampleSize(self, newMaxSamples: int):
            if self.__maxSamples > newMaxSamples:
                for _ in range(self.__maxSamples - newMaxSamples):
                    for key in self.__metrics.keys():
                        self.__metrics[key].popleft()
                        self.__normalizedMetrics[key].popleft()
            self.__maxSamples = newMaxSamples
