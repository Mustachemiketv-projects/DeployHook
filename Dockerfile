FROM python:3.11-slim

WORKDIR /app

# Install system deps: Docker CLI (official static binary) + curl
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-27.3.1.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# /etc/deployhook is the persistent data dir (bind-mounted at runtime)
RUN mkdir -p /etc/deployhook/.secrets

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
