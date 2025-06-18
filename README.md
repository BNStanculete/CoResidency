# CoResidency

This repository contains the source code for the co-residency detection tool. The tool was designed as fully customizable, isolated module that cannot be used a standalone. It needs to be integrated with a system which contains at the bare minimum a packet collection mechansim which can sample metrics about the incomming packets.

## Installation

Make sure you have ```pip``` (Python package manager) installed. If not you can install it on Linux via:
```
sudo apt-get install python3-pip python3-dev
```
After installing pip, download the required dependencies as so:
```
pip install -r {PATH_TO_PROJECT}/requirements.txt
```
This will automatically download and install everything required to integrate the co-residency detection algorithm.

## Integration with existing solutions

The algorithm is designed as a fully-configurable, extensible and completely isolated module which communicates with the rest of the system via 4 (four) types of events:
- ConfigurationReloaded: This event is emitted by the ```ConfigurationManager``` whenever the watchdog detects a change in the configuration file. By default the name of the event is *ConfigurationReloaded*.
- SampleEvent: This event must be emitted by your system whenever you sample a new set of metrics for the filtering algorithm. The detector subscribes to this event on initialization. The default name is *MetricsSampled*.
- StartMitigation: This event is emitted by the `CoResidencyDetector` whenever it has classified a host as suspect for testing for co-residency. The default name of the event is *MitigationStart*.
- StopMitigation: This event is emitted by the `CoResidencyDetector` whenever it has classified a host as no longer a suspect. The default name of the event is *MitigationStop*.

In the repository we also provide a bare-bones, thread-safe implementation of an event bus which can be used for integration purposes. However you can also pass a custom event bus when instantiating the detector and the configuration manager. 

### Communicating via events

The actual metric collection is up to the developer. The filtering algorithm is independent of the underlying technologies and collection mechanisms. What it requires is that those metrics are received from the system via a **SampleEvent**. To do that, you must use the same event bus as the `CoResidencyDetector` and emit the corresponding event like so:
```
eventManager.emit('MetricsSampled', metrics)
```
The `metrics` parameter is a dictionary having the host ID as key and the metrics for that host as value. A mandatory aspect is the presence of the **Activity** metric with a value of 0 or 1 denoting whether the host is considered active or not. We took the decision of not computing activity in the algorithm, as host activity has a different definition from use-case to use-case. Since Activity is required to know whether to include a host in the filtering algorithm, this value and format must be present as-is.

The other metrics are completely customizable, as long as their values support the standard arithmetic operators `+`, `/`, and the boolean operator `>`. As a reference we give the `float` data type. As long as a metric can achieve everything a float does, it can be used by the filtering algorithm. Another important note is that all hosts must contain the same metrics. If a metric is missing for a specific host, use the neutral element (`0.0` for floats) as a default value. Below we provide an example of how to report metrics:
```
metrics = {
    1: {
        "Activity": 1,
        "Connections": 5,
        "Packets": 205,
        "PacketSize": 124159
    },
    2 : {
        "Activity": 1,
        "Connections": 0,
        "Packets": 3,
        "PacketSize: 200,
    }
}

eventManager.emit('MetricsSampled', metrics)
```
In this example host with ID 2 created no new connections since the last sample, so we report the value 0 for connections, but we report it instead of omitting it. Once the algorithm has made a decision to initiate / stop mitigations for a host ID, we will be notified via an event. The system will need to subscribe to both start and stop events to act accordingly. Below we have an example:
```
eventManager.on('MitigationStart', startMitigation)
eventManager.on('MitigationStop', stopMitigation)
```
where `startMitigation` and `stopMitigation` are functions with the following signatures:
```
def startMitigation(hostID):
    pass

def stopMitigation(hostID):
    pass
```
In essence, upon emitting a mitigation-related event, the filtering algorithm also provides the host ID for which the event is emitted. It's important to note that events are emitted individually and not in batch.

Another important aspect is that the filtering algorithm starts as soon as the class is instantiated, and functions any time it receives new samples. Once you want to shutdown the algorithm, call the `stop()` method of the `ConfigurationManager` to join the watchdog thread, and simply join the thread containing the `CoResidencyDetector`.

## Managing the configuration

After you have implemented the metric sampling (mandatory), and mitigation response (optionally) you can add the algorithm. It's important to note that you need to instantiate one instance of both the ```CoResidencyDetector```, and the ```ConfigurationManager```. The former class is responsible for reading the JSON configuration and making it available for the rest of the system. An important feature is **hot reloading** - meaning that the algorithm repurposes itself on runtime and acts upon configuration changes. This is beacause the `ConfigurationManager` is equipped with a watchdog which runs in a dedicated thread and observe filesystem changes. Meaning that once deployed, the system can be reconfigured and repurposed on-demand with minimal changes.

Every configuration option has been detailed inside the JSON file thanks to the `Description` key. In this document we will briefly go through the important options:
- *MitigationConfiguration* and *EnableMitigation* are both options related to mitigation measures. If mitigation is not enabled, the configuration will not be stored in the algorithm. When mitigation is enabled, the configuration will specify how many flags / deflags are required for the algorithm to start / stop mitigation for a specific host. A higher number of flags will lead to lower false-positives but allow the adversary more time. A lower number of deflags achieves the same effect, but deflags are counted after mitigation was initiated for a host.
- *Thresholds* contains all the threshold values for every metric, as well as all the different metrics (implicitly). We talk about custom metrics in the next section.
- *Performance* contains options related to the performance of the algorithm. Here you can specify when a host should be included / excluded from thresholding (based on its activity), as well as how many samples to keep per host (fine-tuning). Sample normalization can be toggled from here, and will be discussed in a later section.
- *EventNames* stores the actual names of the 4 events presented at the beginning. While the value of the keys are not to be altered, by changing the `Value` field of each key you can change the name of the event with that functionality. For instance changing the Value of `SampleEvent` to *MySampleEvent* will cause the `CoResidencyDetector` to subscribe to *MySampleEvent* for receiving event samples.

### Adding custom metrics

The filtering algorithm natively supports custom metrics. All you need to do is set the appropriate value in the ```metrics``` dictionary (which you pass via the SampleEvent) and set the corresponding threshold in the configuration.

To show-case both custom metrics and hot reloading, consider a scenario where you have deployed the filtering algorithm. After deployment you plug-in a module for collecting some virtual machine performance metrics. To make the filtering algorithm aware of those metrics you do the following changes:
1. Register an appropriate threshold in the configuration JSON
   ```
   {
       ... other settings ...
       "Threshold": {
           ... other metrics ...
           "MyCustomMetric": {
               "Description": "This is optional, but the `Value` field is mandatory.",
               "Value": 100
           }
       }
   }
   ```
2. Once you update the configuration, the watchdog will observe the changes in the file, and notify the ```ConfigurationManager```. This will casue the manager to read the new configuration, aggregate the changes and emit a reconfiguration event. The filtering algorithm receives the new configuration via the event and updates its threshold values.
3. Update the reported metric dictionary
   ```
   metrics = {
       hostID: {
           ... previous metrics ...
           "MyCustomMetric": 0.25
       }

       .. other hosts ..
   }
   ```
It is important that you first change the configuration, as otherwise the algorithm will attempt to compare the delta for the new metric to an inexistent threshold which will result in a `KeyError`.

## Paper Citation (Biblatex)

...