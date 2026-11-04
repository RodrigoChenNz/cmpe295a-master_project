FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt
COPY services/dashboard-api/app ./app
COPY retail ./retail
COPY contracts ./contracts
COPY infra/postgres/migrations ./migrations
RUN useradd --no-create-home --shell /usr/sbin/nologin shelfiq
USER shelfiq
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
