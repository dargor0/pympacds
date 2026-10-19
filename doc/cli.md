# pympacds-admin

`pympacds-admin` sets up the pympacds framework, installs (registers) services
under systemd and the D-Bus system bus, scaffolds new services and middleware,
lists running services, and inspects or edits service INI configuration files.

`setup` and `install` require root privileges because they modify the system;
add `--dry-run` to preview the actions without changing anything (and without
needing root). The other commands run as an ordinary user.

## Synopsis

```text
pympacds-admin [OPTIONS] COMMAND [COMMAND-OPTIONS]
```

## Commands

### setup

Performs the one-time, host-wide setup for pympacds:

- creates the `pympacds-user` system account (no home directory, member of the
  `dbus` group), unless `--no-dirs` is given;
- creates the `/var/log/pympacds` log directory and gives it to `pympacds-user`;
- installs the D-Bus security policy to `/etc/dbus-1/system.d/pympacds.conf`,
  substituting the configured bus prefix for `@BUS_PREFIX@`; when that file
  already exists it is updated in place to the new prefix;
- sends `SIGHUP` to `dbus-daemon` so the new policy takes effect.

Run it once after installing the package (the Debian `postinst` already does),
or again to change the bus prefix.

Use this command if pympacds is installed with pip.

Options:

`--bus-prefix PREFIX`
: D-Bus bus-name prefix written into the security policy (default
  `org.pympacds`). Change it only to run several independent pympacds systems on
  the same host.

`--resource-dir DIR`
: Directory holding the templates `pympacds.conf.in` and
  `pympacds@.service.in` (default `/usr/share/pympacds`).

`--no-dirs`
: Do not create the system user or the log directory; only install the D-Bus
  policy.

`--quiet`
: Suppress the completion message.

`--dry-run`
: Print the actions without changing the system.

### install

Registers a service with systemd and the D-Bus system bus. There are two modes.

**Packaged service** (the default). The systemd unit `pympacds-SERVICE.service`
and the D-Bus policy `SERVICE.conf` shipped by the service package are copied
from `/usr/share/pympacds/` (or `/usr/local/share/pympacds/`) to
`/etc/systemd/system/` and `/etc/dbus-1/system.d/`. Use this for the standard
services. If no D-Bus policy is shipped, only the unit is installed.

**Custom program** (`--program PATH`). No packaged unit is required: a unit is
generated from the framework template
(`/usr/share/pympacds/pympacds@.service.in`) with `ExecStart=PATH -c CONFIG`
and `User=USER` (default `pympacds-user`, `CONFIG` default
`/etc/pympacds/SERVICE.ini`). Use this to run your own executable under
pympacds without shipping a `.service` file.

**Security.** In both modes `--program` and `--config` are validated before
anything is written: the symlink-resolved file and every ancestor directory must
be owned by `root` or the service user and not group/other-writable (paths under
world-writable directories such as `/tmp` are rejected). On violation the command
writes nothing and exits non-zero — there is no override. Running a service as
`root` (`--user root`) additionally requires an **interactive** confirmation.

**Hardening.** By default (`--strict`) a safe hardening baseline is written to
`/etc/systemd/system/pympacds-SERVICE.service.d/hardening.conf`; `--no-strict`
(initial testing only) omits it. See [Security model](security.md).

In both modes the `[rt]` section of the service INI file is read and, when
valid, a soft real-time drop-in
`/etc/systemd/system/pympacds-SERVICE.service.d/rt.conf` is generated. If the
`[rt]` section is present but invalid, real-time is **disabled** for the
service, an explicit message is printed, and the command exits non-zero (the
unit itself is still installed). Then `systemctl daemon-reload` is run and,
unless `--no-enable`, the unit is enabled with `systemctl enable`; `--start`
also runs `systemctl start`.

Options:

`-c, --config FILE`
: Service INI file. It is read for the `[rt]` section (to generate the real-time
  drop-in) and, when `--program` is used, becomes the configuration path in the
  generated unit. Default `/etc/pympacds/SERVICE.ini`.

`--program PATH`
: Executable to run. Setting this generates the systemd unit from the framework
  template instead of copying a packaged one, so any program (not necessarily a
  pympacds-named executable) can be registered. Default
  `/usr/bin/pympacds-SERVICE`.

`--user USER`
: System user the service runs as (default `pympacds-user`). For a generated
  unit it is written into the unit; for a packaged unit it is applied via a
  drop-in. `root` requires an interactive confirmation.

`--supp-groups GROUPS`
: Supplementary groups for the service (`SupplementaryGroups=`), applied via the
  identity drop-in. Comma/space-separated **existing** group names; malformed or
  unknown names are rejected. Granting a high-risk group (e.g. `disk`, `docker`,
  `shadow`) prints a warning. Use the narrowest group that works (e.g. `gpio`).

`--strict` / `--no-strict`
: Apply the default systemd hardening baseline (`--strict`, the default) or omit
  it (`--no-strict`, for initial testing only; prints a warning).

`--no-enable`
: Skip `systemctl enable` after installing.

`--start`
: Start the service after installing.

`--dry-run`
: Print the actions without changing the system.

### new

Scaffolds a new service package in `./NAME/`: a `ProcessBase` subclass
(`src/NAME/main.py`), a `pyproject.toml` that exposes a `pympacds-NAME` console
script, an example INI file (`config/NAME.ini`), and a test stub. The bus prefix
is read from the installed D-Bus policy so the generated service matches this
host. After scaffolding, install it in development with `pip install -e .`.

### new-middleware

Scaffolds a new middleware package in `./NAME/`: a `MiddlewareBase` subclass
registered through the `pympacds.middleware` entry point, an example INI file,
and a test stub. Activate the middleware from a service's `[middleware]` section
of its INI file.

### list

Lists the pympacds services currently registered on the D-Bus system bus. Each
line shows the bus name, the owning process PID, and, on a best-effort basis, the
`provides`/`requires` capability tags read from the service's Health interface.
Requires a D-Bus library (`dbus-fast` or `dbus-next`).

### config

Inspects or edits an INI configuration file. It has the subcommands below;
`show`, `get`, and `validate` only read the file, while `set` and `remove`
rewrite it.

Options:

`-c, --config FILE`
: Config file to operate on.

`-s, --schema FILE`
: JSON schema file used by `config validate`.

Subcommands:

show
: Print every section and key of the file.

get SECTION KEY
: Print the value of a single key. Exits non-zero if the key is absent.

set SECTION KEY VALUE
: Set a key, creating the section if necessary, and write the file back.
  Requires `--config`.

remove SECTION KEY
: Remove a key from the file. Requires `--config`.

validate
: Check the file against the built-in rules for `[DEFAULT]`, `[dbus]`, and
  `[rt]` and, when `--schema` is given, against that JSON schema. Any errors are
  printed to standard error and the command exits non-zero.

## Global options

`--version`
: Print the pympacds version and exit.

`--help`
: Show help for the tool or for a command and exit.

## Files

`/etc/pympacds/SERVICE.ini`
: Service configuration file.

`/etc/systemd/system/pympacds-SERVICE.service`
: Installed systemd unit.

`/etc/systemd/system/pympacds-SERVICE.service.d/rt.conf`
: Soft real-time drop-in generated from the `[rt]` section.

`/etc/dbus-1/system.d/SERVICE.conf`
: D-Bus security policy for the service.

`/usr/share/pympacds/pympacds.conf.in`
: D-Bus security policy template (`@BUS_PREFIX@` placeholder).

`/usr/share/pympacds/pympacds@.service.in`
: systemd unit template used for custom programs.

`/var/log/pympacds`
: Log directory.

## Examples

One-time system setup:

```console
sudo pympacds-admin setup --bus-prefix org.pympacds
```

Install a packaged service and start it:

```console
sudo pympacds-admin install mqtt --start
```

Register a custom program with a custom configuration file:

```console
sudo pympacds-admin install mysvc \
    --program /usr/local/bin/myapp \
    --config /etc/myapp/myapp.ini --start
```

Scaffold a new service and run it from source:

```console
pympacds-admin new mysensor
cd mysensor && pip install -e .
```

Inspect and edit a configuration file:

```console
pympacds-admin config show -c /etc/pympacds/mqtt.ini
pympacds-admin config get -c /etc/pympacds/mqtt.ini mqtt host
sudo pympacds-admin config set -c /etc/pympacds/mqtt.ini mqtt qos 1
pympacds-admin config validate -c /etc/pympacds/mqtt.ini -s mqtt.schema.json
```

## Exit status

0
: Success.

1
: Failure: invalid arguments or configuration, missing privileges, a failed
  external command, or a requested `[rt]` configuration that was invalid and
  therefore disabled.

## See also

`systemctl(1)`, `dbus-daemon(1)`.
