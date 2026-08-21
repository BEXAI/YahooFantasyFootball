FROM python:3.12-slim
WORKDIR /app
COPY ffl_common.py yahoo_api.py ./
COPY connector/requirements.txt connector/server.py ./connector/
RUN pip install --no-cache-dir -r connector/requirements.txt
ENV PYTHONPATH=/app \
    YAHOO_SECRETS_PATH=/secrets/yahoo.json
# CONNECTOR_TOKEN and the mounted secrets file come from the host's secret
# store at deploy time — never bake either into this image (plan N5).
EXPOSE 8080
WORKDIR /app/connector
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
