FROM python:3.12-slim AS runtime

ARG PIP_INDEX_URL

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN addgroup --system guardian && adduser --system --ingroup guardian guardian

COPY pyproject.toml README.md alembic.ini ./
COPY requirements ./requirements
COPY src ./src
COPY migrations ./migrations
RUN pip install -r requirements/lock-server.txt \
    && pip install --no-deps .

USER guardian
EXPOSE 8000 8001
CMD ["uvicorn", "experiment_guardian.main:app", "--host", "0.0.0.0", "--port", "8000"]
