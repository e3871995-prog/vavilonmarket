FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml /app/
RUN pip install --upgrade pip && pip install .

COPY app /app/app
COPY main.py /app/main.py

# Default to a writable local data dir. Override DATABASE_URL via env for persistent disks.
ENV DATABASE_URL="sqlite+aiosqlite:///./data/vavilon.db"
RUN mkdir -p /app/data && chmod -R 777 /app/data

EXPOSE 8080
CMD ["python", "-m", "app"]
