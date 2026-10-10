# vLex MCP

[![CI](https://github.com/RosenAdvertising/vlex-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/RosenAdvertising/vlex-mcp/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An MCP server for legal research with vLex and enabled Vincent AI workflows. It uses the
verified Research v1, Vincent Research Assistant v1, and Vincent AI Public API v2 routes. The
server communicates over stdio and returns structured JSON. It currently exposes 14 tools.

## Features

- Search legal authorities and secondary materials; retrieve selected documents as HTML.
- Discover accepted research jurisdiction identifiers and account-enabled Vincent workflows.
- Start and poll asynchronous legal research assignments.
- Create document upload records, check processing status, and start or poll authority matching.
- Start Vincent workflow conversations, read messages, continue conversations, and resolve actions.
- Require `confirm=true` for tools that start jobs, create records, or otherwise mutate vLex data.

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- A vLex subscription key for the target developer portal account
- An MCP-compatible client

## Install from source

Clone this repository, then install the locked dependencies:

```bash
uv sync --locked
```

Set the required environment variable and run the stdio server:

```bash
export VLEX_API_KEY="your-subscription-key"
uv run --locked vlex-mcp
```

The server reads configuration from process environment variables only. It does not load a
dotenv file implicitly. For local development, `.env.example` documents the available settings;
keep any real credentials in an untracked `.env` file or your environment.

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `VLEX_API_KEY` | Yes | — | Subscription key sent using the documented `Ocp-Apim-Subscription-Key` header. |
| `VLEX_HTTP_TIMEOUT_SECONDS` | No | `30` | HTTP request timeout; must be greater than zero. |
| `VLEX_MAX_RETRIES` | No | `3` | Retries for HTTP 429 and 5xx responses; must be zero or greater. |
| `VLEX_BACKOFF_BASE_SECONDS` | No | `0.5` | Base delay for exponential retry backoff; must be greater than zero. |
| `VLEX_MAX_BACKOFF_SECONDS` | No | `8` | Maximum retry delay; must be at least the base delay. |

The server sends requests to the API hosts configured for the verified vLex API surfaces. It does
not assume a separate sandbox or staging host.

## MCP client configuration

For an MCP client that launches local commands, use an absolute path to your clone. For example:

```json
{
  "mcpServers": {
    "vlex": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/vlex-mcp",
        "run",
        "--locked",
        "vlex-mcp"
      ],
      "env": {
        "VLEX_API_KEY": "your-subscription-key"
      }
    }
  }
}
```

Use the jurisdiction and workflow discovery tools instead of guessing identifiers or assuming
features are enabled for an account.

## HTTP mode

Stdio is the default. To serve stateless Streamable HTTP, set the transport and run
the same console command. The MCP endpoint is `/mcp`; the SDK supports both the
2026-07-28 protocol and legacy clients on that endpoint. Responses retain the SDK's
default SSE behavior so disconnecting a client cancels its request.

> **Security: this endpoint has no authentication and no TLS.** Anyone who can reach the port can run every tool, including write and delete tools, with this server's vendor credentials. Keep the default loopback bind (`127.0.0.1`), or put the server behind an authenticating TLS proxy on a private network. `VLEX_MCP_ALLOWED_HOSTS` and `VLEX_MCP_ALLOWED_ORIGINS` protect against browser DNS rebinding, not against direct callers. A proxy in front of it needs connection and idle timeouts: a legacy-style `GET /mcp` with `Accept: text/event-stream` holds a stream open until the client disconnects.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `VLEX_MCP_TRANSPORT` | No | `stdio` | Set to `streamable-http` for HTTP mode. |
| `VLEX_MCP_HOST` | No | `127.0.0.1` | Address to bind. |
| `PORT` | No | `8080` | Integer HTTP port. |
| `VLEX_MCP_ALLOWED_HOSTS` | On non-loopback hosts | — | Comma-separated accepted Host values, such as `mcp.example.com` or `mcp.example.com:*`. |
| `VLEX_MCP_ALLOWED_ORIGINS` | No | — | Comma-separated accepted Origin values, such as `https://app.example.com`; on non-loopback hosts, an empty list rejects any supplied Origin. |
| `VLEX_API_KEY` | For vendor tools | — | The same subscription key used in stdio mode, read from the server environment. |
| `VLEX_HTTP_TIMEOUT_SECONDS` | No | `30` | Vendor HTTP request timeout. |
| `VLEX_MAX_RETRIES` | No | `3` | Retries for vendor HTTP 429 and 5xx responses. |
| `VLEX_BACKOFF_BASE_SECONDS` | No | `0.5` | Base vendor retry delay. |
| `VLEX_MAX_BACKOFF_SECONDS` | No | `8` | Maximum vendor retry delay. |

```bash
export VLEX_API_KEY="your-subscription-key"
VLEX_MCP_TRANSPORT=streamable-http PORT=8080 uv run --locked vlex-mcp
```

Connect to `http://127.0.0.1:8080/mcp`. Loopback bindings use the SDK's Host and Origin
protection. Any other binding requires `VLEX_MCP_ALLOWED_HOSTS`; requests must match
that allowlist and, when they include Origin, `VLEX_MCP_ALLOWED_ORIGINS`. Vendor
credentials always come from process environment variables.

## API behavior and constraints

- HTTP 429 and 5xx responses use bounded exponential retry; valid `Retry-After` values are honored
  up to the configured maximum.
- Research search follows its documented first-page contract with a page size of 10. Results are
  trimmed locally to the requested limit, which cannot exceed 10.
- Public API message pages are capped at 100 records and followed only until the requested limit
  is reached.
- Vendor error details remain in typed client exceptions; MCP errors and logging use sanitized
  messages. Successful asynchronous responses retain their `status` and `error` fields.
- Conversation SSE can be requested with `stream=true`; buffered SSE data records are returned as
  a JSON `events` array. Polling messages is the default.
- `prepare_document_upload` creates the vLex file record and returns a presigned S3 POST target;
  the caller uploads the bytes before checking file status.
- Authority Match jobs expire after four hours. The separate maximum file size is undocumented,
  so the server does not impose an invented limit.

## Development

Tests are offline and intercept HTTP requests:

```bash
uv run --locked pytest -q
uv run --locked ruff check .
```

No test or build command makes a live vLex API call.

## License

MIT. See [LICENSE](LICENSE).

## Local setup and verification

The server starts and answers MCP initialization and tool listing without credentials.
Tools report a configuration error until the required environment is provided.
Run `vlex-mcp-setup` for environment-variable instructions, then restart your MCP
launcher with those variables exported. Run `vlex-mcp-verify` to validate local
configuration (exit 2 if missing or invalid). This command does not contact the
vendor or prove authentication. Neither command saves or prints secret values.

Presigned upload targets returned by `prepare_document_upload` are short-lived secrets.
Treat their URLs and form credentials like tokens; share them only with your own upload client
and keep them out of logs, shared transcripts and public documents.
