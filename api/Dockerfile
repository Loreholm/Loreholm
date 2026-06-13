# Build stage - installs dependencies
FROM python:3.11-slim AS builder

WORKDIR /app

COPY api/requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

# Runtime stage - smaller, faster
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/root/.local/bin:$PATH
ENV PG_DB=""
ENV PG_PORT="5432"
ENV PG_PW=""
ENV PG_SERVER=""
ENV PG_USER=""

# Copy only the installed packages from builder
COPY --from=builder /root/.local /root/.local

COPY api/ ./

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
