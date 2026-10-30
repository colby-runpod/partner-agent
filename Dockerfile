FROM python:3.11-slim
WORKDIR /app
COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt
COPY lead_commands.py weekly_feedback.py partner_sources.py gmail_intake.py fresh_start.py storage.py storage_errors.py postgres_store.py postgres_ledger.py postgres_worker.py recovery.py standby.py outbox.py screening.py INBOUND_SCREENING_POLICY.md intake.py intake-routing.json assistant.py chat.py review.py tasks.py service.py p_ops.py p_ops_notion.py p_ops_notion.json p_ops_capture.py p_ops_approval.py p_ops_linear.py p_ops_inbox.py p_ops_triage.py p_ops_shadow.py p_ops_notifications.py p_ops_eval.py p_ops_test_lab.py p_ops_workflow.py p_ops_runtime.py p_ops_service.py P_OPS_PROMPT.md ./
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://truststore.pki.rds.amazonaws.com/us-east-2/us-east-2-bundle.pem', '/app/rds-ca.pem')"
COPY evals ./evals
COPY assets ./assets
ENV INTAKE_DB=/data/intake.sqlite3
EXPOSE 8080
CMD ["python", "service.py"]
