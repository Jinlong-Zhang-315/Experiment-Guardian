FROM python:3.12-slim AS runtime

ARG PIP_INDEX_URL

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN addgroup --system guardian && adduser --system --ingroup guardian guardian

COPY requirements ./requirements
RUN pip install -r requirements/lock-server.txt
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-deps .
COPY examples ./examples

USER guardian
EXPOSE 8000 8001
CMD ["uvicorn", "experiment_guardian.main:app", "--host", "0.0.0.0", "--port", "8000"]
