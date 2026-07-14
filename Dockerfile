# =====================================================================
# Production-ready, secure Dockerfile for MicroStrategy BI MCP Server
# =====================================================================

FROM python:3.11-slim

LABEL maintainer="Enterprise Data Platform Team"
LABEL description="Secure MCP server wrapping MicroStrategy REST API with user-context LDAP authentication"

# Set environment defaults
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MSTR_MCP_TRANSPORT=sse \
    MSTR_MCP_HOST=0.0.0.0 \
    MSTR_MCP_PORT=8000 \
    COMPLIANCE_LOG_PATH=/app/logs/mstr_mcp_compliance.log

WORKDIR /app

# Install basic compile-time utilities (some C-extensions might need them)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create log directories and non-root user for security compliance (Principle of Least Privilege)
RUN mkdir -p /app/logs && \
    useradd -u 10001 -U -d /app -s /bin/sh mcpuser && \
    chown -R mcpuser:mcpuser /app

# Copy dependency definition and install
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy server implementation
COPY src/server.py /app/server.py

# Set proper ownership
RUN chown -R mcpuser:mcpuser /app

# Expose port (default SSE port)
EXPOSE 8000

# Switch to non-root execution context
USER mcpuser

# Add a basic healthcheck to verify the server is running
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/sse || exit 1

# Execute server
CMD ["python", "server.py"]
