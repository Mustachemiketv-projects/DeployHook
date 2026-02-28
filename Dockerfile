FROM python:3.13-alpine

WORKDIR /app

# Install system deps: Docker CLI static binary
RUN apk add --no-cache ca-certificates curl \
    && curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-29.2.1.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker

# Create non-root user
RUN addgroup -S deployhook \
    && adduser -S -D -H -G deployhook deployhook

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip==26.0.1 \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Data dir lives inside /app which deployhook owns — no root needed at runtime
RUN mkdir -p /app/data/.secrets \
    && chown -R deployhook:deployhook /app

# Point HOME to the persistent data volume so docker login can write ~/.docker/config.json
ENV HOME=/app/data

USER deployhook

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
