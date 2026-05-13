FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    HF_HOME=/tmp/hf

WORKDIR /app

COPY pyproject.toml /app/
RUN pip install --upgrade pip && pip install .

COPY app /app/app
COPY main.py /app/main.py

# Hugging Face Spaces uses /data for persistent storage when enabled; default to a writable subdir.
ENV DATABASE_URL="sqlite+aiosqlite:////data/vavilon.db"

RUN mkdir -p /data && chmod -R 777 /data

EXPOSE 7860
CMD ["python", "-m", "app"]
