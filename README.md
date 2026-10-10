# vLex MCP server

[![CI](https://github.com/RosenAdvertising/vlex-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/RosenAdvertising/vlex-mcp/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP 2026-07-28](https://img.shields.io/badge/MCP-2026--07--28-7C3AED.svg)](https://modelcontextprotocol.io)

Connect Claude and other MCP clients to vLex to search legal authorities, run research questions, work with Vincent AI workflows and check citations in uploaded documents.

vLex MCP server is a [Model Context Protocol](https://modelcontextprotocol.io) server for [vLex](https://vlex.com/), the legal research platform, and its Vincent AI assistant. It registers 14 tools that read and write, covering legal search, Vincent research assignments and workflows, document upload and authority matching. It runs over stdio by default, for desktop clients such as Claude Desktop, and offers an opt-in stateless Streamable HTTP mode that implements MCP specification 2026-07-28. vLex credentials stay on the machine that runs the server: the subscription key comes from an environment variable, never from tool arguments or requests. Every tool that starts a job, creates a record or sends a message requires `confirm=true`, and all tools return structured JSON.

## Features

- Search legal authorities and secondary materials; retrieve selected documents as HTML.
- Discover accepted research jurisdiction identifiers and account-enabled Vincent workflows.
- Start and poll asynchronous legal research assignments.
- Create document upload records, check processing status, and start or poll authority matching.
- Start Vincent workflow conversations, read messages, continue conversations, and resolve actions.
- Require `confirm=true` for tools that start jobs, create records, or otherwise mutate vLex data.

## Tools

The server registers 14 tools. Tools that start a job, create a record or send a message require `confirm=true`.

| Tool | What it does |
| --- | --- |
| `search_authorities` | Search legal authorities and secondary materials, filtered by jurisdiction, content type and sort order. Returns up to 10 results. |
| `retrieve_legal_document` | Retrieve the full HTML of a document by its numeric vLex document ID. |
| `list_research_jurisdictions` | List the jurisdiction identifiers that legal search accepts. |
| `ask_research_question` | Start a Vincent research assignment to answer a legal research question. Requires `confirm=true`. |
| `get_research_answer` | Check a Vincent research assignment and return its answer when it is available. |
| `list_vincent_workflows` | List the Vincent workflows enabled for your vLex account. |
| `analyze_with_vincent` | Start an enabled Vincent workflow over a question or uploaded documents. Requires `confirm=true`. |
| `get_conversation_messages` | Read a Vincent conversation transcript and see whether it is still running. |
| `continue_conversation` | Send a follow-up question or clarification answer to Vincent. Requires `confirm=true`. |
| `resolve_conversation_action` | Approve, reject or answer a pending Vincent workflow action. Requires `confirm=true`. |
| `prepare_document_upload` | Create a vLex file record and return an upload target. Requires `confirm=true`. |
| `get_document_upload_status` | Check whether a document upload is ready or has failed. |
| `start_authority_match` | Start checking the citations in an uploaded document. Requires `confirm=true`. |
| `get_authority_match_result` | Check an authority-match job and return the verified citations when they are ready. |

Use `list_research_jurisdictions` and `list_vincent_workflows` instead of guessing jurisdiction identifiers or assuming a workflow is enabled for your account.

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- A vLex subscription key for the target developer portal account
- An MCP client such as Claude Desktop

## Installation

Install [uv](https://docs.astral.sh/uv/), then clone the repository and install its locked dependencies:

```bash
git clone https://github.com/RosenAdvertising/vlex-mcp.git
cd vlex-mcp
uv sync --locked
```

## Configuration

The server reads its configuration from process environment variables only and does not load a dotenv file. `.env.example` lists the settings; for local development, keep any real credentials in an untracked `.env` file or your environment.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `VLEX_API_KEY` | Yes | none | Subscription key from your developer.vlex.com account, sent in the `Ocp-Apim-Subscription-Key` header. |
| `VLEX_HTTP_TIMEOUT_SECONDS` | No | `30` | HTTP request timeout in seconds; must be greater than zero. |
| `VLEX_MAX_RETRIES` | No | `3` | Retries for HTTP 429 and 5xx responses; must be zero or greater. |
| `VLEX_BACKOFF_BASE_SECONDS` | No | `0.5` | Base delay in seconds for exponential retry backoff; must be greater than zero. |
| `VLEX_MAX_BACKOFF_SECONDS` | No | `8` | Maximum retry delay in seconds; must be at least the base delay. |

The server calls the vLex Research v1, Vincent Research Assistant v1 and Vincent AI Public API v2 services at fixed hosts. It does not assume a separate sandbox or staging host.

Initialization and tool listing work without credentials. Tool calls return a configuration error until `VLEX_API_KEY` is set. Two commands help with local setup:

```bash
uv run vlex-mcp-setup
uv run vlex-mcp-verify
```

`vlex-mcp-setup` prints instructions for the environment variables: export them in the environment your MCP client uses, then restart the client. `vlex-mcp-verify` checks the local configuration and exits with code 2 if it is missing or invalid. It does not contact vLex or prove authentication. Neither command saves or prints secret values.

## Usage with Claude Desktop

Add the server to Claude Desktop's configuration file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "vlex": {
      "command": "uv",
      "args": ["run", "--locked", "--directory", "/absolute/path/to/vlex-mcp", "vlex-mcp"],
      "env": {
        "VLEX_API_KEY": "replace-with-your-subscription-key"
      }
    }
  }
}
```

Replace `/absolute/path/to/vlex-mcp` with the path of your clone, then restart Claude Desktop. Keep real credentials in your client's protected environment or secret store. Any other stdio MCP client uses the same command and arguments.

## HTTP mode

Stdio is the default. Set `VLEX_MCP_TRANSPORT=streamable-http` to serve the stateless Streamable HTTP transport from MCP specification 2026-07-28 at `/mcp`. Each request stands alone: no initialization handshake and no `Mcp-Session-Id`. Clients on earlier protocol versions are served on the same endpoint. Responses use the SDK's default SSE streaming, so a client that disconnects cancels its request.

> **Security: this endpoint has no authentication and no TLS.** Anyone who can reach the port can run every tool, including write tools, with this server's vendor credentials. Keep the default loopback bind (`127.0.0.1`), or put the server behind an authenticating TLS proxy on a private network. `VLEX_MCP_ALLOWED_HOSTS` and `VLEX_MCP_ALLOWED_ORIGINS` protect against browser DNS rebinding, not against direct callers. A proxy in front of it needs connection and idle timeouts: a legacy-style `GET /mcp` with `Accept: text/event-stream` holds a stream open until the client disconnects.

| Variable | Default | Purpose |
| --- | --- | --- |
| `VLEX_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`. An empty value selects `stdio`; any other value exits. |
| `VLEX_MCP_HOST` | `127.0.0.1` | Bind address. `127.0.0.1`, `localhost` and `::1` use the SDK's built-in Host and Origin checks; any other value requires `VLEX_MCP_ALLOWED_HOSTS`. |
| `PORT` | `8080` | Port; must be an integer. |
| `VLEX_MCP_ALLOWED_HOSTS` | unset | Comma-separated `Host` header values accepted on a non-loopback bind, such as `mcp.example.com:8080` or `mcp.example.com:*`. The server exits if it is unset on a non-loopback bind. |
| `VLEX_MCP_ALLOWED_ORIGINS` | unset | Comma-separated `Origin` values accepted on a non-loopback bind, such as `https://app.example.com`. Requests without an `Origin` header are accepted; with this unset, any request that sends an `Origin` header is rejected. |

vLex credentials come from the same configuration as stdio (see [Configuration](#configuration)), never from the request.

```bash
export VLEX_API_KEY="replace-with-your-subscription-key"
VLEX_MCP_TRANSPORT=streamable-http PORT=8080 uv run --locked vlex-mcp
```

Point the MCP client at `http://127.0.0.1:8080/mcp`.

## Error handling

A failed tool call returns an MCP error result (`isError`) with a fixed message. vLex's own error text and response bodies are never returned or logged.

- Missing or invalid configuration: a message that names `VLEX_API_KEY` and `vlex-mcp-setup`. This includes an invalid timeout, retry or backoff value, which is reported when a tool is called, not at startup.
- HTTP 401, 403, 404 and 429: a short message that says what to check (credentials, account permissions, the record ID, or waiting before a retry).
- HTTP 5xx: a "Service unavailable" message that asks you to check whether a write completed before retrying.
- Any other HTTP status, or a response that does not have the expected shape: "Request rejected or response invalid. Check inputs and API configuration."
- Connection failures and timeouts: "Connection failed; the operation outcome may be unknown."
- Invalid tool input: "Invalid tool input or response."
- Checks made inside a tool, such as a missing `confirm=true`, a callback URL that is not a complete HTTPS URL, or an invalid identifier, return their own message that names the problem.

HTTP 429 and 5xx responses are retried up to `VLEX_MAX_RETRIES` times (default 3). The delay starts at `VLEX_BACKOFF_BASE_SECONDS` and doubles on each retry, capped at `VLEX_MAX_BACKOFF_SECONDS`. A valid `Retry-After` header, in seconds or as an HTTP date, sets the delay instead, also capped at the maximum. Retries apply to writes as well as reads, so after a rate-limit or service error check whether a write completed before repeating it. Connection failures and timeouts are not retried. The request timeout is `VLEX_HTTP_TIMEOUT_SECONDS` (default 30).

The server exits with status 1 and a message that names the setting when `VLEX_MCP_TRANSPORT` is unsupported, `PORT` is not an integer, or `VLEX_MCP_HOST` is not a loopback address and `VLEX_MCP_ALLOWED_HOSTS` is unset.

## API behavior and constraints

- Research search follows its documented first-page contract with a page size of 10. Results are trimmed locally to the requested limit, which cannot exceed 10.
- Public API message pages are capped at 100 records and followed only until the requested limit is reached.
- Successful asynchronous responses retain their `status` and `error` fields.
- Conversation SSE can be requested with `stream=true`; buffered SSE data records are returned as a JSON `events` array. Polling messages is the default.
- `prepare_document_upload` creates the vLex file record and returns a presigned S3 POST target; the caller uploads the bytes before checking file status.
- Authority Match jobs expire after four hours. The separate maximum file size is undocumented, so the server does not impose an invented limit.

## Presigned upload targets

Presigned upload targets returned by `prepare_document_upload` are short-lived secrets. Treat their URLs and form credentials like tokens; share them only with your own upload client and keep them out of logs, shared transcripts and public documents.

## Testing

The test suite runs offline and needs no vLex account: every vLex API call is intercepted at the HTTP layer with `respx`, and no test or build command makes a live vLex API call. It covers each tool's requests and responses, write confirmation, identifier validation, pagination and retries, error handling that withholds vendor text, configuration loading, and the Streamable HTTP transport including the 2026-07-28 wire format, Host and Origin checks, stateless requests and cancellation on disconnect.

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
```

CI runs the suite on every push and pull request to `main`.

## License

MIT. See [LICENSE](LICENSE).
