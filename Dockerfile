FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 mcp
USER mcp
EXPOSE 8000
CMD ["mcp-mde-kql", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
