# pympacds Documentation

**Py**thon **M**ulti **P**rocess **A**sync-based **C**ooperative **D**iscoverable **S**ervices Framework

pympacds is a D-Bus-native application framework for building modular, multi-process, asyncio-based daemons on Linux, primarily for embedded and IoT systems.

```{toctree}
:maxdepth: 2
:caption: Contents

features
config
contracts
middleware
signal_action
api
```

## Quick start

```python
from pympacds.process import ProcessBase
from pympacds.dbus import DBusManager


class MyService(ProcessBase):
    async def start_dbus(self):
        self.bus = DBusManager(logger=self.logger, busname="myservice")
        await self.bus.start()
        return True


if __name__ == "__main__":
    MyService().start()
```

Run it with a configuration file:

```bash
my_service -c /etc/my_service/my_service.ini
```

See the [Configuration](config.md) page for the configuration format, and [Features and Philosophy](features.md) for the full picture.
