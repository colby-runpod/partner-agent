FROM python:3.11-slim
WORKDIR /app
COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt
COPY intake.py intake-routing.json chat.py review.py service.py p_ops.py p_ops_notion.py p_ops_notion.json p_ops_capture.py p_ops_approval.py p_ops_linear.py P_OPS_PROMPT.md ./
ENV INTAKE_DB=/data/intake.sqlite3
EXPOSE 8080
CMD ["python", "service.py"]
