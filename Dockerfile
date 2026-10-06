FROM python:3.11-slim
WORKDIR /app
COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt
COPY intake.py intake-routing.json ./
ENV INTAKE_DB=/data/intake.sqlite3
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "intake:create_app()"]
