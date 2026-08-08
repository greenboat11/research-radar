"""
scripts/send_email.py — Send the latest digest via Resend or SMTP.

Resend (recommended):
  Set RESEND_API_KEY and RESEND_FROM_EMAIL in .env.
  config.yaml: email.provider = resend

SMTP (any provider):
  Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS in .env.
  config.yaml: email.provider = smtp
  Gmail: host=smtp.gmail.com, port=587, use an App Password (requires 2FA).

Usage:
  python scripts/send_email.py                        # send latest digest
  python scripts/send_email.py --dry-run              # preview HTML, no send
  python scripts/send_email.py path/to/digest.md      # send specific file
"""

import argparse
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import markdown2
import yaml

ROOT = Path(__file__).parent.parent
CONFIG_FILE = ROOT / "config.yaml"
DIGESTS_DIR = ROOT / "digests"

FLAG_STYLES = {
    "CAPABILITY JUMP": "background:#c0392b;color:#fff;padding:2px 7px;border-radius:3px;font-size:0.82em;font-weight:700;",
    "NEW RESEARCH":    "background:#2471a3;color:#fff;padding:2px 7px;border-radius:3px;font-size:0.82em;font-weight:700;",
    "NOTABLE FINDING": "background:#b7770d;color:#fff;padding:2px 7px;border-radius:3px;font-size:0.82em;font-weight:700;",
}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Research Radar</title></head>
<body style="margin:0;padding:0;background:#0d1117;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1117;">
<tr><td align="center" style="padding:32px 16px;">
<table width="680" cellpadding="0" cellspacing="0"
       style="max-width:680px;background:#161b22;border-radius:8px;overflow:hidden;">
  <tr><td style="background:#0d1117;padding:28px 36px 20px;">
    <div style="font-family:monospace;font-size:22px;font-weight:700;color:#e6edf3;">
      &#x1F6F0; Research Radar
    </div>
    <div style="font-family:monospace;font-size:12px;color:#8b949e;margin-top:6px;">
      AI-security research gap finder
    </div>
    <div style="margin-top:12px;background:#21262d;border-radius:4px;padding:8px 14px;
                font-family:monospace;font-size:12px;color:#58a6ff;">
      {metadata_bar}
    </div>
  </td></tr>
  <tr><td style="padding:28px 36px 36px;color:#c9d1d9;font-size:15px;line-height:1.7;">
    {body}
  </td></tr>
  <tr><td style="background:#0d1117;padding:16px 36px;font-size:12px;color:#484f58;
                 font-family:monospace;">
    Research Radar &mdash; AI-security gap finder &mdash;
    <a href="https://github.com/yourusername/research-radar" style="color:#484f58;">GitHub</a>
  </td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def md_to_html(md_text: str) -> str:
    html = markdown2.markdown(md_text, extras=["fenced-code-blocks", "tables", "strike"])
    for flag, style in FLAG_STYLES.items():
        html = html.replace(flag, f'<span style="{style}">{flag}</span>')
    html = re.sub(r"<h1([^>]*)>", r'<h1\1 style="color:#e6edf3;font-size:20px;">', html)
    html = re.sub(r"<h2([^>]*)>", r'<h2\1 style="color:#e6edf3;font-size:17px;border-bottom:1px solid #30363d;padding-bottom:6px;">', html)
    html = re.sub(r"<h3([^>]*)>", r'<h3\1 style="color:#cdd9e5;font-size:15px;">', html)
    html = re.sub(r"<a ", r'<a style="color:#58a6ff;" ', html)
    html = re.sub(r"<hr\s*/?>", r'<hr style="border:none;border-top:1px solid #30363d;margin:24px 0;">', html)
    return html


def send_resend(subject: str, html: str, to: str, from_email: str):
    import resend
    resend.api_key = os.environ["RESEND_API_KEY"]
    resp = resend.Emails.send({"from": from_email, "to": [to], "subject": subject, "html": html})
    print(f"Sent via Resend. ID: {resp.get('id', resp)}")


def send_smtp(subject: str, html: str, to: str):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(user, password)
        server.sendmail(user, to, msg.as_string())
    print(f"Sent via SMTP ({host}:{port}) to {to}")


def send(digest_file: Path = None, config: dict = None):
    load_env()

    if config is None:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f)

    # Find digest
    if digest_file is None:
        files = sorted(f for f in DIGESTS_DIR.glob("*.md") if f.stem != "latest")
        if not files:
            raise FileNotFoundError("No digest files found in digests/")
        digest_file = files[-1]

    md_text = digest_file.read_text(encoding="utf-8")
    date_str = digest_file.stem
    body_html = md_to_html(md_text)
    metadata_bar = f"Weekly Digest  |  {date_str}"
    full_html = HTML_TEMPLATE.format(metadata_bar=metadata_bar, body=body_html)

    subject_prefix = config.get("email", {}).get("subject_prefix", "Research Radar")
    subject = f"{subject_prefix} — {date_str}"

    to = os.environ.get("EMAIL_TO", "")
    if not to:
        raise ValueError("EMAIL_TO not set in environment")

    provider = config.get("email", {}).get("provider", "resend")

    if provider == "resend":
        from_email = os.environ.get("RESEND_FROM_EMAIL", "")
        if not from_email:
            raise ValueError("RESEND_FROM_EMAIL not set")
        send_resend(subject, full_html, to, from_email)
    elif provider == "smtp":
        send_smtp(subject, full_html, to)
    else:
        raise ValueError(f"Unknown email provider: {provider}")


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("digest", nargs="?", help="Path to digest .md file (default: latest)")
    parser.add_argument("--dry-run", action="store_true", help="Print HTML, do not send")
    args = parser.parse_args()

    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)

    digest_file = Path(args.digest) if args.digest else None
    if digest_file is None:
        files = sorted(f for f in DIGESTS_DIR.glob("*.md") if f.stem != "latest")
        if not files:
            print("No digest files found.")
            return
        digest_file = files[-1]

    if args.dry_run:
        md_text = digest_file.read_text()
        html = md_to_html(md_text)
        full = HTML_TEMPLATE.format(metadata_bar=digest_file.stem, body=html)
        print(f"Subject: Research Radar — {digest_file.stem}")
        print(f"File: {digest_file}")
        print("\n--- HTML preview (first 3000 chars) ---")
        print(full[:3000])
        return

    send(digest_file, config)


if __name__ == "__main__":
    main()
