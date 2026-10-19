# Security model

pympacds applies **least privilege in three places**: the **core** (identity and
D-Bus), the **services** (how each daemon runs and is confined), and the
**admin tool** (`pympacds-admin`, which validates what gets installed). This page
describes that model.

## Core

### Service account

The core creates a dedicated system account **`pympacds-user`** (member of the
`dbus` group). **Every service runs as this account, never as root.**

- D-Bus authorization is **name-based** (`org.pympacds.*`), so a non-root system
  user in the `dbus` group can own a service name — no root and no capability are
  needed.
- The log directory `/var/log/pympacds` is owned by `pympacds-user`.
- Real-time is opt-in and best-effort: `[rt]` may require an ambient
  `CAP_SYS_NICE` for `sched_policy = deadline`; that is granted per unit and never
  makes the service root.

### D-Bus

Services communicate only over the system bus, within the configured namespace
prefix (`org.pympacds` by default). The policy file authorizes the namespace, so
owning a service name needs no privileged syscall.

## Services

### Least privilege

Each service:

- runs as **`pympacds-user`** (never root);
- keeps its program and config **root-owned** (or owned by the account it runs
  as) and **not group/other-writable**;
- obtains resource access through **dedicated groups + `udev` rules**, not root —
  for example `gpio` uses the `gpio` group via `SupplementaryGroups=gpio`;
- writes only to declared paths; with `ProtectSystem=strict`, a service that needs
  writable state declares `StateDirectory=`/`RuntimeDirectory=`/
  `ReadWritePaths=`.

### Hardening directives

Directives fall into three groups.

**`always`** — applied in both hardening modes; they set *privilege*, not
*confinement*:

```
User=pympacds-user
Group=pympacds-user
```

**`strict`** — the default `--strict` baseline (removed by `--no-strict`):

```
NoNewPrivileges=yes
ProtectSystem=strict
LogsDirectory=pympacds
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
UMask=0027
RemoveIPC=yes
```

`NoNewPrivileges=yes` is omitted automatically for `[rt] sched_policy = deadline`
units, which need an ambient `CAP_SYS_NICE`.

**`incompatible`** — not emitted by the framework; a service declares these in
its own unit only when its needs require them:

- *Incompatible with the stack / RT:* `RestrictRealtime=yes`; `NoNewPrivileges=yes`
  alongside an ambient `CAP_SYS_NICE`.
- *Device access:* `PrivateDevices=`, `DevicePolicy=`, `DeviceAllow=` — used by
  `mqtt`/`http`, **not** by `gpio` (which relies on udev + the `gpio` group).
- *Can break a plausible service:* `ProcSubset=pid`, `MemoryDenyWriteExecute=yes`,
  `RestrictAddressFamilies=`, `RestrictNamespaces=yes`, `SystemCallFilter=`,
  `CapabilityBoundingSet=` (empty).

### Shipped services

The shipped units carry the `always` and `strict` groups directly, so Debian
installs are hardened without running the admin tool.

**`mqtt`** — TCP + DNS (and optional TLS):

```
User=pympacds-user
Group=pympacds-user
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
PrivateDevices=yes
DevicePolicy=closed
```

**`http`** — as `mqtt`, plus client-cert paths. If a `tls_certfile`/config lives
under `/home`, relax `ProtectHome=yes` for that unit.

**`gpio`** — device access solely via udev + the `gpio` group:

```
User=pympacds-user
Group=pympacds-user
SupplementaryGroups=gpio
RestrictAddressFamilies=AF_UNIX
```

`gpio` uses **no** systemd device directives: `PrivateDevices=`/`DevicePolicy=`
would hide the chips, and `DeviceAllow=` cannot express "any gpiochip" (no globs;
the major/minor is dynamic).

## Admin tool (`pympacds-admin`)

### Trust boundary

`install` runs as **root** and registers a program/config for a privileged unit,
so `--program` and `--config` are **untrusted inputs**. The tool validates them
before writing anything and **fails closed**; there is no override.

### File-permission checks

`install` resolves the program and config to their **real paths** (symlinks
followed) and verifies that the file **and every ancestor directory**:

1. is a **regular file** (the program must also be executable);
2. is **owned by `root` or by the unit's user**;
3. is **not writable by group or other**;
4. (ancestors) is not **world-writable** — sticky or not, so `/tmp` paths are
   rejected.

On violation it writes nothing and exits non-zero:

```
error: unsafe path for a service running as 'root':
  /home/alice/app.py: owned by uid 1000 (must be root or 'root')
pympacds-admin: refusing to install pympacds-mysvc.service
```

The same checks run in `--dry-run` and, for a `--program` pre-flight, in
`pympacds-admin config validate`.

### Running as root

A service may be installed as root, but only after an **interactive**
confirmation (a `yes` at a TTY) following a risk alert. There is **no flag or
environment variable to pre-answer or automate** it. The shipped services never
run as root.

### Service identity and groups

`--user USER` sets the service account (default `pympacds-user`); `--supp-groups
GROUPS` adds `SupplementaryGroups=`, applied through the `user.conf` drop-in.

Both are validated before anything is written:

- names are restricted to a safe character set (no newlines, `=` or `/`), so a
  value cannot inject another unit directive;
- an explicitly supplied user and every supplementary group must exist (names only
  for groups); unknown principals fail closed;
- a **high-risk group** (`root`, `wheel`, `sudo`, `disk`, `docker`, `libvirt`,
  `kvm`, `lxd`, `incus`, `shadow`, `adm`, `systemd-journal`) triggers a loud
  warning. A supplementary group bypasses the unprivileged-user boundary, so grant
  the narrowest group that works (e.g. `gpio`, never `disk`).

### Hardening modes

For a **generated** unit (`--program`), `install` manages a `hardening.conf`
drop-in:

- **`--strict` (default)** — writes the `strict` group.
- **`--no-strict`** — removes it, for initial testing only (a warning is printed).
  It relaxes *confinement*, never *privilege*: identity and the file-permission
  checks always apply.

A **packaged** unit already ships the `strict` group, so `--no-strict` does not
relax it (the tool prints a note).

## Verification

- `pympacds-admin config validate -c <ini> [--program <path>] [--user <user>]` —
  with `--program`, also reports file-permission problems.
- `systemd-analyze verify <unit>` and `systemd-analyze security <unit>` — inspect
  the installed unit and its confinement score.

## References

- [CWE-732][cwe732] — Incorrect Permission Assignment for Critical Resource.
- [CWE-276][cwe276] — Incorrect Default Permissions.
- [CWE-426][cwe426] — Untrusted Search Path.
- [`systemd.exec(5)`][systemd-exec].

[cwe732]: https://cwe.mitre.org/data/definitions/732.html
[cwe276]: https://cwe.mitre.org/data/definitions/276.html
[cwe426]: https://cwe.mitre.org/data/definitions/426.html
[systemd-exec]: https://www.freedesktop.org/software/systemd/man/systemd.exec.html
