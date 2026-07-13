from __future__ import annotations

from typing import Literal
from typing_extensions import NotRequired, Required, TypedDict


Evidence = Literal[
    "schematic_confirmed",
    "board_test_confirmed",
    "model_inference",
    "unconfirmed",
]


class GpioMappingEntry(TypedDict):
    gpio: Required[int | str]
    function: Required[str]
    direction: NotRequired[str]
    active_level: NotRequired[str]
    constraints: NotRequired[str]
    evidence: NotRequired[Evidence]
    source_location: NotRequired[str]
    confidence: NotRequired[float]


class SerialMappingEntry(TypedDict):
    interface: Required[str]
    tx_gpio: NotRequired[int | str]
    rx_gpio: NotRequired[int | str]
    usb_bridge: NotRequired[str]
    default_baudrate: NotRequired[int | str]
    constraints: NotRequired[str]
    evidence: NotRequired[Evidence]
    source_location: NotRequired[str]
    confidence: NotRequired[float]
