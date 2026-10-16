"""Standalone p-OPS receiver, review dashboard, and single background worker.

This runtime is intentionally separate from the existing Slack chat service.
Every external side effect retains its module-level approval and environment
gate. The dashboard only changes persisted approval state.
"""
import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.parse
from zoneinfo import ZoneInfo

from intake import Application, api, verify
from p_ops_approval import ApprovalLedger, send_message
from p_ops_capture import CaptureStore, SlackCaptureClient, accept_reaction, load_capture_config, resolve_capture
from p_ops_inbox import InboxLedger, create_approved_task
from p_ops_linear import LinearClient, LinearLedger, create_approved_issue
from p_ops_notion import Ledger as NotionLedger, NotionClient, RemoteError, apply_ready, preview
from p_ops_notifications import ReviewNotificationLedger
from p_ops_shadow import ShadowLedger, run_shadow
from p_ops_test_lab import (TestLabLedger, active_task_queue, bundle_from_notion_url,
                            bundle_from_text, evaluate_bundle, validate_corpus)
from p_ops_triage import ClaudeTriageClient
from p_ops_workflow import (ApprovalFlowLedger, SlackApprovalClient, accept_context_reply,
                            process_flows, receive_interaction)


REVIEW_STATES = ("needs_review", "approved", "send_authorized", "review", "conflict")


DASHBOARD_CSS = r"""
:root {
  color-scheme: light;
  --bg: #f4f6fa;
  --surface: #ffffff;
  --surface-soft: #fafaff;
  --border: #e2e4eb;
  --border-strong: #d1d4de;
  --text: #17171c;
  --muted: #686a75;
  --subtle: #8b8d98;
  --purple: #5d29f0;
  --purple-dark: #4817d3;
  --purple-soft: #f0edff;
  --green: #087a55;
  --green-soft: #e9f8f1;
  --amber: #966516;
  --amber-soft: #fff6df;
  --red: #b33142;
  --red-soft: #fff0f2;
  --blue: #2463a9;
  --blue-soft: #edf5ff;
  --shadow: 0 1px 2px rgba(20, 20, 32, .04), 0 8px 24px rgba(33, 25, 57, .045);
  font-family: Diatype, Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 15px;
  line-height: 1.45;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; color: var(--text); background: var(--bg); }
button, input, textarea, select { font: inherit; }
a { color: inherit; }
.shell { width: 100%; min-height: 100vh; overflow-x: hidden; display: grid; grid-template-columns: 196px minmax(0, 1fr); }
.sidebar {
  min-width: 0; position: sticky; top: 0; height: 100vh; padding: 17px 10px 14px;
  background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column;
}
.brand { display: flex; align-items: center; height: 30px; padding: 0 5px; margin-bottom: 25px; }
.brand img { width: 101px; height: auto; display: block; }
.nav-label { margin: 0 10px 8px; color: var(--subtle); font-size: 11px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; }
.nav { display: grid; gap: 4px; }
.nav a { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 8px; border-radius: 6px; text-decoration: none; color: #34343b; font-size: 13px; }
.nav a .nav-copy { display: flex; align-items: center; gap: 9px; }
.nav svg { width: 17px; height: 17px; stroke-width: 2; color: #666873; }
.nav a:hover { background: #f1f1f5; }
.nav a.active { background: #ececf1; font-weight: 650; }
.nav-spacer { height: 19px; }
.nav-count { min-width: 24px; padding: 1px 7px; border-radius: 999px; background: #e2e2e8; color: #52545e; font-size: 11px; text-align: center; }
.side-footer { margin-top: auto; padding: 16px 10px 2px; border-top: 1px solid var(--border); }
.side-footer strong { display: block; font-size: 13px; }
.side-footer span { display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }
.status-dot { display: inline-block; width: 7px; height: 7px; margin-right: 6px; border-radius: 50%; background: #21a273; box-shadow: 0 0 0 3px #e3f6ef; }
.main { min-width: 0; }
.topbar { min-height: 55px; padding: 0 24px; border-bottom: 1px solid var(--border); background: rgba(255,255,255,.96); display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.topbar-title { font-size: 14px; font-weight: 650; }
.topbar-actions, .topbar-meta { display: flex; align-items: center; gap: 9px; }
.command-trigger { width: min(360px, 34vw); height: 36px; min-height: 36px; padding: 0 9px 0 12px; border-color: var(--border); background: #f7f7f9; color: var(--muted); display: flex; align-items: center; gap: 8px; text-align: left; font-weight: 450; }
.command-trigger:hover { border-color: var(--border-strong); background: white; color: #41434c; }
.command-trigger svg { width: 16px; height: 16px; }
.command-trigger .shortcut { margin-left: auto; padding: 2px 6px; border: 1px solid #dbdce2; border-radius: 5px; background: white; color: #777985; font-size: 10px; }
.icon-button { position: relative; width: 36px; height: 36px; min-height: 36px; padding: 0; display: grid; place-items: center; border-color: transparent; background: transparent; color: #33343b; }
.icon-button:hover { border-color: var(--border); background: #f4f4f7; color: #17171c; }
.icon-button svg { width: 19px; height: 19px; }
.notification-badge { position: absolute; top: 2px; right: 1px; min-width: 16px; height: 16px; padding: 0 4px; border: 2px solid white; border-radius: 999px; background: var(--purple); color: white; font-size: 9px; line-height: 12px; }
.notification-wrap { position: relative; }
.notification-panel { position: absolute; z-index: 20; top: 43px; right: 0; width: 330px; padding: 9px; border: 1px solid var(--border); border-radius: 11px; background: white; box-shadow: 0 16px 50px rgba(30,24,48,.16); }
.notification-panel[hidden] { display: none; }
.popover-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 9px 10px; }
.popover-head strong { font-size: 13px; }
.popover-head span { color: var(--subtle); font-size: 11px; }
.notification-item { display: block; padding: 10px; border-radius: 7px; text-decoration: none; }
.notification-item:hover { background: #f5f5f8; }
.notification-item strong { display: block; font-size: 12px; }
.notification-item span { display: block; margin-top: 2px; color: var(--muted); font-size: 11px; }
.environment { padding: 6px 10px; border: 1px solid var(--border); border-radius: 7px; color: var(--muted); background: var(--surface); font-size: 12px; }
.avatar { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%; background: var(--purple); color: white; font-size: 11px; font-weight: 750; }
.content { width: min(1440px, 100%); margin: 0 auto; padding: 28px 30px 64px; }
.hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 27px; }
.eyebrow { margin: 0 0 8px; color: var(--purple); font-size: 11px; font-weight: 750; letter-spacing: .09em; text-transform: uppercase; }
h1 { margin: 0; font-size: clamp(27px, 3vw, 34px); line-height: 1.12; letter-spacing: -.035em; font-weight: 650; }
.lede { max-width: 720px; margin: 12px 0 0; color: var(--muted); font-size: 15px; }
.mode-pill { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 7px; padding: 8px 11px; border: 1px solid #dcd5ff; border-radius: 999px; color: var(--purple-dark); background: var(--purple-soft); font-size: 12px; font-weight: 650; }
.mode-pill::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--purple); }
.safety { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 14px 16px; margin-bottom: 24px; border: 1px solid #e5defe; border-radius: 10px; background: #f8f6ff; }
.safety-copy { min-width: 0; }
.safety-copy strong { display: block; font-size: 13px; }
.safety-copy span { color: var(--muted); font-size: 12px; }
.gates { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }
.gate { padding: 5px 8px; border: 1px solid #ded9ee; border-radius: 6px; background: white; color: #5b5767; font-size: 11px; white-space: nowrap; }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 32px; }
.metric { min-width: 0; padding: 18px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); box-shadow: 0 1px 1px rgba(20,20,32,.02); }
.metric-value { display: block; margin-bottom: 3px; font-size: 28px; line-height: 1; letter-spacing: -.04em; font-weight: 650; }
.metric-label { color: var(--muted); font-size: 12px; }
.queue-section { scroll-margin-top: 22px; margin-top: 34px; }
.section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.section-heading h2 { margin: 0; font-size: 20px; letter-spacing: -.02em; font-weight: 650; }
.section-heading p { margin: 3px 0 0; color: var(--muted); font-size: 12px; }
.section-count { color: var(--subtle); font-size: 12px; }
.queue-list { display: grid; gap: 7px; }
.review-card { overflow: hidden; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); box-shadow: 0 1px 1px rgba(20,20,32,.025); }
.card-main { padding: 13px 15px; }
.card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.card-title { min-width: 0; }
.card-title h3 { margin: 0; font-size: 13px; line-height: 1.35; font-weight: 650; overflow-wrap: anywhere; }
.card-kind { margin: 0 0 5px; color: var(--subtle); font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.state { flex: 0 0 auto; padding: 4px 8px; border-radius: 999px; background: #f0f1f4; color: #5d5f68; font-size: 10px; font-weight: 700; letter-spacing: .02em; text-transform: uppercase; }
.state-needs-review, .state-review, .state-conflict { background: var(--amber-soft); color: var(--amber); }
.state-approved, .state-send-authorized, .state-captured { background: var(--green-soft); color: var(--green); }
.state-shadow, .state-resolving { background: var(--blue-soft); color: var(--blue); }
.state-error, .state-failed { background: var(--red-soft); color: var(--red); }
.card-preview { margin: 8px 0 0; color: var(--muted); font-size: 11px; line-height: 1.45; }
.card-details { margin-top: 9px; border-top: 1px solid transparent; }
.card-details[open] { padding-top: 10px; border-top-color: var(--border); }
.card-details summary { width: fit-content; color: var(--purple); cursor: pointer; font-size: 11px; font-weight: 650; list-style: none; }
.card-details summary::-webkit-details-marker { display: none; }
.card-details summary::after { content: " +"; }
.card-details[open] summary::after { content: " -"; }
.card-body { margin: 11px 0 0; color: #484a54; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 12px; line-height: 1.55; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 17px; padding-top: 15px; border-top: 1px solid var(--border); }
.actions form { display: inline; margin: 0; }
button { min-height: 34px; padding: 7px 12px; border: 1px solid var(--purple); border-radius: 7px; background: var(--purple); color: white; cursor: pointer; font-size: 12px; font-weight: 650; }
button:hover { background: var(--purple-dark); border-color: var(--purple-dark); }
button:disabled { border-color: #d8d9df; background: #d8d9df; color: #777985; cursor: not-allowed; }
button.secondary { border-color: var(--border-strong); background: white; color: #3f4149; }
button.secondary:hover { border-color: #b9bbc5; background: #f6f6f8; }
.empty { display: grid; place-items: center; min-height: 98px; padding: 22px; border: 1px dashed var(--border-strong); border-radius: 10px; background: rgba(255,255,255,.45); color: var(--muted); text-align: center; font-size: 13px; }
.empty strong { display: block; margin-bottom: 3px; color: #3c3e47; font-weight: 600; }
.page-footer { margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--border); color: var(--subtle); font-size: 11px; }
.overview-grid { display: grid; grid-template-columns: 1.45fr 1fr; gap: 14px; margin: 0 0 32px; }
.overview-panel { padding: 19px; border: 1px solid var(--border); border-radius: 10px; background: white; }
.overview-panel h2 { margin: 0 0 4px; font-size: 15px; }
.overview-panel > p { margin: 0; color: var(--muted); font-size: 12px; }
.progress-track { height: 7px; margin: 18px 0 12px; overflow: hidden; border-radius: 999px; background: #ececf1; }
.progress-value { height: 100%; border-radius: inherit; background: var(--purple); }
.health-row { display: flex; justify-content: space-between; gap: 16px; padding: 11px 0; border-bottom: 1px solid var(--border); font-size: 12px; }
.health-row:last-child { padding-bottom: 0; border-bottom: 0; }
.health-row span { color: var(--muted); }
.health-value { color: var(--text) !important; font-weight: 650; }
.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 14px; }
.settings-card { padding: 20px; border: 1px solid var(--border); border-radius: 10px; background: white; }
.settings-card h2 { margin: 0; font-size: 16px; }
.settings-card > p { margin: 5px 0 18px; color: var(--muted); font-size: 12px; }
.setting-row { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 13px 0; border-top: 1px solid var(--border); }
.setting-copy strong { display: block; font-size: 13px; }
.setting-copy span { display: block; margin-top: 2px; color: var(--muted); font-size: 11px; }
.switch { flex: 0 0 auto; width: 36px; height: 20px; padding: 2px; border-radius: 999px; background: #d8d9df; }
.switch::after { content: ""; display: block; width: 16px; height: 16px; border-radius: 50%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,.18); }
.switch.on { background: var(--purple); }
.switch.on::after { margin-left: 16px; }
.activity-list { border: 1px solid var(--border); border-radius: 10px; background: white; overflow: hidden; }
.activity-row { display: grid; grid-template-columns: minmax(140px,1.5fr) minmax(90px,.5fr) minmax(140px,1fr); gap: 18px; align-items: center; padding: 15px 18px; border-bottom: 1px solid var(--border); }
.activity-row:last-child { border-bottom: 0; }
.activity-title strong { display: block; font-size: 13px; }
.activity-title span, .activity-source { color: var(--muted); font-size: 11px; }
.lab-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 14px; margin-bottom: 28px; }
.lab-panel { padding: 20px; border: 1px solid var(--border); border-radius: 10px; background: white; }
.lab-panel.full { grid-column: 1 / -1; }
.lab-panel h2 { margin: 0; font-size: 16px; }
.lab-panel > p { margin: 5px 0 17px; color: var(--muted); font-size: 12px; }
.lab-form { display: grid; gap: 12px; }
.field { display: grid; gap: 6px; }
.field label { color: #3e4048; font-size: 12px; font-weight: 650; }
.field input, .field textarea, .field select { width: 100%; padding: 10px 11px; border: 1px solid var(--border-strong); border-radius: 7px; outline: 0; background: white; color: var(--text); font-size: 13px; }
.field textarea { min-height: 124px; resize: vertical; line-height: 1.5; }
.field input:focus, .field textarea:focus, .field select:focus { border-color: var(--purple); box-shadow: 0 0 0 3px rgba(93,41,240,.10); }
.form-note { color: var(--subtle); font-size: 11px; }
.lab-results { display: grid; gap: 14px; }
.decision-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 9px; margin: 16px 0; }
.decision-fact { padding: 10px; border-radius: 7px; background: #f7f7fa; }
.decision-fact span { display: block; color: var(--subtle); font-size: 10px; text-transform: uppercase; letter-spacing: .05em; }
.decision-fact strong { display: block; margin-top: 3px; font-size: 12px; overflow-wrap: anywhere; }
.feedback-form { display: grid; grid-template-columns: minmax(150px,.55fr) minmax(220px,1fr) auto; gap: 9px; align-items: end; margin-top: 17px; padding-top: 15px; border-top: 1px solid var(--border); }
.feedback-form .field { gap: 4px; }
.feedback-form input, .feedback-form select { padding: 8px 9px; }
.feedback-saved { display: inline-block; margin-top: 14px; padding: 5px 8px; border-radius: 6px; background: var(--green-soft); color: var(--green); font-size: 11px; font-weight: 650; }
.baseline { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.baseline-metrics { display: flex; flex-wrap: wrap; gap: 8px; }
.baseline-metrics span { padding: 6px 9px; border-radius: 6px; background: #f3f2f8; color: #50525c; font-size: 11px; }
.task-queue { margin-bottom: 28px; }
.task-queue-tools { display: grid; grid-template-columns: minmax(220px,1fr) minmax(150px,.35fr); gap: 10px; margin: 17px 0 14px; }
.task-search, .task-filter { width: 100%; min-height: 38px; padding: 9px 11px; border: 1px solid var(--border-strong); border-radius: 7px; outline: 0; background: white; color: var(--text); font-size: 12px; }
.task-search:focus, .task-filter:focus { border-color: var(--purple); box-shadow: 0 0 0 3px rgba(93,41,240,.10); }
.task-queue-summary { color: var(--subtle); font-size: 11px; white-space: nowrap; }
.task-queue-list { display: grid; gap: 8px; max-height: 590px; overflow-y: auto; padding-right: 3px; }
.task-queue-item { display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 18px; align-items: center; padding: 14px 15px; border: 1px solid var(--border); border-radius: 8px; background: #fcfcfe; }
.task-queue-item:hover { border-color: #cec8e8; background: #faf9ff; }
.task-queue-item[hidden] { display: none; }
.task-queue-item.reviewed { background: #fafafa; }
.task-queue-main { min-width: 0; }
.task-title-row { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
.task-title-row a { color: var(--text); font-size: 13px; font-weight: 650; text-decoration: none; overflow-wrap: anywhere; }
.task-title-row a:hover { color: var(--purple); text-decoration: underline; }
.task-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.task-meta span { padding: 3px 6px; border-radius: 5px; background: #f0f0f4; color: #5d5f68; font-size: 10px; }
.task-meta .overdue { background: var(--red-soft); color: var(--red); font-weight: 650; }
.task-meta .reviewed-badge { background: var(--green-soft); color: var(--green); }
.task-next { margin: 7px 0 0; color: var(--muted); font-size: 11px; line-height: 1.4; overflow-wrap: anywhere; }
.task-detail { margin-top: 8px; }
.task-detail summary { color: var(--purple); cursor: pointer; font-size: 10px; font-weight: 650; list-style: none; }
.task-detail summary::-webkit-details-marker { display: none; }
.task-detail summary::after { content: " +"; }
.task-detail[open] summary::after { content: " -"; }
.task-queue-actions { min-width: 96px; text-align: right; }
.task-queue-actions form { margin: 0; }
.task-queue-actions button { width: 100%; }
.task-queue-empty { display: none; margin-top: 8px; }
.task-queue-empty.visible { display: grid; }
.run-stage-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-bottom: 12px; }
.stage-filter { min-height: 29px; padding: 5px 9px; border: 1px solid var(--border); border-radius: 999px; background: white; color: var(--muted); font-size: 10px; }
.stage-filter.active, .stage-filter:hover { border-color: #cfc5ff; background: var(--purple-soft); color: var(--purple-dark); }
.test-run-card[hidden] { display: none; }
.runpod-spinner { width: 30px; height: 30px; object-fit: contain; border-radius: 8px; animation: runpod-spin 1.25s cubic-bezier(.5,.1,.5,.9) infinite; }
.evaluating-card { border-color: #dcd3ff; background: linear-gradient(90deg,#fff 0%,#faf8ff 100%); }
.evaluating-main { display: flex; align-items: center; gap: 12px; }
.evaluating-copy strong { display: block; font-size: 12px; }
.evaluating-copy span { display: block; margin-top: 2px; color: var(--muted); font-size: 10px; }
@keyframes runpod-spin { to { transform: rotate(360deg); } }
.modal-open { overflow: hidden; }
.command-overlay { position: fixed; z-index: 50; inset: 0; padding: 10vh 18px 18px; background: rgba(24,22,31,.44); backdrop-filter: blur(2px); }
.command-overlay[hidden] { display: none; }
.command-dialog { width: min(620px, 100%); max-height: 72vh; margin: 0 auto; overflow: hidden; border: 1px solid var(--border); border-radius: 13px; background: white; box-shadow: 0 28px 80px rgba(20,16,38,.25); }
.command-input-wrap { display: flex; align-items: center; gap: 11px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.command-input-wrap svg { width: 19px; height: 19px; color: var(--muted); }
.command-input { width: 100%; border: 0; outline: 0; background: transparent; color: var(--text); font-size: 15px; }
.command-close { min-height: 26px; padding: 3px 7px; border-color: var(--border); background: white; color: var(--muted); font-size: 10px; }
.command-results { max-height: calc(72vh - 58px); overflow-y: auto; padding: 8px; }
.command-group-label { padding: 8px 9px 5px; color: var(--subtle); font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.command-item { display: flex; align-items: center; justify-content: space-between; gap: 15px; padding: 10px 11px; border-radius: 7px; text-decoration: none; }
.command-item[hidden], .command-empty[hidden] { display: none; }
.command-item:hover { background: var(--purple-soft); }
.command-item strong { display: block; font-size: 13px; }
.command-item span { color: var(--muted); font-size: 11px; }
.command-item kbd { color: var(--subtle); font: inherit; font-size: 10px; }
.command-empty { padding: 32px; color: var(--muted); text-align: center; font-size: 13px; }
@media (max-width: 940px) {
  .shell { grid-template-columns: minmax(0, 1fr); }
  .sidebar { position: static; width: 100%; height: auto; padding: 16px 20px; display: block; }
  .brand { margin-bottom: 14px; }
  .nav-label, .side-footer { display: none; }
  .nav { display: flex; overflow-x: auto; padding-bottom: 2px; }
  .nav a { flex: 0 0 auto; }
  .nav-spacer { display: none; }
  .topbar { padding: 0 22px; }
  .content { padding: 30px 22px 56px; }
  .metrics, .settings-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .overview-grid { grid-template-columns: 1fr; }
  .decision-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
}
@media (max-width: 600px) {
  .topbar-meta .environment { display: none; }
  .topbar { gap: 10px; }
  .command-trigger { width: 38px; justify-content: center; padding: 0; }
  .command-trigger span:not(.shortcut), .command-trigger .shortcut { display: none; }
  .hero, .safety { align-items: flex-start; flex-direction: column; }
  .gates { justify-content: flex-start; }
  .metrics { grid-template-columns: 1fr 1fr; }
  .settings-grid { grid-template-columns: 1fr; }
  .lab-grid { grid-template-columns: 1fr; }
  .lab-panel.full { grid-column: auto; }
  .feedback-form { grid-template-columns: 1fr; }
  .task-queue-tools, .task-queue-item { grid-template-columns: 1fr; }
  .task-queue-actions { text-align: left; }
  .task-queue-actions button { width: auto; }
  .card-head { flex-direction: column; gap: 9px; }
  .notification-panel { position: fixed; top: 62px; right: 10px; left: 10px; width: auto; }
  .activity-row { grid-template-columns: 1fr; gap: 6px; }
}
"""


def config(env=None):
    env = env or os.environ
    runtime_enabled = env.get("P_OPS_ENABLED") == "true"
    cfg = load_capture_config(env) if runtime_enabled else {
        "enabled": False, "secret": "", "team_id": "", "app_id": "",
        "channel_ids": set(), "user_ids": set(),
    }
    cfg.update({
        "runtime_enabled": runtime_enabled,
        "db": env.get("P_OPS_DB", "/data/p_ops.sqlite3"),
        "review_user": env.get("P_OPS_REVIEW_USER", ""),
        "review_password": env.get("P_OPS_REVIEW_PASSWORD", ""),
        "csrf_secret": env.get("P_OPS_CSRF_SECRET", ""),
        "daily_review": env.get("P_OPS_DAILY_REVIEW_ENABLED") == "true",
        "triage_shadow": env.get("P_OPS_TRIAGE_SHADOW_ENABLED") == "true",
        "review_posting": env.get("P_OPS_REVIEW_POSTING_ENABLED") == "true",
        "review_channel": env.get("P_OPS_REVIEW_CHANNEL_ID", ""),
        "review_interactive": env.get("P_OPS_REVIEW_INTERACTIVE_ENABLED") == "true",
        "review_user_ids": {value.strip() for value in env.get(
            "P_OPS_REVIEW_USER_IDS", env.get("P_OPS_CAPTURE_USER_IDS", "")).split(",") if value.strip()},
        "public_url": env.get("P_OPS_PUBLIC_URL", "").rstrip("/"),
    })
    if cfg["runtime_enabled"] and not all((cfg["review_user"], cfg["review_password"], cfg["csrf_secret"])):
        raise ValueError("Set P_OPS_REVIEW_USER, P_OPS_REVIEW_PASSWORD, and P_OPS_CSRF_SECRET.")
    if cfg["review_posting"] and not cfg["review_channel"]:
        raise ValueError("Set P_OPS_REVIEW_CHANNEL_ID before enabling review posting.")
    if cfg["review_posting"] and not env.get("SLACK_BOT_TOKEN"):
        raise ValueError("Set SLACK_BOT_TOKEN before enabling review posting.")
    if cfg["review_interactive"] and not cfg["review_posting"]:
        raise ValueError("Enable P_OPS_REVIEW_POSTING_ENABLED before interactive review.")
    if cfg["review_interactive"] and not cfg["triage_shadow"]:
        raise ValueError("Enable P_OPS_TRIAGE_SHADOW_ENABLED before interactive review.")
    if cfg["review_interactive"] and not cfg["review_user_ids"]:
        raise ValueError("Set P_OPS_REVIEW_USER_IDS before enabling interactive review.")
    return cfg


class RuntimeState:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS p_ops_runtime_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def get(self, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value FROM p_ops_runtime_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO p_ops_runtime_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, value))


class SlackSender:
    def __init__(self, token=None):
        self.token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.token:
            raise ValueError("Set SLACK_BOT_TOKEN before sending Slack messages.")

    def __call__(self, row):
        if row["kind"] != "slack":
            raise ValueError("This sender only supports Slack drafts.")
        result = api("https://slack.com/api/chat.postMessage", self.token, {
            "channel": row["destination"], "text": row["content"], "mrkdwn": False,
            "parse": "none", "unfurl_links": False, "unfurl_media": False})
        if not result.get("ts"):
            raise ValueError("Slack did not confirm message delivery.")
        return result["ts"]


class SlackReviewSender:
    def __init__(self, expected_channel, token=None):
        self.expected_channel = expected_channel
        self.token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.expected_channel or not self.token:
            raise ValueError("Review channel and Slack bot token are required.")

    def __call__(self, row):
        if row["channel"] != self.expected_channel:
            raise ValueError("Review notification destination is not authorized.")
        result = api("https://slack.com/api/chat.postMessage", self.token, {
            "channel": self.expected_channel, "text": row["content"], "mrkdwn": False,
            "parse": "none", "unfurl_links": False, "unfurl_media": False})
        if not result.get("ts"):
            raise ValueError("Slack did not confirm review notification delivery.")
        return result["ts"]


def _basic_authorized(env, cfg):
    value = env.get("HTTP_AUTHORIZATION", "")
    if not value.startswith("Basic "):
        return False
    try:
        user, password = base64.b64decode(value[6:], validate=True).decode().split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(user, cfg["review_user"]) and hmac.compare_digest(password, cfg["review_password"])


def _csrf(cfg, kind, action, item_id):
    value = ":".join((kind, action, item_id)).encode()
    return hmac.new(cfg["csrf_secret"].encode(), value, hashlib.sha256).hexdigest()


def _button(cfg, kind, action, item_id, label):
    token = _csrf(cfg, kind, action, item_id)
    css_class = "secondary" if action == "reject" else "primary"
    return ("<form method='post' action='/review/action'><input type='hidden' name='kind' value='%s'>"
            "<input type='hidden' name='action' value='%s'><input type='hidden' name='id' value='%s'>"
            "<input type='hidden' name='csrf' value='%s'><button class='%s'>%s</button></form>" %
            tuple(html.escape(x, quote=True) for x in (kind, action, item_id, token, css_class, label)))


def _card(title, state, body, buttons="", kind="Work item"):
    state_class = "".join(character if character.isalnum() else "-" for character in state.lower()).strip("-")
    actions = "<div class='actions'>%s</div>" % buttons if buttons else ""
    preview = " ".join(str(body or "").split())
    if len(preview) > 180:
        preview = preview[:177].rsplit(" ", 1)[0] + "..."
    return ("<article class='review-card'><div class='card-main'><div class='card-head'>"
            "<div class='card-title'><p class='card-kind'>%s</p><h3>%s</h3></div>"
            "<span class='state state-%s'>%s</span></div><p class='card-preview'>%s</p>"
            "<details class='card-details'><summary>View details</summary><div class='card-body'>%s</div>%s"
            "</details></div></article>" % (
                html.escape(kind), html.escape(title), html.escape(state_class, quote=True),
                html.escape(state.replace("_", " ")), html.escape(preview), html.escape(body), actions))


def _empty(title, detail):
    return "<div class='empty'><div><strong>%s</strong>%s</div></div>" % (
        html.escape(title), html.escape(detail))


def _section(section_id, title, detail, cards):
    count = len(cards)
    content = "".join(cards) if cards else _empty("Queue is clear", "Nothing needs attention here.")
    return ("<section class='queue-section' id='%s'><div class='section-heading'><div><h2>%s</h2>"
            "<p>%s</p></div><span class='section-count'>%d %s</span></div><div class='queue-list'>%s</div>"
            "</section>" % (html.escape(section_id, quote=True), html.escape(title), html.escape(detail),
                             count, "item" if count == 1 else "items", content))


def _icon(name):
    paths = {
        "dashboard": "<rect x='3' y='3' width='7' height='7' rx='1'/><rect x='14' y='3' width='7' height='7' rx='1'/><rect x='3' y='14' width='7' height='7' rx='1'/><rect x='14' y='14' width='7' height='7' rx='1'/>",
        "activity": "<path d='M3 12h4l3-8 4 16 3-8h4'/>",
        "capture": "<path d='M4 5h16v14H4z'/><path d='m4 7 8 6 8-6'/>",
        "tasks": "<path d='M9 11l3 3L22 4'/><path d='M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11'/>",
        "message": "<path d='M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z'/>",
        "linear": "<path d='m16 3 5 5-13 13H3v-5z'/><path d='m15 4 5 5'/>",
        "triage": "<path d='M12 2v4'/><path d='M12 18v4'/><path d='m4.93 4.93 2.83 2.83'/><path d='m16.24 16.24 2.83 2.83'/><path d='M2 12h4'/><path d='M18 12h4'/><circle cx='12' cy='12' r='4'/>",
        "test": "<path d='M9 3h6'/><path d='M10 9V3h4v6l5 9a2 2 0 0 1-1.75 3H6.75A2 2 0 0 1 5 18z'/><path d='M7.5 15h9'/>",
        "settings": "<circle cx='12' cy='12' r='3'/><path d='M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.55V21h-4v-.08a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3v-4h.08a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3h4v.08a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.12.61.55 1.1 1.15 1.32.15.05.3.08.45.08v4h-.08A1.7 1.7 0 0 0 19.4 15z'/>",
        "search": "<circle cx='11' cy='11' r='8'/><path d='m21 21-4.35-4.35'/>",
        "bell": "<path d='M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9'/><path d='M13.7 21a2 2 0 0 1-3.4 0'/>",
    }
    return "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>%s</svg>" % paths[name]


class Dashboard:
    def __init__(self, cfg, capture, inbox, messages, linear, notion, triage, test_lab=None):
        self.cfg, self.capture, self.inbox = cfg, capture, inbox
        self.messages, self.linear, self.notion, self.triage = messages, linear, notion, triage
        self.test_lab = test_lab or TestLabLedger(cfg["db"])
        self.approval_flows = ApprovalFlowLedger(cfg["db"])

    def test_lab_content(self, task_pages=None, queue_error=""):
        corpus = validate_corpus(Path(__file__).with_name("evals") / "p_ops_triage_cases.jsonl")
        model_ready = bool(os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("ANTHROPIC_MODEL"))
        notion_ready = bool(os.environ.get("NOTION_TOKEN"))
        new_tokens = {action: _csrf(self.cfg, "test_lab", action, "new")
                      for action in ("validate", "evaluate_notion", "evaluate_text")}
        queue_items = active_task_queue(task_pages, self.test_lab.evaluated_source_ids())
        status_options = sorted({item["status"] for item in queue_items})
        queue_rows = []
        for item in queue_items[:100]:
            meta = [item["status"]]
            if item["work_mode"]:
                meta.append(item["work_mode"])
            if item["owner"]:
                meta.append(item["owner"])
            if item["next_step_date"]:
                meta.append("Next: " + item["next_step_date"])
            if item["due_date"]:
                meta.append("Due: " + item["due_date"])
            meta_html = "".join(
                "<span class='%s'>%s</span>" %
                ("overdue" if item["overdue"] and (value.startswith("Next:") or value.startswith("Due:")) else "",
                 html.escape(value)) for value in meta)
            if item["evaluated"]:
                meta_html += "<span class='reviewed-badge'>Reviewed</span>"
            query = " ".join((item["title"], item["status"], item["owner"], item["work_mode"], item["next_step"]))
            next_preview = " ".join(item["next_step"].split())
            if len(next_preview) > 145:
                next_preview = next_preview[:142].rsplit(" ", 1)[0] + "..."
            detail_html = ""
            if item["next_step"]:
                detail_html = ("<p class='task-next'><strong>Next:</strong> %s</p>"
                               "<details class='task-detail'><summary>View full task context</summary>"
                               "<p class='task-next'>%s</p></details>" %
                               (html.escape(next_preview), html.escape(item["next_step"])))
            queue_rows.append(
                "<article class='task-queue-item %s' data-task-queue-item data-status='%s' data-query='%s'>"
                "<div class='task-queue-main'><div class='task-title-row'><a href='%s' target='_blank' rel='noopener'>%s</a>"
                "</div><div class='task-meta'>%s</div>%s</div><div class='task-queue-actions'>"
                "<form class='lab-form task-evaluation-form' data-evaluation-title='%s' method='post' action='/test-lab/action'>"
                "<input type='hidden' name='action' value='evaluate_notion'>"
                "<input type='hidden' name='csrf' value='%s'><input type='hidden' name='task_url' value='%s'>"
                "<button %s>%s</button></form></div></article>" %
                ("reviewed" if item["evaluated"] else "", html.escape(item["status"], quote=True),
                 html.escape(query.lower(), quote=True), html.escape(item["url"], quote=True),
                 html.escape(item["title"]), meta_html, detail_html,
                 html.escape(item["title"], quote=True), html.escape(new_tokens["evaluate_notion"], quote=True), html.escape(item["url"], quote=True),
                 "" if notion_ready and model_ready else "disabled",
                 "Re-evaluate" if item["evaluated"] else "Evaluate"))
        status_filter = "".join("<option value='%s'>%s</option>" %
                                (html.escape(status, quote=True), html.escape(status))
                                for status in status_options)
        reviewed_count = sum(1 for item in queue_items if item["evaluated"])
        if queue_error:
            queue_body = _empty("Task queue unavailable", queue_error)
        elif queue_rows:
            queue_body = ("<div class='task-queue-tools'><input class='task-search' data-task-queue-search "
                          "placeholder='Search tasks, owners, next steps...' aria-label='Search active tasks'>"
                          "<select class='task-filter' data-task-status-filter aria-label='Filter by status'>"
                          "<option value=''>All statuses</option>%s</select></div>"
                          "<div class='task-queue-list'>%s</div>"
                          "<div class='empty task-queue-empty' data-task-queue-empty><div><strong>No matching tasks</strong>"
                          "Try a different search or status.</div></div>" %
                          (status_filter, "".join(queue_rows)))
        else:
            queue_body = _empty("No active tasks", "There are no active Working Tasks available for review.")
        queue = ("<section class='lab-panel full task-queue'><div class='section-heading'><div>"
                 "<h2>Active task review queue</h2><p>Review active Working Tasks in priority order-no link pasting required.</p>"
                 "</div><span class='task-queue-summary' data-task-queue-count>%d active · %d reviewed</span></div>%s</section>" %
                 (len(queue_items), reviewed_count, queue_body))
        baseline = ("<section class='lab-panel full'><div class='baseline'><div><h2>Evaluation baseline</h2>"
                    "<p>The deterministic corpus can be validated without calling Claude.</p></div>"
                    "<div class='baseline-metrics'><span>%d cases</span><span>%d historical</span>"
                    "<span>%d policy edges</span><span>Corpus valid</span></div>"
                    "<form method='post' action='/test-lab/action'><input type='hidden' name='action' value='validate'>"
                    "<input type='hidden' name='csrf' value='%s'><button class='secondary'>Validate again</button></form>"
                    "</div></section>" % (corpus["cases"], corpus["historical"], corpus["policy_edge"],
                                           html.escape(new_tokens["validate"], quote=True)))
        notion_form = ("<section class='lab-panel'><h2>Evaluate by URL</h2>"
                       "<p>Use this fallback for a task that does not appear in the active queue.</p>"
                       "<form class='lab-form task-evaluation-form' data-evaluation-title='Working Task evaluation' method='post' action='/test-lab/action'>"
                       "<input type='hidden' name='action' value='evaluate_notion'>"
                       "<input type='hidden' name='csrf' value='%s'><div class='field'><label for='task_url'>Notion task URL</label>"
                       "<input id='task_url' name='task_url' type='url' required placeholder='https://app.notion.com/p/runpod/...'></div>"
                       "<span class='form-note'>Notion token: %s · Claude: %s</span>"
                       "<button %s>Evaluate task</button></form></section>" %
                       (html.escape(new_tokens["evaluate_notion"], quote=True),
                        "configured" if notion_ready else "not configured", "ready" if model_ready else "not configured",
                        "" if notion_ready and model_ready else "disabled"))
        text_form = ("<section class='lab-panel'><h2>Evaluate pasted context</h2>"
                     "<p>Use this for Slack, email, meeting, or hypothetical work that is not yet a Working Task.</p>"
                     "<form class='lab-form task-evaluation-form' data-evaluation-title='Pasted context evaluation' method='post' action='/test-lab/action'>"
                     "<input type='hidden' name='action' value='evaluate_text'>"
                     "<input type='hidden' name='csrf' value='%s'><div class='field'><label for='test_title'>Task or signal</label>"
                     "<input id='test_title' name='title' maxlength='240' required placeholder='What needs to be evaluated?'></div>"
                     "<div class='field'><label for='test_context'>Known context and evidence</label>"
                     "<textarea id='test_context' name='context' maxlength='20000' required placeholder='Include the current state, objective, owner, dependencies, dates, and any known next step.'></textarea></div>"
                     "<span class='form-note'>Claude: %s · This does not search your workspace.</span>"
                     "<button %s>Run read-only evaluation</button></form></section>" %
                     (html.escape(new_tokens["evaluate_text"], quote=True),
                      "ready" if model_ready else "not configured", "" if model_ready else "disabled"))

        result_cards = []
        stage_counts = {"baseline": 0, "evaluating": 0, "ready-review": 0, "approved": 0,
                        "needs-correction": 0, "error": 0}
        feedback_labels = (("approved", "Approve"), ("too_aggressive", "Too aggressive"),
                           ("too_cautious", "Too cautious"), ("missing_context", "Missing context"),
                           ("wrong_disposition", "Wrong disposition"))
        for row in self.test_lab.rows():
            result = json.loads(row["result_json"]) if row.get("result_json") else None
            try:
                run_input = json.loads(row.get("input_json") or "{}")
            except (TypeError, ValueError):
                run_input = {}
            source_context = run_input.get("source", {}).get("text") or run_input.get("submitted") or ""
            if row["kind"] == "corpus":
                stage_counts["baseline"] += 1
                body = ("Corpus validation passed.\n\nCases: %d · Historical: %d · Policy edges: %d" %
                        (result["cases"], result["historical"], result["policy_edge"])) if result else (row.get("error") or "Validation failed.")
                baseline_card = _card(row["label"], row["state"], body, kind="Baseline validation")
                result_cards.append(baseline_card.replace(
                    "class='review-card'", "class='review-card test-run-card' data-run-stage='baseline'", 1))
                continue
            if row["state"] == "evaluating":
                stage_counts["evaluating"] += 1
                result_cards.append(
                    "<article class='review-card test-run-card evaluating-card' id='%s' data-run-stage='evaluating'>"
                    "<div class='card-main evaluating-main'><img class='runpod-spinner' src='/assets/runpod-icon.png' alt=''>"
                    "<div class='evaluating-copy'><strong>%s</strong><span>Reading context and evaluating the next best action...</span>"
                    "</div><span class='state state-shadow'>evaluating</span></div></article>" %
                    (html.escape(row["id"], quote=True), html.escape(row["label"])))
                continue
            if result:
                decision = result["decision"]
                if row.get("feedback") == "approved":
                    stage = "approved"
                elif row.get("feedback"):
                    stage = "needs-correction"
                else:
                    stage = "ready-review"
                stage_counts[stage] += 1
                facts = (("Disposition", decision["disposition"]), ("Work mode", decision["work_mode"]),
                         ("Priority", decision["priority"]), ("Confidence", "%d%%" % round(decision["confidence"] * 100)))
                fact_html = "".join("<div class='decision-fact'><span>%s</span><strong>%s</strong></div>" %
                                    (html.escape(label), html.escape(str(value))) for label, value in facts)
                detail = ("<div class='card-body'><strong>Next step</strong>\n%s\n\n<strong>Definition of done</strong>\n%s"
                          "\n\n<strong>Why</strong>\n%s\n\n<strong>Review</strong>\n%s\n\n<strong>Missing evidence</strong>\n%s"
                          "\n\n<strong>Evaluated context</strong>\n%s</div>" %
                        (html.escape(decision["next_step"]),
                         html.escape(decision["definition_of_done"]), html.escape(decision["rationale"]),
                         "Required - " + html.escape(", ".join(result["policy_reasons"])) if result["review_required"] else "Not required",
                         html.escape(", ".join(decision["missing_evidence"]) or "None"),
                         html.escape(source_context or "Context not available.")))
                if row.get("feedback"):
                    detail += "<span class='feedback-saved'>Feedback saved: %s</span>" % html.escape(row["feedback"].replace("_", " "))
                options = "".join("<option value='%s'>%s</option>" % (value, label) for value, label in feedback_labels)
                feedback_token = _csrf(self.cfg, "test_lab", "feedback", row["id"])
                detail += ("<form class='feedback-form' method='post' action='/test-lab/action'>"
                         "<input type='hidden' name='action' value='feedback'><input type='hidden' name='id' value='%s'>"
                         "<input type='hidden' name='csrf' value='%s'><div class='field'><label>Your assessment</label>"
                         "<select name='feedback'>%s</select></div><div class='field'><label>One correction, if needed</label>"
                         "<input name='note' maxlength='2000' placeholder='What should it understand or do differently?'></div>"
                         "<button>Save feedback</button></form>" %
                         (html.escape(row["id"], quote=True), html.escape(feedback_token, quote=True), options))
                result_cards.append("<article class='review-card test-run-card' id='%s' data-run-stage='%s'><div class='card-main'><div class='card-head'>"
                                    "<div class='card-title'><p class='card-kind'>Read-only test</p><h3>%s</h3></div>"
                                    "<span class='state state-%s'>%s</span></div><p class='card-preview'>%s</p>"
                                    "<div class='decision-grid'>%s</div><details class='card-details'><summary>Open evaluation</summary>%s"
                                    "</details></div></article>" %
                                    (html.escape(row["id"], quote=True), html.escape(stage, quote=True),
                                     html.escape(row["label"]), "approved" if stage == "approved" else "shadow",
                                     html.escape(stage.replace("-", " ")), html.escape(decision["summary"]),
                                     fact_html, detail))
            else:
                stage_counts["error"] += 1
                error_card = _card(row["label"], "error", (row.get("error") or "Evaluation failed.") +
                                   ("\n\nContext\n" + source_context if source_context else ""), kind="Read-only test")
                result_cards.append(error_card.replace("class='review-card'", "class='review-card test-run-card' data-run-stage='error'", 1))
        stage_buttons = "".join(
            "<button class='stage-filter %s' type='button' data-run-filter='%s'>%s <span>%d</span></button>" %
            ("active" if key == "all" else "", html.escape(key, quote=True), html.escape(label), count)
            for key, label, count in (
                ("all", "All", len(result_cards)), ("evaluating", "Evaluating", stage_counts["evaluating"]),
                ("ready-review", "Ready for review", stage_counts["ready-review"]),
                ("approved", "Approved", stage_counts["approved"]),
                ("needs-correction", "Needs correction", stage_counts["needs-correction"]),
                ("error", "Errors", stage_counts["error"]), ("baseline", "Baseline", stage_counts["baseline"]),
            ))
        history = ("<section class='queue-section'><div class='section-heading'><div><h2>Recent test runs</h2>"
                   "<p>Persistent history with the original context, decision, and your feedback.</p></div><span class='section-count'>%d saved runs</span>"
                   "</div><div class='run-stage-bar'>%s</div><div class='lab-results' data-run-list>%s</div>"
                   "<div class='empty task-queue-empty' data-run-empty><div><strong>No runs in this stage</strong>Choose another stage.</div></div></section>" %
                   (len(result_cards), stage_buttons, "".join(result_cards) if result_cards else _empty(
                       "No test runs yet", "Evaluate a Working Task or pasted context to begin calibration.")))
        return ("<div class='hero'><div><p class='eyebrow'>Calibration</p><h1>Test Lab</h1>"
                "<p class='lede'>See exactly how p-OPS understands work, why it chooses a disposition, and what it would do next.</p>"
                "</div><span class='mode-pill'>No external writes</span></div><div class='safety'>"
                "<div class='safety-copy'><strong>Safe shadow testing</strong><span>Tests may read Notion and call Claude. They cannot update tasks, post to Slack, create Linear issues, or send messages.</span>"
                "</div><div class='gates'><span class='gate'>Notion writes: off</span><span class='gate'>Messages: off</span>"
                "<span class='gate'>Execution: off</span></div></div><div class='lab-grid'>%s%s%s%s</div>%s" %
                (queue, baseline, notion_form, text_form, history))

    def render(self, page="dashboard", task_pages=None, queue_error=""):
        captures = self.capture.rows(("pending", "resolving", "captured", "review"))
        inbox_rows = self.inbox.rows(REVIEW_STATES)
        message_rows = self.messages.rows(REVIEW_STATES)
        linear_rows = self.linear.rows(REVIEW_STATES)
        notion_rows = self.notion.rows(REVIEW_STATES)
        triage_rows = self.triage.rows(("shadow", "error"))
        flow_rows = self.approval_flows.rows()

        sections = []
        activity = []
        cards = [_card("Slack capture", row["state"], row.get("source_text") or row.get("error") or "Waiting to resolve",
                       kind="Slack capture")
                 for row in captures]
        activity.extend(("Slack capture", row["state"], row.get("source_text") or "Captured Slack work") for row in captures)
        sections.append(_section("captures", "Captures", "Slack reactions waiting to be resolved or classified.", cards))

        cards = []
        for row in inbox_rows:
            data = json.loads(row["proposal_json"])
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "inbox", "approve", row["id"], "Approve task") + _button(
                    self.cfg, "inbox", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                buttons = _button(self.cfg, "inbox", "reject", row["id"], "Cancel approval")
            cards.append(_card(data["title"], row["state"], data["context"] + "\n\nNext: " + data["next_step"],
                               buttons, "Task proposal"))
            activity.append((data["title"], row["state"], "Task proposal"))
        sections.append(_section("task-proposals", "Task proposals",
                                 "New tasks proposed from captured work and supporting evidence.", cards))

        cards = []
        for row in message_rows:
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "message", "approve", row["id"], "Approve wording") + _button(
                    self.cfg, "message", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                if row["kind"] == "slack":
                    buttons = _button(self.cfg, "message", "authorize", row["id"], "Authorize send")
                buttons += _button(self.cfg, "message", "reject", row["id"], "Reject")
            cards.append(_card("%s to %s" % (row["kind"].title(), row["destination"]), row["state"],
                               ((row["subject"] + "\n\n") if row["subject"] else "") + row["content"], buttons,
                               "Message draft"))
            activity.append(("%s to %s" % (row["kind"].title(), row["destination"]), row["state"], "Message draft"))
        sections.append(_section("messages", "Messages", "Drafts that require review before anything is sent.", cards))

        cards = []
        for row in linear_rows:
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "linear", "approve", row["id"], "Approve issue") + _button(
                    self.cfg, "linear", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                buttons = _button(self.cfg, "linear", "reject", row["id"], "Cancel approval")
            cards.append(_card(row["title"], row["state"], row["description"], buttons, "Linear issue"))
            activity.append((row["title"], row["state"], "Linear issue"))
        sections.append(_section("linear", "Linear", "Engineering work that may need a linked Linear issue.", cards))

        cards = []
        for row in notion_rows:
            plan = json.loads(row["plan_json"])
            buttons = (_button(self.cfg, "notion", "approve", row["id"], "Approve update") +
                       _button(self.cfg, "notion", "reject", row["id"], "Reject")) if row["state"] == "needs_review" else ""
            cards.append(_card(row["title"], row["state"], plan.get("reason", ""), buttons, "Notion task update"))
            activity.append((row["title"], row["state"], "Notion task update"))
        sections.append(_section("task-updates", "Task updates",
                                 "Proposed changes to active Working Tasks in Notion.", cards))

        cards = []
        for row in triage_rows:
            if row["decision_json"]:
                guarded = json.loads(row["decision_json"])
                decision = guarded["decision"]
                body = (decision["summary"] + "\n\nDisposition: " + decision["disposition"] +
                        "\nReview required: " + str(guarded["review_required"]) +
                        "\nPolicy: " + ", ".join(guarded["policy_reasons"]) +
                        "\nViolations: " + (", ".join(guarded["violations"]) or "none"))
                title = decision["title"]
            else:
                bundle = json.loads(row["bundle_json"])
                title = bundle["source"].get("text", "Shadow triage")[:120]
                body = row["error"] or "Triage failed."
            cards.append(_card(title, row["state"], body, kind="Read-only evaluation"))
            activity.append((title, row["state"], "Read-only evaluation"))
        sections.append(_section("shadow-triage", "Shadow triage",
                                 "Read-only decisions used to calibrate the agent before automation.", cards))

        cards = []
        for row in reversed(flow_rows[-50:]):
            steps = self.approval_flows.steps(row["id"])
            completed = sum(1 for step in steps if step["state"] in {"approved", "revised"})
            current = next((step for step in steps if step["state"] in {"pending", "awaiting_context"}), None)
            body = "%d of %d decisions recorded." % (completed, len(steps))
            if current:
                body += "\n\nCurrent question: " + current["prompt"] + "\n" + current["context"]
            links = json.loads(row.get("links_json") or "[]")
            if links:
                body += "\n\nLinks: " + " · ".join(item["label"] + ": " + item["url"] for item in links)
            cards.append(_card(row["title"], row["state"], body, kind="Slack approval flow"))
            activity.append((row["title"], row["state"], "Slack approval flow"))
        sections.append(_section("approvals", "Approvals",
                                 "Sequential Slack decisions, context, and completion state.", cards))

        groups = [
            ("captures", "Captures", len(captures)),
            ("task-proposals", "Task proposals", len(inbox_rows)),
            ("messages", "Messages", len(message_rows)),
            ("linear", "Linear", len(linear_rows)),
            ("task-updates", "Task updates", len(notion_rows)),
            ("shadow-triage", "Shadow triage", len(triage_rows)),
            ("approvals", "Approvals", len(flow_rows)),
        ]
        all_rows = captures + inbox_rows + message_rows + linear_rows + notion_rows + triage_rows + flow_rows
        needs_review = sum(1 for row in all_rows if row["state"] in ("needs_review", "review", "conflict"))
        approved = sum(1 for row in all_rows if row["state"] in ("approved", "send_authorized"))
        metrics = "".join(
            "<div class='metric'><strong class='metric-value'>%d</strong><span class='metric-label'>%s</span></div>" % item
            for item in ((needs_review, "Needs review"), (approved, "Approved"),
                         (len(triage_rows), "Read-only evaluations"), (len(all_rows), "Open queue items")))
        gates = (
            ("Notion writes", os.environ.get("P_OPS_NOTION_WRITE") == "true"),
            ("Linear writes", os.environ.get("P_OPS_LINEAR_WRITE") == "true"),
            ("Message sending", os.environ.get("P_OPS_SEND_MESSAGES") == "true"),
            ("Slack review posts", bool(self.cfg.get("review_posting"))),
            ("Slack approvals", bool(self.cfg.get("review_interactive"))),
        )
        gate_html = "".join("<span class='gate'>%s: %s</span>" %
                            (html.escape(label), "on" if enabled else "off") for label, enabled in gates)
        if page == "test_lab":
            main_content = self.test_lab_content(task_pages, queue_error)
        elif page == "settings":
            setting_rows = "".join(
                "<div class='setting-row'><div class='setting-copy'><strong>%s</strong><span>%s</span></div>"
                "<span class='switch %s' aria-label='%s'></span></div>" %
                (html.escape(label), html.escape(detail), "on" if enabled else "", "on" if enabled else "off")
                for label, detail, enabled in (
                    ("Daily task review", "Runs the Working Tasks preview once per Mountain Time day.", bool(self.cfg.get("daily_review"))),
                    ("Shadow triage", "Evaluates captured work without making external changes.", bool(self.cfg.get("triage_shadow"))),
                    ("Slack review posts", "Posts review requests only to the approved internal channel.", bool(self.cfg.get("review_posting"))),
                    ("Slack approvals", "Runs one persisted approval question at a time in the review thread.", bool(self.cfg.get("review_interactive"))),
                    ("Slack capture", "Accepts the configured handshake reaction as a capture signal.", bool(self.cfg.get("enabled"))),
                ))
            write_rows = "".join(
                "<div class='setting-row'><div class='setting-copy'><strong>%s</strong><span>%s</span></div>"
                "<span class='switch %s' aria-label='%s'></span></div>" %
                (html.escape(label), html.escape(detail), "on" if enabled else "", "on" if enabled else "off")
                for label, detail, enabled in (
                    ("Notion writes", "Allows approved Working Task updates to be applied.", os.environ.get("P_OPS_NOTION_WRITE") == "true"),
                    ("Notion task creation", "Allows approved task proposals to create Notion records.", os.environ.get("P_OPS_NOTION_CREATE") == "true"),
                    ("Linear writes", "Allows approved engineering issues to be created.", os.environ.get("P_OPS_LINEAR_WRITE") == "true"),
                    ("Message sending", "Allows separately authorized messages to be sent.", os.environ.get("P_OPS_SEND_MESSAGES") == "true"),
                ))
            main_content = ("<div class='hero'><div><p class='eyebrow'>Admin</p><h1>Settings</h1>"
                            "<p class='lede'>View the production configuration and automation boundaries. Change these in Railway, then redeploy.</p>"
                            "</div><span class='mode-pill'>Read only</span></div><div class='settings-grid'>"
                            "<section class='settings-card'><h2>Processing</h2><p>Controls that determine what the agent reads and evaluates.</p>%s</section>"
                            "<section class='settings-card'><h2>External actions</h2><p>High-impact gates remain off until explicitly enabled.</p>%s</section>"
                            "<section class='settings-card'><h2>Destinations</h2><p>Approved internal routing for the production agent.</p>"
                            "<div class='health-row'><span>Review channel</span><span class='health-value'>%s</span></div>"
                            "<div class='health-row'><span>Database</span><span class='health-value'>Persistent SQLite</span></div>"
                            "<div class='health-row'><span>Timezone</span><span class='health-value'>America/Denver</span></div></section>"
                            "<section class='settings-card'><h2>Security</h2><p>Runtime protections applied to every operator session.</p>"
                            "<div class='health-row'><span>Dashboard authentication</span><span class='health-value'>Required</span></div>"
                            "<div class='health-row'><span>Approval tokens</span><span class='health-value'>CSRF protected</span></div>"
                            "<div class='health-row'><span>Browser caching</span><span class='health-value'>Disabled</span></div></section></div>" %
                            (setting_rows, write_rows, html.escape(self.cfg.get("review_channel") or "Not configured")))
        elif page == "activity":
            rows = "".join(
                "<div class='activity-row'><div class='activity-title'><strong>%s</strong><span>%s</span></div>"
                "<div><span class='state state-%s'>%s</span></div><span class='activity-source'>Current queue</span></div>" %
                (html.escape(title), html.escape(source), html.escape(state.replace("_", "-")),
                 html.escape(state.replace("_", " "))) for title, state, source in activity)
            main_content = ("<div class='hero'><div><p class='eyebrow'>Operations</p><h1>Activity</h1>"
                            "<p class='lede'>A combined view of the work currently moving through the Partner Ops queues.</p>"
                            "</div><span class='mode-pill'>%d open</span></div><div class='activity-list'>%s</div>" %
                            (len(activity), rows or _empty("No activity yet", "Captured and evaluated work will appear here.")))
        else:
            completion = 100 if not all_rows else max(0, round((len(all_rows) - needs_review) / len(all_rows) * 100))
            overview = ("<div class='overview-grid'><section class='overview-panel'><h2>Queue health</h2>"
                        "<p>Share of current items that do not require operator attention.</p><div class='progress-track'>"
                        "<div class='progress-value' style='width:%d%%'></div></div><div class='health-row'>"
                        "<span>Clear or progressing</span><span class='health-value'>%d%%</span></div></section>"
                        "<section class='overview-panel'><h2>Agent status</h2><p>Current production processing state.</p>"
                        "<div class='health-row'><span>Runtime</span><span class='health-value'>Online</span></div>"
                        "<div class='health-row'><span>Daily review</span><span class='health-value'>%s</span></div>"
                        "<div class='health-row'><span>Operating mode</span><span class='health-value'>Guarded</span></div></section></div>" %
                        (completion, completion, "Enabled" if self.cfg.get("daily_review") else "Disabled"))
            main_content = ("<div class='hero'><div><p class='eyebrow'>Operations console</p><h1>Partner ops dashboard</h1>"
                            "<p class='lede'>Review recommendations, approve exact actions, and keep partner work moving without bypassing the write or send controls.</p>"
                            "</div><span class='mode-pill'>Guarded mode</span></div><div class='safety'>"
                            "<div class='safety-copy'><strong>Automation safety controls</strong><span>Approvals here only update the action queue. External changes still require their configured gate.</span>"
                            "</div><div class='gates'>%s</div></div><div class='metrics'>%s</div>%s%s" %
                            (gate_html, metrics, overview, "".join(sections)))

        queue_nav = "".join(
            "<a href='/review#%s'><span class='nav-copy'>%s<span>%s</span></span><span class='nav-count'>%d</span></a>" %
            (section_id, _icon(icon), html.escape(label), count)
            for (section_id, label, count), icon in zip(groups, ("capture", "tasks", "message", "linear", "tasks", "triage", "tasks")))
        page_nav = (
            "<a class='%s' href='/review'><span class='nav-copy'>%s<span>Dashboard</span></span></a>"
            "<a class='%s' href='/activity'><span class='nav-copy'>%s<span>Activity</span></span></a>"
            "<a class='%s' href='/test-lab'><span class='nav-copy'>%s<span>Test Lab</span></span></a>" %
            ("active" if page == "dashboard" else "", _icon("dashboard"),
             "active" if page == "activity" else "", _icon("activity"),
             "active" if page == "test_lab" else "", _icon("test")))
        settings_nav = "<a class='%s' href='/settings'><span class='nav-copy'>%s<span>Settings</span></span></a>" % (
            "active" if page == "settings" else "", _icon("settings"))
        notifications = [(label, count, section_id) for section_id, label, count in groups if count]
        notification_items = "".join(
            "<a class='notification-item' href='/review#%s'><strong>%s</strong><span>%d %s in this queue</span></a>" %
            (section_id, html.escape(label), count, "item" if count == 1 else "items")
            for label, count, section_id in notifications)
        if not notification_items:
            notification_items = "<div class='notification-item'><strong>You're all caught up</strong><span>No queue items need attention.</span></div>"
        command_items = "".join(
            "<a class='command-item' data-command-item data-command-query='%s' href='%s'><div><strong>%s</strong>"
            "<span>%s</span></div><kbd>Open</kbd></a>" % tuple(html.escape(value, quote=True) for value in item)
            for item in (
                ("dashboard home overview", "/review", "Dashboard", "Review queue health and pending actions"),
                ("activity history recent work", "/activity", "Activity", "See work across every queue"),
                ("test lab calibration evaluate task", "/test-lab", "Test Lab", "Evaluate live tasks without external writes"),
                ("settings configuration gates", "/settings", "Settings", "Inspect automation controls and destinations"),
            ) + tuple((label.lower(), "/review#" + section_id, label, "%d current items" % count)
                      for section_id, label, count in groups))
        title = {"dashboard": "Dashboard", "activity": "Activity", "test_lab": "Test Lab",
                 "settings": "Settings"}.get(page, "Dashboard")
        shell = ("<div class='shell'><aside class='sidebar'><a class='brand' href='/review' aria-label='Runpod partner ops'>"
                 "<img src='/assets/runpod-logo-purple.png' alt='Runpod'></a><p class='nav-label'>Partner ops</p>"
                 "<nav class='nav'>%s<div class='nav-spacer'></div><p class='nav-label'>Work queues</p>%s"
                 "<div class='nav-spacer'></div><p class='nav-label'>Admin</p>%s</nav>"
                 "<div class='side-footer'><strong><span class='status-dot'></span>Agent online</strong>"
                 "<span>Partner ops · production</span></div></aside><main class='main'><header class='topbar'>"
                 "<span class='topbar-title'>Partner ops / %s</span><div class='topbar-actions'>"
                 "<button class='command-trigger' data-open-command aria-label='Open command search'>%s<span>Search or jump to...</span><span class='shortcut'>⌘K</span></button>"
                 "<div class='notification-wrap'><button class='icon-button' data-notification-button aria-label='Notifications' aria-expanded='false'>%s%s</button>"
                 "<div class='notification-panel' data-notification-panel hidden><div class='popover-head'><strong>Notifications</strong><span>%d open</span></div>%s</div></div>"
                 "<span class='environment'>Runpod</span><span class='avatar' aria-label='Colby'>CH</span></div></header>"
                 "<div class='content'>%s<footer class='page-footer'>Runpod Partner Ops · Internal operator console</footer></div></main></div>"
                 "<div class='command-overlay' data-command-palette hidden><div class='command-dialog' role='dialog' aria-modal='true' aria-label='Command search'>"
                 "<div class='command-input-wrap'>%s<input class='command-input' data-command-input placeholder='Search pages and queues' aria-label='Search commands'>"
                 "<button class='command-close' data-close-command>Esc</button></div><div class='command-results'><p class='command-group-label'>Navigate</p>%s"
                 "<div class='command-empty' data-command-empty hidden>No matching pages or queues.</div></div></div></div>" %
                 (page_nav, queue_nav, settings_nav, html.escape(title), _icon("search"), _icon("bell"),
                  "<span class='notification-badge'>%d</span>" % needs_review if needs_review else "", needs_review,
                  notification_items, main_content, _icon("search"), command_items))
        return shell

    def action(self, kind, action, item_id):
        if kind == "inbox" and action == "approve": self.inbox.approve(item_id)
        elif kind == "inbox" and action == "reject": self.inbox.reject(item_id)
        elif kind == "message" and action == "approve": self.messages.approve(item_id)
        elif kind == "message" and action == "reject": self.messages.reject(item_id)
        elif kind == "message" and action == "authorize": self.messages.authorize_send(item_id)
        elif kind == "linear" and action == "approve": self.linear.approve(item_id)
        elif kind == "linear" and action == "reject": self.linear.reject(item_id)
        elif kind == "notion" and action == "approve": self.notion.approve(item_id)
        elif kind == "notion" and action == "reject": self.notion.reject(item_id)
        else: raise ValueError("Unsupported review action.")


class RuntimeApp:
    def __init__(self, cfg, dashboard, slack_app, triage_client=None, notion_client=None):
        self.cfg, self.dashboard, self.slack_app = cfg, dashboard, slack_app
        self.triage_client, self.notion_client = triage_client, notion_client
        self.approval_flows = ApprovalFlowLedger(cfg["db"])

    def slack_events(self, env, start):
        def respond(code, value):
            data = json.dumps(value).encode()
            start(code, [("Content-Type", "application/json"), ("Content-Length", str(len(data)))])
            return [data]
        try:
            size = int(env.get("CONTENT_LENGTH") or 0)
            if size <= 0 or size > 262144:
                return respond("413 Payload Too Large", {})
            raw = env["wsgi.input"].read(size)
            if not verify(self.cfg["secret"], env.get("HTTP_X_SLACK_REQUEST_TIMESTAMP"),
                          env.get("HTTP_X_SLACK_SIGNATURE"), raw):
                return respond("401 Unauthorized", {})
            body = json.loads(raw)
            if body.get("type") == "url_verification":
                return respond("200 OK", {"challenge": body.get("challenge", "")})
            if body.get("type") == "event_callback":
                capture = accept_reaction(body, self.cfg)
                if capture:
                    self.dashboard.capture.enqueue(capture)
                context = accept_context_reply(body, self.cfg, self.approval_flows)
                if context:
                    self.approval_flows.enqueue(context)
            return respond("200 OK", {"ok": True})
        except (ValueError, TypeError, KeyError, AttributeError):
            return respond("400 Bad Request", {})
        except sqlite3.Error:
            return respond("503 Service Unavailable", {"error": "queue_unavailable"})

    @staticmethod
    def _form(env, maximum=65536):
        size = int(env.get("CONTENT_LENGTH") or 0)
        if size <= 0 or size > maximum:
            raise ValueError("Invalid form size.")
        return {key: values[0] for key, values in urllib.parse.parse_qs(
            env["wsgi.input"].read(size).decode(), keep_blank_values=True).items()}

    def test_lab_action(self, form):
        action = form.get("action", "")
        item_id = form.get("id", "new") if action == "feedback" else "new"
        if not hmac.compare_digest(form.get("csrf", ""), _csrf(self.cfg, "test_lab", action, item_id)):
            raise PermissionError("Invalid test token.")
        ledger = self.dashboard.test_lab
        if action == "validate":
            report = validate_corpus(Path(__file__).with_name("evals") / "p_ops_triage_cases.jsonl")
            return ledger.record("corpus", "Evaluation corpus", {"path": "evals/p_ops_triage_cases.jsonl"}, report)
        if action == "feedback":
            ledger.feedback(item_id, form.get("feedback", ""), form.get("note", ""))
            return item_id
        triage_client = self.triage_client or ClaudeTriageClient()
        run_id = None
        try:
            if action == "evaluate_text":
                bundle = bundle_from_text(form.get("title"), form.get("context"))
                label, kind = form.get("title", "Manual test").strip(), "text"
            elif action == "evaluate_notion":
                notion_client = self.notion_client or NotionClient()
                bundle = bundle_from_notion_url(form.get("task_url", ""), notion_client)
                label, kind = bundle["source"]["text"].splitlines()[0], "notion"
            else:
                raise ValueError("Unsupported test action.")
            run_id = ledger.start(kind, label, bundle)
            return ledger.finish(run_id, evaluate_bundle(bundle, triage_client))
        except Exception as exc:
            label = (form.get("title") or form.get("task_url") or "Test evaluation").strip()[:240]
            safe_error = type(exc).__name__ + ": " + str(exc)[:500]
            if run_id:
                return ledger.finish(run_id, error=safe_error)
            return ledger.record("notion" if action == "evaluate_notion" else "text", label,
                                 {"submitted": label}, error=safe_error)

    def __call__(self, env, start):
        def respond(code, data, content_type="application/json", headers=()):
            raw = data if isinstance(data, bytes) else data.encode()
            base = [("Content-Type", content_type), ("Content-Length", str(len(raw))),
                    ("Cache-Control", "no-store"), ("X-Frame-Options", "DENY"),
                    ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin")]
            start(code, base + list(headers)); return [raw]
        path, method = env.get("PATH_INFO"), env.get("REQUEST_METHOD")
        asset_types = {
            "/assets/runpod-logo-purple.png": "image/png",
            "/assets/runpod-icon.png": "image/png",
            "/assets/p_ops_console.js": "application/javascript; charset=utf-8",
        }
        if path in asset_types and method == "GET":
            filename = path.rsplit("/", 1)[-1]
            asset = Path(__file__).with_name("assets") / filename
            if not asset.is_file():
                return respond("404 Not Found", "{}")
            return respond("200 OK", asset.read_bytes(), asset_types[path],
                           (("Cache-Control", "public, max-age=86400, immutable"),))
        if path == "/healthz" and method == "GET":
            return respond("200 OK", json.dumps({"status": "listening", "p_ops_enabled": self.cfg["runtime_enabled"],
                                                  "capture_enabled": self.cfg["enabled"],
                                                  "triage_shadow_enabled": self.cfg.get("triage_shadow", False),
                                                  "review_posting_enabled": self.cfg.get("review_posting", False),
                                                  "review_interactive_enabled": self.cfg.get("review_interactive", False),
                                                  "review_channel_configured": bool(self.cfg.get("review_channel"))}))
        if not self.cfg["runtime_enabled"]:
            return respond("503 Service Unavailable", json.dumps({"error": "p_ops_disabled"}))
        if path == "/slack/events":
            return self.slack_events(env, start)
        if path == "/slack/interactions" and method == "POST":
            return receive_interaction(env, start, self.cfg, self.approval_flows)
        if path not in {"/review", "/review/action", "/activity", "/settings", "/test-lab", "/test-lab/action"}:
            return respond("404 Not Found", "{}")
        if not _basic_authorized(env, self.cfg):
            return respond("401 Unauthorized", "Authentication required", "text/plain; charset=utf-8",
                           (("WWW-Authenticate", 'Basic realm="p-OPS review"'),))
        if path in {"/review", "/activity", "/settings", "/test-lab"} and method == "GET":
            page = {"/review": "dashboard", "/activity": "activity", "/settings": "settings",
                    "/test-lab": "test_lab"}[path]
            task_pages, queue_error = None, ""
            if page == "test_lab":
                try:
                    task_pages = (self.notion_client or NotionClient()).active_pages()
                except (ValueError, RemoteError):
                    task_pages = []
                    queue_error = "Check the Notion connection, then refresh this page."
            body = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                    "<meta name='theme-color' content='#5d29f0'><title>Partner ops · Runpod</title>"
                    "<link rel='icon' type='image/png' href='/assets/runpod-icon.png'>"
                    "<style>%s</style><script src='/assets/p_ops_console.js' defer></script></head><body>%s</body></html>" %
                    (DASHBOARD_CSS, self.dashboard.render(page, task_pages=task_pages,
                                                          queue_error=queue_error)))
            return respond("200 OK", body, "text/html; charset=utf-8",
                           (("Content-Security-Policy", "default-src 'none'; img-src 'self'; script-src 'self'; style-src 'unsafe-inline'; "
                                                       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),))
        if path == "/review/action" and method == "POST":
            try:
                form = self._form(env, 16384)
                kind, action, item_id = form["kind"], form["action"], form["id"]
                if not hmac.compare_digest(form.get("csrf", ""), _csrf(self.cfg, kind, action, item_id)):
                    return respond("403 Forbidden", "Invalid review token", "text/plain; charset=utf-8")
                self.dashboard.action(kind, action, item_id)
            except (ValueError, KeyError, UnicodeDecodeError, sqlite3.Error):
                return respond("400 Bad Request", "Review action was not applied", "text/plain; charset=utf-8")
            return respond("303 See Other", b"", headers=(("Location", "/review"),))
        if path == "/test-lab/action" and method == "POST":
            try:
                run_id = self.test_lab_action(self._form(env))
            except PermissionError:
                return respond("403 Forbidden", "Invalid test token", "text/plain; charset=utf-8")
            except (ValueError, KeyError, UnicodeDecodeError, sqlite3.Error):
                return respond("400 Bad Request", "Test action was not applied", "text/plain; charset=utf-8")
            return respond("303 See Other", b"", headers=(("Location", "/test-lab#" + run_id),))
        return respond("405 Method Not Allowed", "{}")


def components(cfg):
    path = cfg["db"]
    capture, inbox = CaptureStore(path), InboxLedger(path)
    messages, linear = ApprovalLedger(path), LinearLedger(path)
    notion, triage = NotionLedger(path), ShadowLedger(path)
    return capture, inbox, messages, linear, notion, triage


def create_app():
    cfg = config()
    capture, inbox, messages, linear, notion, triage = components(cfg)
    dashboard = Dashboard(cfg, capture, inbox, messages, linear, notion, triage)
    slack_app = Application(cfg, capture, accept_reaction)
    return RuntimeApp(cfg, dashboard, slack_app)


def run_once(cfg, stores, clients=None, today=None):
    capture, inbox, messages, linear, notion, triage_ledger = stores
    clients = clients or {}
    results = []
    for row in capture.rows(("pending",))[:10]:
        client = clients.get("slack_capture") or SlackCaptureClient()
        results.append(("capture", row["event_id"], resolve_capture(capture, row["event_id"], client)))
    for row in capture.rows(("captured",)):
        inbox.propose(row)
    if cfg.get("triage_shadow"):
        notion_client = clients.get("notion") or NotionClient()
        triage_client = clients.get("triage") or ClaudeTriageClient()
        shadow_rows = run_shadow(inbox, triage_ledger, notion_client, triage_client, current_date=today)
        results.extend(("triage_shadow", item_id, state) for item_id, state in shadow_rows)
        notifications = ReviewNotificationLedger(cfg["db"])
        for item_id, item_state in shadow_rows:
            if item_state != "shadow":
                continue
            row = triage_ledger.get(item_id)
            guarded = json.loads(row["decision_json"])
            if guarded.get("review_required") and cfg.get("review_channel"):
                notifications.queue(row, cfg.get("review_channel", ""), cfg.get("public_url", ""))
        if cfg.get("review_posting"):
            sender = clients.get("review_sender") or SlackReviewSender(cfg["review_channel"])
            for row in notifications.rows(("queued",)):
                results.append(("review_notification", row["triage_id"],
                                notifications.publish(row["triage_id"], cfg["review_channel"], sender)))
        if cfg.get("review_interactive"):
            flows = ApprovalFlowLedger(cfg["db"])
            for notice in notifications.rows(("sent",)):
                try:
                    shadow_row = triage_ledger.get(notice["triage_id"])
                except KeyError:
                    continue
                flows.create(shadow_row, cfg["review_channel"], notice["external_id"], cfg.get("public_url", ""))
            approval_client = clients.get("approval_client") or SlackApprovalClient(cfg["review_channel"])
            results.extend(("approval_flow", flow_id, state)
                           for flow_id, state in process_flows(flows, approval_client))
    if os.environ.get("P_OPS_NOTION_CREATE") == "true":
        client = clients.get("notion") or NotionClient()
        for row in inbox.rows(("approved",)):
            results.append(("inbox", row["id"], create_approved_task(client, inbox, row["id"])))
    if os.environ.get("P_OPS_LINEAR_WRITE") == "true":
        client = clients.get("linear") or LinearClient()
        for row in linear.rows(("approved",)):
            results.append(("linear", row["id"], create_approved_issue(client, linear, row["id"])))
    if os.environ.get("P_OPS_SEND_MESSAGES") == "true":
        sender = clients.get("slack_sender") or SlackSender()
        for row in messages.rows(("send_authorized",)):
            if row["kind"] == "slack":
                results.append(("message", row["id"], send_message(messages, row["id"], sender)))
    state = RuntimeState(cfg["db"])
    day = (today or dt.datetime.now(ZoneInfo("America/Denver")).date()).isoformat()
    if cfg["daily_review"] and state.get("last_daily_preview") != day:
        client = clients.get("notion") or NotionClient()
        results.append(("daily_preview", day, preview(client, notion, dt.date.fromisoformat(day))))
        state.set("last_daily_preview", day)
    if os.environ.get("P_OPS_NOTION_WRITE") == "true" and notion.rows(("approved",)):
        client = clients.get("notion") or NotionClient()
        results.append(("notion", "approved", apply_ready(client, notion, include_auto=False)))
    return results


def worker():
    import fcntl
    cfg = config()
    if not cfg["runtime_enabled"]:
        raise SystemExit("Set P_OPS_ENABLED=true to run the p-OPS worker.")
    stores = components(cfg)
    with open(cfg["db"] + ".worker.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            run_once(cfg, stores)
            time.sleep(max(1, int(os.environ.get("P_OPS_POLL_SECONDS", "5"))))


if __name__ == "__main__":
    worker()
