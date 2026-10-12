# Real-Time Configuration

```{warning}
Real-time support in pympacds is **soft** real-time only. Be careful if your application has firm or hard real-time requirements: the `[rt]` settings reduce latency and jitter on a **best-effort** basis and provide **no worst-case guarantee**. Hard real-time is **practically unachievable in Python** — the GIL, garbage collection, the memory allocator and the interpreter introduce pauses with no upper bound, and no scheduling priority or CPU reservation can remove them. If you need hard real-time, use a native (C/C++) task under a PREEMPT_RT kernel; `[rt]` alone is never enough.
```

pympacds services can request optional, **best-effort soft real-time** tuning with a single framework-managed `[rt]` INI section.

Real-time here does **not** mean "fast" — it means *bounded latency and jitter*. A server sampling a sensor every 5 ms cares more about the worst-case delay than about throughput, and on a general-purpose Linux system page faults, scheduling delays and timer wakeups can make that worst case unpredictably large. The `[rt]` section lets a service use special system configurations to reduce that jitter.

## The `[rt]` section

`[rt]` is a special section at the same level as `[dbus]` and `[middleware]`: it is read and applied by the framework, not by service code (service code may only read the resolved values, see [Checking what was applied](#checking-what-was-applied)). To enable the real-time provisions, add the `[rt]` section with the relevant configurations:

* **No `[rt]` section** — real-time is disabled; nothing is applied or verified.
* **An empty `[rt]` section** — real-time is enabled with the defaults (normal scheduling: policy `other`, nice `0`), which is applied and verified.

`[rt]` configures the **service main thread** (the event-loop thread). Threads a service spawns afterwards inherit that policy unless `sched_reset_on_fork = true`.

Most settings are applied by **systemd** at service start; the framework applies the two settings systemd cannot express: `mlockall()` and `SCHED_DEADLINE`, and then **verifies** every setting by reading back the effective values.

## Configuration keys

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `nice` | int | `0` | Scheduling niceness, `-20`..`19`. |
| `sched_policy` | str | `other` | `other`, `batch`, `idle`, `fifo`, `rr`, `deadline`. |
| `sched_priority` | int | `0` | Static RT priority `1`..`99` (required for `fifo`/`rr`). |
| `sched_runtime_ns` | int | `0` | `SCHED_DEADLINE` per-period CPU budget (ns). |
| `sched_deadline_ns` | int | `0` | `SCHED_DEADLINE` relative deadline (ns). |
| `sched_period_ns` | int | `0` | `SCHED_DEADLINE` reservation period (ns). |
| `sched_reset_on_fork` | bool | `false` | Reset children/threads to `SCHED_OTHER`. |
| `cpu_affinity` | str | `""` | CPU list/ranges, e.g. `2,3` or `2-3`. |
| `io_sched_class` | str | `""` | `realtime`, `best-effort`, `idle` (unset = kernel default, `best-effort`). |
| `io_sched_priority` | int | `-1` | I/O priority `0`..`7` (only with a class). |
| `memlock` | str | `""` | `RLIMIT_MEMLOCK`: `infinity` or bytes. |
| `mlockall` | bool | `false` | Lock current+future pages (requires `memlock`). |
| `oom_score_adjust` | int | `0` | `/proc/self/oom_score_adj`, `-1000`..`1000`. |
| `timer_slack_ns` | int | `-1` | Timer slack in ns; `-1` = default. |

### Consistency rules

* `fifo`/`rr`: `sched_priority` must be `1`..`99`; the three `*_ns` keys must be `0`.
* `deadline`: `sched_priority` must be `0`; the triple must satisfy `0 < sched_runtime_ns <= sched_deadline_ns <= sched_period_ns`.
* `other`/`batch`/`idle`: `sched_priority` and the three `*_ns` keys must be `0`.
* `io_sched_priority` requires `io_sched_class`.
* `mlockall = true` requires `memlock`.
* `nice` is ignored by the kernel under `fifo`/`rr`/`idle` (a warning only).

### Environment overrides

Every key can be overridden with `PYMPACDS_RT_<KEY>`, e.g. `PYMPACDS_RT_SCHED_POLICY=deadline`. Overrides apply to the **running service** and require an `[rt]` section to be present (they never enable RT on their own). They do **not** change the systemd directives; for those, edit the INI and re-install (see below).

## How to adjust

Start with the weakest lever and move up only as needed:

1. **`nice`** — a relative CPU-share hint.
2. **`io_sched_class`/`io_sched_priority`** — relative I/O share.
3. **`timer_slack_ns`** — reduce timer wakeup jitter.
4. **`cpu_affinity`** — pin the service to a CPU set.
5. **`memlock` + `mlockall`** — remove page-fault jitter.
6. **`sched_policy = fifo`/`rr` + `sched_priority`** — a static RT priority.
7. **`sched_policy = deadline`** + the `*_ns` triple — a CPU-bandwidth reservation for a periodic task.

Prerequisites: RT scheduling (`fifo`/`rr`/`deadline`) needs the RT priority/capability to be granted: the framework's tooling grants it for you when you install with the `[rt]` section present. Memory locking needs the `memlock` limit. `SCHED_DEADLINE` is set by the service itself (systemd cannot), so on a machine without systemd the service keeps normal scheduling and the mismatch is reported in the logs.

After editing `[rt]`, regenerate the systemd drop-in and restart:

```bash
sudo pympacds-admin install <service> -c /etc/pympacds/<service>.ini
sudo systemctl restart pympacds-<service>
```

If the section is invalid, the admin tool **disables** real-time entirely (it writes no drop-in) and tells you exactly why. The runtime is more forgiving: a bad value in a hand-edited config is logged and dropped, and the service keeps running with normal scheduling.

## Checking what was applied

The framework reads back the effective settings at startup, warns about any mismatch, and exposes a per-key status through `HealthContract.status()` and the read-only `ProcessBase.rt` property:

```python
import json

print(json.dumps(svc.rt, indent=2))
```

```json
{
  "ok": false,
  "sched_policy": {"requested": "fifo", "applied": null, "verified": "other", "ok": false},
  "nice": {"requested": -10, "applied": null, "verified": 0, "ok": false},
  "mlockall": {"requested": true, "applied": true, "verified": true, "ok": true}
}
```

`applied` records only the settings the framework applied itself (`mlockall`, `SCHED_DEADLINE`); it is `null` for settings that systemd owns.

## Examples

### Periodic sensor (`SCHED_DEADLINE`)

```ini
[rt]
sched_policy = deadline
sched_runtime_ns = 200000     ; 200 µs of CPU per period
sched_deadline_ns = 1000000   ; 1 ms after period start
sched_period_ns = 5000000     ; 5 ms period (200 Hz)
cpu_affinity = 2
mlockall = true
memlock = infinity
```

The install emits (among others):

```ini
[Service]
Nice=0
AmbientCapabilities=CAP_SYS_NICE
RestrictRealtime=no
CPUAffinity=2
LimitMEMLOCK=infinity
```

### Latency-sensitive service (`SCHED_FIFO`)

```ini
[rt]
sched_policy = fifo
sched_priority = 20
mlockall = true
memlock = infinity
```

```ini
[Service]
Nice=0
CPUSchedulingPolicy=fifo
CPUSchedulingPriority=20
RestrictRealtime=no
LimitMEMLOCK=infinity
```

### Minimal (`nice` only)

```ini
[rt]
nice = -10
```

```ini
[Service]
Nice=-10
CPUSchedulingPolicy=other
```

## A note on CPU isolation

Pinning a service with `cpu_affinity` does **not** isolate the CPU: other tasks can still be scheduled there. Meaningful soft real-time also needs boot-time isolation (`isolcpus`, `nohz_full`, `rcu_nocbs`) and/or cpusets, which are kernel/deployment choices outside `[rt]`.
