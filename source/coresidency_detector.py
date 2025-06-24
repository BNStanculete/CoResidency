"""
CoResidencyDetector is a singleton class that detects which hosts are probing for co-residency.
"""

from logging import Logger
from collections import deque
from threading import Lock
from typing import Dict

import sys

from source.meta.singleton import SingletonMeta
from source.event_manager import EventManager

class CoResidencyDetector(metaclass=SingletonMeta):
    """
    CoResidencyDetector is a singleton class that detects which hosts are probing for co-residency
    based on their activity (expressed as metrics). For each host, it maintains a rolling window of
    metrics samples, calculates normalized metrics, and stores host metadata such as activity status
    and deltas.
    """

    def __init__(self, configuration: Dict, event_manager = EventManager()):
        """
        Initializes the CoResidencyDetector with a configuration and an event manager.

        :param configuration: A dictionary containing the configuration parameters for the detector.
        :param event_manager: An instance of EventManager to enable communication with other
                    components of the control plane. This can be replaced with a custom event
                    manager if needed.
        """
        self.__event_manager = event_manager
        self.__configuration = configuration
        self.__lock = Lock()
        self.__logger = Logger("CoResidencyDetector")

        # Global variables exposed to be used in the rest of the control plane
        self.host_metrics = {}
        self.host_flags = {}
        self.host_deflags = {}
        self.global_metrics = {}
        self.mitigated_host_ids = set()

        # Subscribe to events
        self.__event_manager.on(
            self.__configuration["EventNames"]["ConfigurationReloaded"], self.__update_config)
        self.__event_manager.on(
            self.__configuration["EventNames"]["SampleEvent"], self.__update_metrics)
        self.__logger.info("Initialized Co-Residency Detector.")

    def __update_config(self, new_configuration: Dict):
        self.__logger.debug("Reloading configuration in response to `ConfigurationReloaded` event.")
        self.__configuration = new_configuration

        for key in self.host_metrics:
            self.host_metrics[key].reconfigure(self.__configuration["MaxSamples"],
                                                self.__configuration["SamplesBeforeInclusion"],
                                                self.__configuration["SamplesBeforeExclusion"],
                                                self.__configuration["NormalizeSamples"])

    def __update_metrics(self, metrics: Dict):
        """
        Updates the metrics for each host based on the provided metrics dictionary.

        :param metrics: A dictionary where keys are host IDs and values are dictionaries
                        of metrics for each host.
        
        :note: The name of the metrics must match the keys defined in the configuration.
        :note: The `Activity` metric is mandatory for each host. It is used to determine
               the activity status of the host.
        """
        with self.__lock:
            for host_id in metrics.keys():
                try:
                    self.host_metrics[host_id].record_sample(metrics[host_id])
                except KeyError:
                    self.host_metrics[host_id] = \
                        CoResidencyDetector.HostMetrics(
                            metrics[host_id],
                            self.__configuration["MaxSamples"],
                            self.__configuration["SamplesBeforeInclusion"],
                            self.__configuration["SamplesBeforeExclusion"],
                            self.__configuration["NormalizeSamples"])
            self.__update_global_metrics()
            self.__update_host_deltas()
            self.__update_host_flags()

            if not self.__configuration["Mitigation"]:
                return

            # Start / Stop mitigation measures.
            for host_id, value in self.host_flags.items():
                if value > self.__configuration["Mitigation"]["FlagsBeforeActivation"]:
                    self.__logger.info("Initiating mitigation on host %s.", str(host_id))
                    self.mitigated_host_ids.add(host_id)
                    self.__event_manager.emit(
                        self.__configuration["EventNames"]["StartMitigation"], host_id)
                    self.host_flags[host_id] = 0

            for host_id, value in self.host_deflags.items():
                if value > self.__configuration["Mitigation"]["DeflagsBeforeDeactivation"]:
                    self.__logger.info("Stopping mitigation on host %s.", str(host_id))
                    self.mitigated_host_ids.discard(host_id)
                    self.__event_manager.emit(
                        self.__configuration["EventNames"]["StopMitigation"], host_id)
                    self.host_deflags[host_id] = 0

    def __update_host_flags(self):
        """
        Updates the flags for each host based on the deltas of their metrics.
        """
        for host_id in self.host_metrics:
            # Skip inactive hosts from flagging
            if not self.host_metrics[host_id].is_active():
                continue

            deltas = self.host_metrics[host_id].get_deltas()
            trigger_flag = 1
            for key in deltas.keys():
                if deltas[key] <= self.__configuration["Thresholds"][key]:
                    trigger_flag = 0
                    break

            if trigger_flag:
                self.__logger.debug(
                    "Host %s flagged for exceeding thresholds in all deltas: %s.",
                    str(host_id), deltas)
                # Host exceeds in all deltas. Reset deflags.
                if host_id in self.mitigated_host_ids:
                    self.host_deflags[host_id] = 0
                else:
                    try:
                        self.host_flags[host_id] += 1
                    except KeyError:
                        self.host_flags[host_id] = 1
            elif host_id in self.mitigated_host_ids:
                try:
                    self.host_deflags[host_id] += 1
                except KeyError:
                    self.host_deflags[host_id] = 1

    def __update_global_metrics(self):
        """
        Calculates the global metrics by averaging the metrics of all active hosts.

        It skips hosts that are either suspect (in the mitigated list) or inactive.
        """
        self.global_metrics.clear()
        benign_hosts = 0

        for host_id in self.host_metrics:
            # As per the design, skip the inclusion of suspect hosts or inactive hosts.
            if host_id in self.mitigated_host_ids or not self.host_metrics[host_id].is_active():
                continue

            metrics = self.host_metrics[host_id].get_metrics()
            benign_hosts += 1

            for key, value in metrics.items():
                try:
                    self.global_metrics[key] += value
                except KeyError:
                    self.global_metrics[key] = value
        if benign_hosts != 0:
            for key in self.global_metrics:
                self.global_metrics[key] /= benign_hosts

    def __update_host_deltas(self):
        for host_id in self.host_metrics:
            # Hosts that are not active do not participate in threshold checking
            if not self.host_metrics[host_id].is_active():
                continue

            self.host_metrics[host_id].update_deltas(self.global_metrics)

    class HostMetrics:
        """
        Inner class to handle metrics for each host.

        It maintains a rolling window of metrics samples,
        calculates normalized metrics, and stores host metadata
        such as activity status and deltas.
        """
        def __init__(self, initial_metrics: Dict,
                           max_samples: int = 0,
                           activity_threshold: int = 0,
                           inactivity_threshold: int = 0,
                           pref_normalized: bool = True):
            self.__metrics = {}
            self.__normalized_metrics = {}
            self.__logger = Logger("CoResidencyDetector.HostMetrics")
            self.__max_samples = max_samples
            self.__current_samples = 1
            self.__activity_threshold = \
                activity_threshold if activity_threshold > 0 else max_samples - 1
            self.__inactivity_threshold = \
                inactivity_threshold if inactivity_threshold > 0 else 1
            self.__pref_normalized = pref_normalized
            self.__deltas = {}
            self.__active = False

            if 'Activity' not in initial_metrics.keys():
                self.__logger.error("Activity metric is missing from initial metrics." \
                                    "Cannot determine activity status.")
                sys.exit(1)

            for key, value in initial_metrics.items():
                self.__metrics[key] = deque()
                self.__metrics[key].append(value)

                self.__normalized_metrics[key] = deque()
                self.__normalized_metrics[key].append(value)

            # Adjust activity status
            if sum(self.__metrics['Activity']) > self.__activity_threshold:
                self.__active = True
            elif sum(self.__metrics['Activity']) < self.__inactivity_threshold:
                self.__active = False

        def record_sample(self, sample_metrics: Dict):
            """
            Records a new sample of metrics for the host.
            """
            if self.__current_samples == self.__max_samples:
                for key, value in sample_metrics.items():
                    self.__metrics[key].popleft()
                    self.__normalized_metrics[key].popleft()
            else:
                self.__current_samples += 1

            for key, value in sample_metrics.items():
                self.__metrics[key].append(value)
                self.__normalized_metrics[key].append(
                    self.__metrics[key][-1] - self.__metrics[key][-2])

            # Activity metric is by definition normalized.
            self.__normalized_metrics['Activity'] = self.__metrics['Activity']

            # Adjust activity status
            if sum(self.__metrics['Activity']) > self.__activity_threshold:
                self.__active = True
            elif sum(self.__metrics['Activity']) < self.__inactivity_threshold:
                self.__active = False

        def get_metrics(self) -> Dict:
            """
            Returns the average metrics for the host.

            If `pref_normalized` is set to True, it returns the normalized metrics,
            otherwise it returns the raw metrics.
            """
            metric_report = {}
            collection = self.__metrics if not self.__pref_normalized else self.__normalized_metrics

            for key in self.__metrics:
                metric_report[key] = round(sum(collection[key]) / self.__current_samples)

            return metric_report

        def get_deltas(self):
            """
            Returns the host deltas.
            """
            return self.__deltas

        def is_active(self) -> bool:
            """
            Returns whether the host is considered active
            and can be included in the threshold checking.
            """
            return self.__active

        def reconfigure(self, new_max_samples: int,
                              new_activity_threshold: int,
                              new_inactivity_threshold: int,
                              new_pref_normalized: bool):
            """
            Updates the configuration of the host metrics.
            """
            self.__activity_threshold = new_activity_threshold
            self.__inactivity_threshold = new_inactivity_threshold
            self.__pref_normalized = new_pref_normalized
            self.__adjust_sample_size(new_max_samples)

        def update_deltas(self, global_metrics: Dict):
            """
            Re-calculates the host deltas based on the current global metrics.
            """
            metrics = self.get_metrics()

            # The deviation in each metric is expressed in percentages
            for key in metrics:
                self.__deltas[key] = abs(1.0 - metrics[key] / global_metrics[key])

        def __adjust_sample_size(self, new_max_samples: int):
            """
            Adjusts the deuque size for metrics and normalized metrics
            with respect to the new maximum samples.
            """
            if self.__max_samples > new_max_samples:
                for _ in range(self.__max_samples - new_max_samples):
                    for key in self.__metrics:
                        self.__metrics[key].popleft()
                        self.__normalized_metrics[key].popleft()
            self.__max_samples = new_max_samples
