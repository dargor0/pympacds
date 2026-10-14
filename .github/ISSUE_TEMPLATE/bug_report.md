---
name: Bug report
about: Something is not working
labels: bug
---

**Version** (`pympacds-admin --version`):

**Installation** — how did you install pympacds?
- [ ] pip (`pip install pympacds`)
- [ ] Debian/Ubuntu package from the PPA (`apt install python3-pympacds`)
- [ ] other (describe):

**Platform** (distro / Python version / D-Bus backend: dbus-fast or dbus-next):

**D-Bus configuration**
- Bus type (`system` or `session`):
- `bus_prefix` (default `org.pympacds`):
- Other relevant `[dbus]` keys:

**What happened**

A clear and concise description of the bug.

**Steps to reproduce**

1.
2.
3.

**Expected behaviour**

**Logs / config**

Relevant log output and the `[DEFAULT]`/`[dbus]` sections of your INI (redact
secrets).
