"""
validator/notifier.py — Email and webhook dispatch for backtest validation reports.

Supports:
- SMTP email with HTML formatting + markdown attachment.
- Reading credentials from agent_config.yaml or environment variables.
- Dry-run / test email dispatch.
"""

import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional, Dict, Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def markdown_to_simple_html(markdown_text: str) -> str:
    """Convert basic markdown to responsive, styled HTML suitable for email clients."""
    lines = markdown_text.strip().split("\n")
    html_lines = []
    in_table = False
    table_header_done = False

    for line in lines:
        stripped = line.strip()

        # Horizontal rule
        if stripped == "---":
            if in_table:
                html_lines.append("</table>")
                in_table = False
                table_header_done = False
            html_lines.append("<hr style='border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;'>")
            continue

        # Headers
        if stripped.startswith("# "):
            html_lines.append(f"<h1 style='color: #1a202c; font-size: 22px; border-bottom: 2px solid #3182ce; padding-bottom: 8px;'>{stripped[2:]}</h1>")
            continue
        if stripped.startswith("## "):
            html_lines.append(f"<h2 style='color: #2b6cb0; font-size: 18px; margin-top: 20px;'>{stripped[3:]}</h2>")
            continue
        if stripped.startswith("### "):
            html_lines.append(f"<h3 style='color: #4a5568; font-size: 15px; margin-top: 15px;'>{stripped[4:]}</h3>")
            continue

        # Tables
        if "|" in stripped and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c).issubset({"-", ":", " "}) for c in cells):
                # separator row
                table_header_done = True
                continue

            if not in_table:
                in_table = True
                table_header_done = False
                html_lines.append("<table style='width: 100%; border-collapse: collapse; margin: 12px 0; font-family: monospace; font-size: 13px;'>")

            if not table_header_done:
                row_html = "".join(f"<th style='background: #edf2f7; color: #2d3748; padding: 8px 12px; border: 1px solid #cbd5e0; text-align: left;'>{c}</th>" for c in cells)
                html_lines.append(f"<tr>{row_html}</tr>")
            else:
                row_html = "".join(f"<td style='padding: 6px 12px; border: 1px solid #e2e8f0;'>{c}</td>" for c in cells)
                html_lines.append(f"<tr>{row_html}</tr>")
            continue
        else:
            if in_table:
                html_lines.append("</table>")
                in_table = False
                table_header_done = False

        # Bullet points
        if stripped.startswith("- "):
            content = stripped[2:]
            # Style alerts with bold / colored badges
            html_lines.append(f"<li style='margin: 4px 0; color: #2d3748;'>{content}</li>")
            continue

        # Empty lines
        if not stripped:
            html_lines.append("<p style='margin: 6px 0;'></p>")
            continue

        # Paragraphs
        html_lines.append(f"<p style='margin: 6px 0; color: #2d3748; line-height: 1.5;'>{stripped}</p>")

    if in_table:
        html_lines.append("</table>")

    body_content = "\n".join(html_lines)
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f7fafc; padding: 24px;">
      <div style="max-width: 800px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 24px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);">
        {body_content}
        <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #a0aec0; text-align: center;">
          Sent by Crypto Analyzer Automated Backtest Validator
        </div>
      </div>
    </body>
    </html>
    """


def send_email_report(
    report_md: str,
    run_id: int,
    config: Dict[str, Any],
    report_file_path: Optional[str] = None,
    alerts_count: int = 0,
    critical_count: int = 0,
) -> bool:
    """Send backtest validation report via SMTP email."""
    email_cfg = config.get("notification", {}).get("email", {})

    smtp_server = os.environ.get("SMTP_SERVER") or email_cfg.get("smtp_server")
    smtp_port = int(os.environ.get("SMTP_PORT") or email_cfg.get("smtp_port", 587))
    from_addr = os.environ.get("SMTP_FROM") or email_cfg.get("from_address")
    to_addr = os.environ.get("SMTP_TO") or email_cfg.get("to_address")
    username = os.environ.get("SMTP_USERNAME") or email_cfg.get("username") or from_addr
    password = os.environ.get("SMTP_PASSWORD") or email_cfg.get("password")
    use_tls = email_cfg.get("use_tls", True)

    if not smtp_server or not to_addr or not from_addr:
        print("[NOTIFIER] ⚠ Email notification skipped: SMTP server, To, or From address not configured.")
        print("[NOTIFIER] Please update 'notification.email' in validator/agent_config.yaml or set SMTP_* env vars.")
        return False

    if not password:
        print(f"[NOTIFIER] ⚠ Email notification paused for {to_addr}:")
        print("  -> Google/Gmail requires a 16-character 'App Password' to send automated emails.")
        print("  -> You can set it in 'validator/agent_config.yaml' (password: \"...\")")
        print("     or in PowerShell via:  $env:SMTP_PASSWORD = \"your-16-char-app-password\"")
        return False

    status_tag = "🔴 CRITICAL" if critical_count > 0 else ("🟡 WARNING" if alerts_count > 0 else "🟢 HEALTHY")
    subject = f"[Crypto Validator] Run #{run_id} - {status_tag} Audit Report"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    # Plain text version
    part1 = MIMEText(report_md, "plain", "utf-8")
    msg.attach(part1)

    # HTML version
    html_content = markdown_to_simple_html(report_md)
    part2 = MIMEText(html_content, "html", "utf-8")
    msg.attach(part2)

    # Optional: Attach raw markdown file
    if report_file_path and os.path.exists(report_file_path):
        try:
            with open(report_file_path, "rb") as f:
                attachment = MIMEBase("application", "octet-stream")
                attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition",
                f"attachment; filename={os.path.basename(report_file_path)}",
            )
            msg.attach(attachment)
        except Exception as e:
            print(f"[NOTIFIER] Warning: Could not attach report file: {e}")

    try:
        print(f"[NOTIFIER] Connecting to SMTP server {smtp_server}:{smtp_port}...")
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
            if use_tls:
                server.starttls()

        if username and password:
            server.login(username, password)

        server.sendmail(from_addr, [to_addr], msg.as_string())
        server.quit()
        print(f"[NOTIFIER] [OK] Email successfully sent to {to_addr}")
        return True
    except Exception as e:
        print(f"[NOTIFIER] [ERROR] Failed to send email: {e}")
        return False


if __name__ == "__main__":
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="Test email notification")
    parser.add_argument("--test", action="store_true", help="Send a test email using agent_config.yaml")
    args = parser.parse_args()

    cfg_path = Path(__file__).resolve().parent / "agent_config.yaml"
    with open(cfg_path, "r") as f:
        cfg = yaml.safe_load(f)

    test_md = """# Backtest Validation Test Report
---
## Status
Everything is working properly.

| Metric | Value |
|---|---|
| Test Status | SUCCESS |
| Timestamp | Just Now |

- ✅ SMTP connection established successfully.
"""
    send_email_report(test_md, run_id=0, config=cfg)
