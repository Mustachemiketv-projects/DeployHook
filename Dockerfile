FROM python:3.14-alpine3.23

# Override at build time: docker build --build-arg DOCKER_VERSION=29.x.x .
ARG DOCKER_VERSION=29.3.0

WORKDIR /app

# Upgrade Alpine packages (addresses vulnerability scanner), install runtime + build deps
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates curl \
    && apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev libffi-dev \
    && curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-${DOCKER_VERSION}.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker

# Create non-root user
RUN addgroup -S deployhook \
    && adduser -S -D -H -G deployhook deployhook

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY app ./app

# Data dir lives inside /app which deployhook owns — no root needed at runtime
RUN mkdir -p /app/data/.secrets \
    && chown -R deployhook:deployhook /app

# Point HOME to the persistent data volume so docker login can write ~/.docker/config.json
ENV HOME=/app/data

EXPOSE 8000

USER deployhook

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
