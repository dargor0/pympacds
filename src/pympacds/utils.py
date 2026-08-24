"""Hardware and system utilities."""

import logging

_logger = logging.getLogger(__name__)


def get_systemhw_data(param: str) -> str:
    """Read hardware identification data from the Linux device tree.

    Args:
        param: Which data to obtain — 'serial' or 'model'.

    Returns:
        The requested data string, or an empty string if unavailable.
    """
    sysfile = ""
    if param == "serial":
        sysfile = "/sys/firmware/devicetree/base/serial-number"
    elif param == "model":
        sysfile = "/sys/firmware/devicetree/base/model"

    if not sysfile:
        return ""

    try:
        with open(sysfile) as f:
            return f.read().replace("\0", "")
    except OSError:
        _logger.debug("Cannot read hardware data from %s", sysfile)
        return ""
