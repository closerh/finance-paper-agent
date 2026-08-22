import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Anthropic
    anthropic_api_key: str = field(default_factory=lambda: os.environ["ANTHROPIC_API_KEY"])
    claude_model: str = "claude-sonnet-4-6"

    # Gmail
    gmail_user: str = field(default_factory=lambda: os.environ["GMAIL_USER"])
    gmail_app_password: str = field(default_factory=lambda: os.environ["GMAIL_APP_PASSWORD"])
    recipient_emails: list[str] = field(
        default_factory=lambda: os.environ.get(
            "RECIPIENT_EMAILS", os.environ.get("GMAIL_USER", "")
        ).split(",")
    )

    # Paper fetching
    arxiv_categories: list[str] = field(default_factory=lambda: [
        "q-fin.GN",  # General Finance
        "q-fin.EC",  # Economics
        "q-fin.PM",  # Portfolio Management
        "q-fin.RM",  # Risk Management
        "q-fin.ST",  # Statistical Finance
        "q-fin.TR",  # Trading and Market Microstructure
    ])
    lookback_days: int = 10
    top_n: int = 5


def load_config() -> Config:
    return Config()
