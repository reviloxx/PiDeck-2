"""Linux readers that collect hardware, driver and software facts.

The package is split by data source (procfs, sysfs GPUs, video decoding,
sensors, network). Each reader degrades to ``None`` or an empty result, so a
missing tool or file never prevents the System information page from opening.
"""

from .gateway import LinuxSystemInfoGateway

__all__ = ["LinuxSystemInfoGateway"]
