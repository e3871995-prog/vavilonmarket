FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml /app/
RUN pip install --upgrade pip && pip install .

COPY app /app/app

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8080
CMD ["python", "-m", "app"]
