FROM python:3.11-slim
WORKDIR /app
COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt
COPY intake.py intake-routing.json chat.py review.py service.py ./
ENV INTAKE_DB=/data/intake.sqlite3
EXPOSE 8080
CMD ["python", "service.py"]
