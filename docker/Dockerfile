FROM python:3.11-slim

WORKDIR /app

# Keep it small; build deps not needed.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir -U pip \
  && pip install --no-cache-dir -e .

EXPOSE 8080

ENV RAP_HOST=0.0.0.0
ENV RAP_PORT=8080
ENV RAP_LOG_DIR=/app/logs

CMD ["rap-proxy", "--host", "0.0.0.0", "--port", "8080"]
