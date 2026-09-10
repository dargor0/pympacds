"""Higher-level friend bus subscription helpers."""

import logging
from typing import Any


async def connect_friendbus(
    basebusname: str,
    busdict: dict,
    buscallback: Any,
    busobj: Any,
    logger: logging.Logger,
) -> None:
    """Dynamically subscribe and unsubscribe to friend buses.

    Args:
        basebusname: Bus name substring to match among friends.
        busdict: Dict tracking active subscriptions
            ``{busname: {"busif": ..., "mcb": ...}}``.
        buscallback: Called as ``callback(busname, busif, mcb)`` where
            ``mcb`` is ``None`` on subscribe and the previously stored
            value on unsubscribe.  The return value of the subscribe
            call is stored as ``mcb``.
        busobj: A ``DBusManager`` instance.
        logger: Logger for diagnostic messages.
    """
    await busobj.update_friendbus()
    buslist = busobj.query_friend_busname(basebusname)

    # subscribe to new friends
    for busname in buslist:
        if busname not in busdict:
            try:
                mbusif = await busobj.get_friend_bus(busname)
                mcb = None
                if buscallback is not None:
                    mcb = buscallback(busname, mbusif, None)
                busdict[busname] = {"busif": mbusif, "mcb": mcb}
                logger.debug("Subscribed to bus %s", busname)
            except Exception:
                logger.exception("Unable to get busif for bus %s", busname)

    # unsubscribe from departed friends
    for busname in list(busdict.keys()):
        if busname not in buslist:
            entry = busdict[busname]
            if buscallback is not None:
                buscallback(busname, entry["busif"], entry["mcb"])
            busdict.pop(busname)
            logger.debug("Unsubscribed from bus %s", busname)
