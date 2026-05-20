import logging
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import Config

logger = logging.getLogger(__name__)


def send_email(
    subject: str,
    html_body: str,
    config: Config,
    attachments: list[tuple[str, bytes]] | None = None,
) -> None:
    """
    attachments: list of (filename, raw_bytes) pairs, e.g. [("paper.pdf", b"...")]
    """
    # Use "mixed" to allow attachments; nest "alternative" inside for proper HTML handling
    outer = MIMEMultipart("mixed")
    outer["Subject"] = subject
    outer["From"] = config.gmail_user
    outer["To"] = ", ".join(config.recipient_emails)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    outer.attach(alt)

    for filename, data in (attachments or []):
        part = MIMEBase("application", "pdf")
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        outer.attach(part)

    logger.info(
        "Sending email to %s (%d attachment(s))",
        config.recipient_emails,
        len(attachments or []),
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(config.gmail_user, config.gmail_app_password)
        server.sendmail(config.gmail_user, config.recipient_emails, outer.as_string())
    logger.info("Email sent successfully")
