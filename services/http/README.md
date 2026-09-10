# pympacds-http

HTTP client service for [pympacds](https://github.com/dargor0/pympacds). Sends
data over HTTP on behalf of other services and exposes it over D-Bus, with
automatic retry and optional mutual TLS.

## Features

- Fire-and-forget D-Bus API: `send`, `download`, and `request` each return a
  random request token immediately; the result is delivered asynchronously via
  the `request_completed(token, result)` signal, so callers correlate a call
  with its outcome by token.
- A single pooled `aiohttp.ClientSession` (native asyncio, no thread
  offloading), created at startup with no network I/O and closed on shutdown.
- Automatic retry of transient failures (network error, timeout, HTTP 5xx)
  with exponential backoff (`retries`, `retry_backoff_s`).
- TLS verification control (`tls_verify`) plus mutual TLS via
  `tls_certfile`/`tls_keyfile` (must be provided together).
- `download` / `request` with a `download_path` write the response body
  atomically to disk (temp file + rename) for binary/large downloads.
- Runtime configuration: `[http]` keys are writable via the framework
  `ConfigContract`.
- Exports the framework `HealthContract` (always) and `ConfigContract`
  (opt-in via `[dbus] contract_config = true`) alongside the HTTP contract.

### Why aiohttp

The service uses [aiohttp](https://docs.aiohttp.org/) because it is asyncio
native: requests run directly on the framework's single event loop with no
thread offloading. It is isolated in this service's own package, so the core
`pympacds` framework keeps its zero-dependency property (the core's
`httpconfprov` middleware continues to use the stdlib `urllib.request`).

## Installation

```bash
pip install pympacds-http
```

## Configuration

The service reads its parameters from the `[http]` INI section (see
`config/http.ini.example`). Key options:

| Key | Default | Description |
|-----|---------|-------------|
| `timeout_s` | `10` | Per-request timeout |
| `retries` | `3` | Number of retry attempts |
| `retry_backoff_s` | `1` | Initial backoff (exponential) between retries |
| `tls_verify` | `true` | Verify TLS certificates |
| `tls_certfile` / `tls_keyfile` | `""` | Client cert/key (PEM) for mutual TLS |
| `max_redirects` | `5` | Maximum redirects to follow |
| `default_url` | `""` | Target URL used by `send()` (empty = `send()` fails) |
| `default_method` | `POST` | Default HTTP method used by `send()` |
| `default_headers` | `""` | Default headers (JSON object) used by `send()` |

Run with:

```bash
pympacds-http -c /etc/pympacds/http.ini
```

## D-Bus API

Interface `org.pympacds.HTTP` at object path `/org/pympacds/http`:

| Member | Type | Description |
|--------|------|-------------|
| `send(payload)` | `send(s) -> s` | Send with default url/method/headers; returns a token |
| `download(payload, download_path)` | `download(ss) -> s` | Like `send`, but saves the body to `download_path` |
| `request(method, url, headers, body, download_path)` | `request(sssss) -> s` | Explicit request; empty `download_path` = return body |
| `request_completed(token, result)` | signal `(ss)` | Emitted on completion; carries the token and JSON result |

All sends are **fire-and-forget**: the method returns immediately with a
randomized token, the request runs asynchronously, and `request_completed` is
emitted with the full JSON outcome:

```json
{
  "status_code": 200,
  "url": "https://example.com/api",
  "elapsed_ms": 123,
  "body": "...",
  "download_path": null,
  "bytes_written": null,
  "error": null
}
```

The framework `HealthContract` (at `.../health`) and `ConfigContract`
(`[dbus] contract_config = true`) are also exported.
