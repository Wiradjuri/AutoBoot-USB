"""Boot capability boundary. No unattended firmware modification is implemented."""

from dataclasses import dataclass

from ..adapters.base import PlatformAdapter


@dataclass(frozen=True)
class BootCapabilities:
    firmware: str
    guidance: str
    automatic_one_time_boot_verified: bool = False


def inspect_boot(adapter: PlatformAdapter) -> BootCapabilities:
    return BootCapabilities(adapter.firmware_mode(), adapter.boot_guidance())


def request_one_time_usb_boot(*_args: object, **_kwargs: object) -> None:
    raise NotImplementedError(
        "Automated USB boot requires firmware-specific target validation and physical testing."
    )
