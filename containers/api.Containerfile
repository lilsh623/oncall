FROM python:3.12.8-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY mcp_servers ./mcp_servers
COPY migrations ./migrations
COPY alembic.ini ./
COPY project-packs ./project-packs
RUN pip install --no-cache-dir .

ENV ONCALL_PROJECT_PACKS_ROOT=/app/project-packs

EXPOSE 8000

CMD ["uvicorn", "oncall.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
