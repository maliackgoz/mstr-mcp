import os
import base64
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any, List
import pandas as pd

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from mstrio.connection import Connection
from mstrio.server.project import list_projects
from mstrio.project_objects.report import list_reports, Report
from mstrio.project_objects import OlapCube, SuperCube

# =====================================================================
# Logging Configuration (Compliance & Auditing)
# =====================================================================
logger = logging.getLogger("mstr_mcp_server")
logger.setLevel(logging.INFO)

log_formatter = logging.Formatter(
    '[%(asctime)s] [%(levelname)s] [COMPLIANCE] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 1. Console handler (logs to stderr so it does not pollute stdio channel)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

# 2. Rotating file handler for compliance audit trail
log_file_path = os.getenv("COMPLIANCE_LOG_PATH", "mstr_mcp_compliance.log")
try:
    file_handler = RotatingFileHandler(log_file_path, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
    logger.info(f"Compliance logging initialized. Writing to: {log_file_path}")
except Exception as log_err:
    logger.error(f"Failed to initialize compliance log file: {log_err}")

def log_compliance(username: str, tool_name: str, project_id: Optional[str], target_id: Optional[str], status: str, details: str):
    """
    Standardized logging wrapper for compliance auditing. 
    Usernames are masked to comply with bank data protection rules.
    """
    masked_user = "UNKNOWN"
    if username:
        if len(username) > 3:
            masked_user = username[:3] + "***"
        else:
            masked_user = "***"
    
    logger.info(
        f"User: {masked_user} | Tool: {tool_name} | ProjectID: {project_id or 'N/A'} | "
        f"TargetID: {target_id or 'N/A'} | Status: {status} | Details: {details}"
    )

# =====================================================================
# Environment Config Defaults
# =====================================================================
MSTR_BASE_URL = os.getenv("MSTR_BASE_URL", "https://localhost/MicroStrategyLibrary/api")
MSTR_DEFAULT_PROJECT_ID = os.getenv("MSTR_PROJECT_ID", "")
MSTR_SSL_VERIFY = os.getenv("MSTR_SSL_VERIFY", "True").lower() in ("true", "1", "yes")

# Disable SSL warnings if validation is disabled (common for internal dev servers)
if not MSTR_SSL_VERIFY:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    logger.warning("SSL verification is disabled. Do not use this configuration in production.")

# Initialize FastMCP Server
mcp = FastMCP(
    name="microstrategy-bi-agent",
    description="A secure MCP server wrapping MicroStrategy REST API to list and execute reports using user LDAP context."
)

# =====================================================================
# Authentication & Extraction Logic
# =====================================================================
def extract_credentials() -> Dict[str, Any]:
    """
    Extracts LDAP username and password from request headers (SSO) or env variables (fallback).
    Supported headers:
    1. Authorization: Basic <base64(username:password)>
    2. Custom headers: X-User-LDAP-Username & X-User-LDAP-Password (or without 'User-' prefix)
    3. Direct identity/session token: X-MSTR-AuthToken
    """
    headers = {}
    try:
        headers = get_http_headers() or {}
    except Exception as e:
        logger.warning(f"Could not retrieve HTTP headers (running in STDIO mode or prior to handshake): {e}")

    # Check for direct MSTR Auth Token
    mstr_token = headers.get("x-mstr-authtoken") or headers.get("X-MSTR-AuthToken")
    if mstr_token:
        return {"token": mstr_token}

    # Check Authorization header (Basic Auth)
    auth_header = headers.get("authorization") or headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("basic "):
        try:
            encoded_creds = auth_header.split(" ", 1)[1]
            decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
            if ":" in decoded_creds:
                username, password = decoded_creds.split(":", 1)
                return {"username": username, "password": password}
        except Exception as e:
            logger.error(f"Error parsing Basic Authorization header: {e}")

    # Check custom LDAP headers
    username = (
        headers.get("x-user-ldap-username") or 
        headers.get("X-User-LDAP-Username") or
        headers.get("x-ldap-username") or 
        headers.get("X-LDAP-Username")
    )
    password = (
        headers.get("x-user-ldap-password") or 
        headers.get("X-User-LDAP-Password") or
        headers.get("x-ldap-password") or 
        headers.get("X-LDAP-Password")
    )
    if username and password:
        return {"username": username, "password": password}

    # Fallback to env variables for local testing/dev
    env_username = os.getenv("MSTR_USERNAME")
    env_password = os.getenv("MSTR_PASSWORD")
    if env_username and env_password:
        return {"username": env_username, "password": env_password}

    return {}

def create_connection(creds: Dict[str, Any], project_id: Optional[str] = None) -> Connection:
    """
    Instantiates a Connection object using the provided credentials.
    Supports LDAP Authentication mode (loginMode: 16) or existing Identity Token.
    """
    if not creds:
        raise ValueError("No authentication credentials provided or extracted from request.")

    kwargs = {
        "base_url": MSTR_BASE_URL,
        "ssl_verify": MSTR_SSL_VERIFY,
    }
    
    if project_id:
        kwargs["project_id"] = project_id
    elif MSTR_DEFAULT_PROJECT_ID:
        kwargs["project_id"] = MSTR_DEFAULT_PROJECT_ID

    if "token" in creds:
        # Pre-authenticated session
        kwargs["identity_token"] = creds["token"]
    else:
        # LDAP Authentication
        kwargs["username"] = creds["username"]
        kwargs["password"] = creds["password"]
        kwargs["login_mode"] = 16  # LDAP auth mode

    try:
        conn = Connection(**kwargs)
        if not conn.is_alive():
            conn.connect()
        return conn
    except Exception as e:
        raise RuntimeError(f"MicroStrategy REST API connection/auth failed: {e}")

# =====================================================================
# Tools Exposed to Onyx / LLM
# =====================================================================

@mcp.tool
async def list_mstr_projects() -> str:
    """
    Lists all MicroStrategy projects the currently authenticated user has access to.
    
    Returns:
        A Markdown table displaying available projects.
    """
    creds = extract_credentials()
    username = creds.get("username", "TOKEN_AUTH" if "token" in creds else "UNKNOWN")
    
    if not creds:
        log_compliance("UNKNOWN", "list_mstr_projects", None, None, "DENIED", "No credentials supplied")
        return "Error: No user session credentials found. Please ensure you are authenticated in Onyx."

    log_compliance(username, "list_mstr_projects", None, None, "REQUESTED", "Listing project directory")
    
    try:
        # Establish connection without a project ID first to list them
        conn = create_connection(creds)
        projects = list_projects(connection=conn)
        
        if not projects:
            log_compliance(username, "list_mstr_projects", None, None, "SUCCESS", "Zero projects found")
            return "No projects found or accessible for the current user."

        proj_data = []
        for p in projects:
            proj_data.append({
                "Project ID": p.id,
                "Project Name": p.name,
                "Description": p.description or "No description provided."
            })
        
        df = pd.DataFrame(proj_data)
        markdown_table = df.to_markdown(index=False)
        
        log_compliance(username, "list_mstr_projects", None, None, "SUCCESS", f"Listed {len(projects)} projects")
        return f"### Available MicroStrategy Projects\n\n{markdown_table}"
    
    except Exception as e:
        log_compliance(username, "list_mstr_projects", None, None, "FAILED", str(e))
        return f"Error: Failed to list projects. {str(e)}"

@mcp.tool
async def list_mstr_reports(project_id: Optional[str] = None) -> str:
    """
    Lists all reports, OLAP cubes, and Super cubes the user is authorized to see in the specified project.
    
    Args:
        project_id: The ID of the MicroStrategy project (optional). If not specified,
                    the default project ID from the environment is used.
    """
    creds = extract_credentials()
    username = creds.get("username", "TOKEN_AUTH" if "token" in creds else "UNKNOWN")

    if not creds:
        log_compliance("UNKNOWN", "list_mstr_reports", project_id, None, "DENIED", "No credentials supplied")
        return "Error: No user session credentials found. Please ensure you are authenticated in Onyx."

    active_project_id = project_id or MSTR_DEFAULT_PROJECT_ID
    if not active_project_id:
        log_compliance(username, "list_mstr_reports", None, None, "BAD_REQUEST", "No project context specified")
        return (
            "Error: No project ID specified or configured. Please run `list_mstr_projects` "
            "first, and pass your target project's ID to `project_id`."
        )

    log_compliance(username, "list_mstr_reports", active_project_id, None, "REQUESTED", "Listing reports and cubes")

    try:
        conn = create_connection(creds, project_id=active_project_id)
        reports_list = []

        # 1. Fetch Reports
        try:
            reports = list_reports(connection=conn)
            for r in reports:
                reports_list.append({
                    "Name": r.name,
                    "ID": r.id,
                    "Type": "Report",
                    "Description": r.description or ""
                })
        except Exception as re:
            logger.warning(f"Non-fatal error listing reports in project {active_project_id}: {re}")

        # 2. Fetch OLAP Cubes
        try:
            from mstrio.project_objects import list_olap_cubes
            cubes = list_olap_cubes(connection=conn)
            for c in cubes:
                reports_list.append({
                    "Name": c.name,
                    "ID": c.id,
                    "Type": "OLAP Cube",
                    "Description": c.description or ""
                })
        except Exception as ce:
            logger.warning(f"Non-fatal error listing OLAP cubes in project {active_project_id}: {ce}")

        # 3. Fetch Super Cubes
        try:
            from mstrio.project_objects import list_super_cubes
            super_cubes = list_super_cubes(connection=conn)
            for sc in super_cubes:
                reports_list.append({
                    "Name": sc.name,
                    "ID": sc.id,
                    "Type": "Super Cube",
                    "Description": sc.description or ""
                })
        except Exception as sce:
            logger.warning(f"Non-fatal error listing Super Cubes in project {active_project_id}: {sce}")

        if not reports_list:
            log_compliance(username, "list_mstr_reports", active_project_id, None, "SUCCESS", "Zero objects found")
            return f"No reports or cubes found in project '{active_project_id}' for the current user."

        df = pd.DataFrame(reports_list)
        markdown_table = df.to_markdown(index=False)
        
        log_compliance(username, "list_mstr_reports", active_project_id, None, "SUCCESS", f"Listed {len(reports_list)} reports/cubes")
        return f"### Accessible Reports & Cubes in Project '{active_project_id}'\n\n{markdown_table}"

    except Exception as e:
        log_compliance(username, "list_mstr_reports", active_project_id, None, "FAILED", str(e))
        return f"Error: Failed to list reports/cubes. {str(e)}"

@mcp.tool
async def execute_mstr_report(report_id: str, project_id: Optional[str] = None, limit: int = 100) -> str:
    """
    Executes a MicroStrategy report, OLAP cube, or Super cube and returns the dataset as a Markdown table.
    
    Args:
        report_id: The ID of the report or cube to execute.
        project_id: The ID of the MicroStrategy project (optional). If not specified,
                    the default project ID from the environment is used.
        limit: The maximum number of rows to return (default 100). Prevents context window exhaustion.
    """
    creds = extract_credentials()
    username = creds.get("username", "TOKEN_AUTH" if "token" in creds else "UNKNOWN")

    if not creds:
        log_compliance("UNKNOWN", "execute_mstr_report", project_id, report_id, "DENIED", "No credentials supplied")
        return "Error: No user session credentials found. Please ensure you are authenticated in Onyx."

    active_project_id = project_id or MSTR_DEFAULT_PROJECT_ID
    if not active_project_id:
        log_compliance(username, "execute_mstr_report", None, report_id, "BAD_REQUEST", "No project context specified")
        return (
            "Error: No project ID specified or configured. Please run `list_mstr_projects` "
            "first, and pass your target project's ID to `project_id`."
        )

    log_compliance(username, "execute_mstr_report", active_project_id, report_id, "REQUESTED", f"Executing dataset with limit={limit}")

    try:
        conn = create_connection(creds, project_id=active_project_id)
        df = None
        error_msgs = []

        # 1. Attempt Report Execution
        try:
            logger.info(f"Attempting to load object '{report_id}' as Report")
            report_obj = Report(connection=conn, report_id=report_id)
            df = report_obj.to_dataframe()
            logger.info(f"Successfully executed Report '{report_id}'")
        except Exception as e_report:
            error_msgs.append(f"Report execution failed: {e_report}")

            # 2. Attempt OLAP Cube Execution
            try:
                logger.info(f"Attempting to load object '{report_id}' as OlapCube")
                cube_obj = OlapCube(connection=conn, cube_id=report_id)
                df = cube_obj.to_dataframe()
                logger.info(f"Successfully executed OlapCube '{report_id}'")
            except Exception as e_cube:
                error_msgs.append(f"OlapCube execution failed: {e_cube}")

                # 3. Attempt Super Cube Execution
                try:
                    logger.info(f"Attempting to load object '{report_id}' as SuperCube")
                    sc_obj = SuperCube(connection=conn, id=report_id)
                    df = sc_obj.to_dataframe()
                    logger.info(f"Successfully executed SuperCube '{report_id}'")
                except Exception as e_scube:
                    error_msgs.append(f"SuperCube execution failed: {e_scube}")

        if df is None or df.empty:
            all_errors = "\n".join(error_msgs)
            log_compliance(username, "execute_mstr_report", active_project_id, report_id, "FAILED", "Dataframe empty or loading failed")
            return f"Error: Could not execute or fetch data for ID '{report_id}'.\nDetails:\n{all_errors}"

        # Clean/sanitize index and columns for standard Markdown presentation
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ['_'.join(str(level) for level in col).strip() for col in df.columns]
        else:
            df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]

        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index()
        elif df.index.name:
            df = df.reset_index()

        df = df.fillna("")

        # Paginate results
        total_rows = len(df)
        if total_rows > limit:
            df = df.head(limit)
            truncated_msg = f"\n\n*Note: Output truncated to the first {limit} rows of {total_rows} total rows.*"
        else:
            truncated_msg = ""

        # Format output as Markdown
        markdown_table = df.to_markdown(index=False)
        result_output = f"### Dataset Results for ID: {report_id}\n\n{markdown_table}{truncated_msg}"
        
        log_compliance(username, "execute_mstr_report", active_project_id, report_id, "SUCCESS", f"Returned {len(df)} rows")
        return result_output

    except Exception as e:
        log_compliance(username, "execute_mstr_report", active_project_id, report_id, "FAILED", str(e))
        return f"Error: Failed to execute report/cube. {str(e)}"

# =====================================================================
# Server Startup Entry Point
# =====================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MicroStrategy BI Agent MCP Server")
    parser.add_argument("--transport", type=str, default="sse", choices=["stdio", "sse"], help="MCP transport type (stdio or sse)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="HTTP host for SSE server")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port for SSE server")
    args = parser.parse_args()

    # Environment variables overrides (typical for Docker environments)
    transport_mode = os.getenv("MSTR_MCP_TRANSPORT", args.transport)
    host = os.getenv("MSTR_MCP_HOST", args.host)
    port = int(os.getenv("MSTR_MCP_PORT", str(args.port)))

    logger.info(f"Starting FastMCP server with transport={transport_mode}")
    if transport_mode == "sse":
        logger.info(f"SSE Server running on http://{host}:{port}/sse")
        mcp.run(transport="sse", host=host, port=port)
    else:
        logger.info("STDIO Server running")
        mcp.run(transport="stdio")
