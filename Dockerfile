FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/data/requests.sqlite3
ENV FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && groupadd --system signup \
    && useradd --system --gid signup --home-dir /nonexistent signup \
    && mkdir -p /data \
    && chown -R signup:signup /app /data

COPY service /app/service
RUN chown -R signup:signup /app

USER signup
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn service.app:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips=$FORWARDED_ALLOW_IPS"]
