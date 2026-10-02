# Layer 5 API. uv supplies Python itself, so the image needs no system python.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first, so a code edit does not reinstall the world.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "asyncpg>=0.30" \
      "pydantic>=2.9" "pydantic-settings>=2.6" "aiomqtt>=2.3" \
      "argon2-cffi>=23.1" "email-validator>=2.2"

COPY app ./app

# Run as nobody: this service holds the credential that can move shelf prices.
RUN useradd --no-create-home --shell /usr/sbin/nologin shelfiq
USER shelfiq

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
