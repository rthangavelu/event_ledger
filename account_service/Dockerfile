# Build context is the repo root so we can include the shared `common` package.
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY account_service/requirements.txt ./account_service/requirements.txt
RUN pip install --no-cache-dir -r account_service/requirements.txt

COPY common ./common
COPY account_service ./account_service

EXPOSE 8001

CMD ["uvicorn", "account_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
