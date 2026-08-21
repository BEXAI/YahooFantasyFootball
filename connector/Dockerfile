FROM python:3.12-slim
WORKDIR /app
COPY ffl_common.py yahoo_api.py ./
COPY connector/requirements.txt connector/server.py ./connector/
RUN pip install --no-cache-dir -r connector/requirements.txt
ENV PYTHONPATH=/app \
    YAHOO_SECRETS_PATH=/secrets/yahoo.json
# CONNECTOR_TOKEN and YAHOO_SECRETS_B64 come from the host's secret store at
# deploy time — never bake either into this image (plan N5). The secrets file
# is materialized at CONTAINER START (a Fly release_command runs in a separate
# throwaway machine whose filesystem never reaches app machines).
# --no-access-log: with URL-secret auth the URL contains the credential —
# access logs must not record it.
EXPOSE 8080
WORKDIR /app/connector
CMD ["sh", "-c", "mkdir -p /secrets && printf %s \"$YAHOO_SECRETS_B64\" | base64 -d > /secrets/yahoo.json && chmod 600 /secrets/yahoo.json && exec uvicorn server:app --host 0.0.0.0 --port 8080 --no-access-log"]
