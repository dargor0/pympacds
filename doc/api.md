# API Reference

This page is generated from the pympacds docstrings.

## Process lifecycle

```{eval-rst}
.. autoclass:: pympacds.process.ProcessBase
   :members:
```

## D-Bus transport

```{eval-rst}
.. autoclass:: pympacds.dbus.DBusManager
   :members:
```

## Contracts

```{eval-rst}
.. autoclass:: pympacds.contracts.ServiceContract
   :members:

.. autofunction:: pympacds.contracts.dbus_method
.. autofunction:: pympacds.contracts.dbus_signal
.. autofunction:: pympacds.contracts.dbus_property
```

## Built-in contracts

```{eval-rst}
.. autoclass:: pympacds.builtin_contracts.HealthContract
   :members:
   :show-inheritance:

.. autoclass:: pympacds.builtin_contracts.LifecycleContract
   :members:
   :show-inheritance:

.. autoclass:: pympacds.builtin_contracts.ConfigContract
   :members:
   :show-inheritance:

.. autoclass:: pympacds.builtin_contracts.MetricsContract
   :members:
   :show-inheritance:
```

## Middleware

```{eval-rst}
.. autoclass:: pympacds.middleware.MiddlewareBase
   :members:

.. autoclass:: pympacds.middleware.HttpConfigMiddleware
   :members:
   :show-inheritance:
```

## Configuration

```{eval-rst}
.. autoclass:: pympacds.config.ConfigManager
   :members:
```

## Service discovery

```{eval-rst}
.. autofunction:: pympacds.service_discovery.connect_friendbus
```

## Utilities

```{eval-rst}
.. autofunction:: pympacds.utils.get_systemhw_data
```
