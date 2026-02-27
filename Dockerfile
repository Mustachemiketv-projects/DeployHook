FROM python:3.13-slim

WORKDIR /app

# Install system deps: Docker CLI
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-29.2.1.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker

# Create non-root user and a docker socket group (GID matched at runtime via group_add)
RUN addgroup --system deployhook \
    && adduser --system --ingroup deployhook --no-create-home deployhook

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Data dir lives inside /app which deployhook owns — no root needed at runtime
RUN mkdir -p /app/data/.secrets \
    && chown -R deployhook:deployhook /app

USER deployhook

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
