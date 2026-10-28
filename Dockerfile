# Grid Alarm Triage Agent — AgentCore Runtime image.
#
# AgentCore Runtime runs containers built for linux/arm64 and serves
# the agent on port 8080 (POST /invocations, GET /ping). Build with:
#   docker buildx build --platform linux/arm64 -t grid-triage-agent .
# Multi-stage, non-root, unbuffered logs (Runtime log streams read
# stdout; buffering hides them).

FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml constraints.txt README.md ./
COPY src ./src
COPY migrations ./migrations
COPY data ./data
RUN pip install --no-cache-dir -r constraints.txt \
 && pip install --no-cache-dir --no-deps .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    SQLITE_PATH=/data/grid.db \
    OBJECT_STORE_DIR=/data/objects
RUN useradd --create-home app && mkdir -p /data && chown app:app /data
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/grid-agent* /usr/local/bin/
COPY --from=build /src/migrations /app/migrations
COPY --from=build /src/data /app/data
WORKDIR /app
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')"
CMD ["grid-agent-serve"]
