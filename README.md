# MicroStrategy (MSTR) BI Agent MCP Server

Phase 1 of the Enterprise AI Architecture at our highly regulated state bank: a secure Model Context Protocol (MCP) server that wraps the MicroStrategy REST API, enforcing native Row-Level Security (RLS) and object-level permissions using **Pass-Through LDAP Authentication**.

This server is designed to integrate as an HTTP/SSE tool within **Onyx** (formerly Danswer), allowing our local **Qwen 27B** LLM to execute reports and return data to authorized bank employees without using master API keys.

---

## Architecture Overview

```
[Bank Employee]
      │
      ▼
[Onyx Web UI (SSO Authenticated)]
      │
      ▼ (HTTP /sse request with User LDAP credentials/Basic Auth)
[MSTR MCP Server (Docker Container)]
      │
      ├─► Dynamic Header Extraction
      ├─► Instantiates mstrio Connection (loginMode: 16 - LDAP)
      │
      ▼ (REST request with User Context)
[MicroStrategy Library API / IServer]
```

---

## Deliverables & Project Structure

*   `src/server.py`: Custom FastMCP server code with dynamic header parsing, pandas parsing, and rotating compliance logs.
*   `requirements.txt`: Python package requirements (`fastmcp`, `mstrio-py`, `pandas`, `tabulate`, `requests`, `urllib3`).
*   `Dockerfile`: Secure, non-root, lightweight `slim-python` container definition.
*   `onyx_system_prompt.txt`: Strict system instructions for Qwen 27B (forbids hallucinations, outlines tool calling pipeline).

---

## Configuration Variables

The server is configured using the following environment variables:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `MSTR_BASE_URL` | MicroStrategy Library REST API URL | `https://localhost/MicroStrategyLibrary/api` |
| `MSTR_PROJECT_ID` | Default MicroStrategy Project ID | `""` (Required if querying specific project reports) |
| `MSTR_SSL_VERIFY` | Verify SSL certificates (`True` / `False`) | `True` |
| `MSTR_MCP_TRANSPORT` | Server transport channel (`sse` / `stdio`) | `sse` |
| `MSTR_MCP_HOST` | Host address to bind the SSE server to | `0.0.0.0` |
| `MSTR_MCP_PORT` | Port to bind the SSE server to | `8000` |
| `COMPLIANCE_LOG_PATH` | Destination file path for compliance audit log | `mstr_mcp_compliance.log` |

---

## Quick Start & Deployment

### 1. Build the Container
Run this command from the root of this project:
```bash
# Standard Build
docker build -t mstr-mcp-server:latest .

# Proxy-aware Build (Required for internal bank deployments)
docker build \
  --build-arg http_proxy="http://proxy.internal-bank.com:8080" \
  --build-arg https_proxy="http://proxy.internal-bank.com:8080" \
  -t mstr-mcp-server:latest .
```

### 2. Run the Container
Launch the container, mounting the log folder to your host's secure audit path:
```bash
docker run -d \
  --name mstr-mcp-agent \
  -p 8000:8000 \
  -e MSTR_BASE_URL="https://mstr-library.internal-bank.com/MicroStrategyLibrary/api" \
  -e MSTR_SSL_VERIFY="True" \
  -v /var/log/mcp-audit:/app/logs \
  mstr-mcp-server:latest
```

### 3. Register in Onyx Admin UI
*   Go to **Onyx Admin UI** -> **Tools** -> **Add Custom Tool**.
*   Select **HTTP/SSE**.
*   Enter the SSE Endpoint URL: `http://<mstr-mcp-agent-ip>:8000/sse`
*   Ensure Onyx is configured to pass the user's LDAP credentials (Basic Authentication header or custom `X-LDAP-*` headers) down to the tool during invocation.

---

## Exposed MCP Tools

The server registers three tools with FastMCP:

1.  `list_mstr_projects`
    *   **Description**: Retrieves all MicroStrategy projects the authenticated user's LDAP account is authorized to view.
    *   **Arguments**: None.
2.  `list_mstr_reports`
    *   **Description**: Lists all Reports, OLAP Cubes, and Super Cubes in the specified project.
    *   **Arguments**:
        *   `project_id` (optional, string): MicroStrategy project ID. If omitted, uses the default `MSTR_PROJECT_ID` env.
3.  `execute_mstr_report`
    *   **Description**: Runs a report or cube and returns the dataset as a clean Markdown table.
    *   **Arguments**:
        *   `report_id` (required, string): ID of the report/cube.
        *   `project_id` (optional, string): MicroStrategy project ID.
        *   `limit` (optional, integer): Maximum number of rows to return (default is `100` to prevent context window bloat).

---

## Auditing and Compliance

All actions are logged to the file specified in `COMPLIANCE_LOG_PATH` in a standardized, SIEM-parseable format. 
*   **Username Masking**: The username is always masked (e.g., `jdo***`) to prevent leaking PII.
*   **Credential Protection**: Passwords are never written to any log file.
*   **Data Protection**: Raw dataset records/cells are never written to the audit log.

Log Example:
```text
[2026-07-14 16:20:05] [INFO] [COMPLIANCE] User: jdo*** | Tool: execute_mstr_report | ProjectID: B129A8... | TargetID: R98721... | Status: SUCCESS | Details: Returned 45 rows
```

---

## Development & Testing

For local verification on Python 3.10+, install dependencies:
```bash
pip install -r requirements.txt
```

To run the server in STDIO mode for testing (using local environment fallbacks):
```bash
export MSTR_USERNAME="test_ldap_user"
export MSTR_PASSWORD="test_ldap_password"
export MSTR_BASE_URL="https://mstr-library.internal-bank.com/MicroStrategyLibrary/api"
export MSTR_PROJECT_ID="YOUR_PROJECT_ID"
export MSTR_SSL_VERIFY="False" # For local testing

python src/server.py --transport stdio
```
