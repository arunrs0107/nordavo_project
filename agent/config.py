"""Settings come from environment variables (a local .env file is loaded if present).
Nothing secret lives in the repo."""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
FAQ_PATH = ROOT / "data" / "faq.md"
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"

DEFAULT_URL = "https://nakywgkztvzkihdjnwbc.supabase.co/functions/v1/helpdesk"


@dataclass
class Settings:
    helpdesk_url: str
    helpdesk_key: str
    candidate_email: str
    candidate_name: Optional[str]
    model: str
    confidence_min: float
    price_in: Optional[float]
    price_out: Optional[float]
    provider: str = "anthropic"         
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_min_interval: float = 4.0        


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing environment variable {name}. See .env.example.")
    return value


def _float(name: str) -> Optional[float]:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def load_settings() -> Settings:
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    if provider == "anthropic":
        _required("ANTHROPIC_API_KEY") 
        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
        extra = {}
    elif provider == "openai_compat":
        model = _required("LLM_MODEL") 
        extra = dict(llm_base_url=_required("LLM_BASE_URL"),
                     llm_api_key=os.environ.get("LLM_API_KEY", "").strip(), 
                     llm_min_interval=float(os.environ.get("LLM_MIN_INTERVAL", "4")))
    else:
        raise SystemExit("LLM_PROVIDER must be 'anthropic' or 'openai_compat'")
    return Settings(
        helpdesk_url=os.environ.get("HELPDESK_URL", DEFAULT_URL).rstrip("/"),
        helpdesk_key=_required("HELPDESK_KEY"),
        candidate_email=_required("CANDIDATE_EMAIL"),
        candidate_name=os.environ.get("CANDIDATE_NAME") or None,
        model=model,
        provider=provider,
        confidence_min=float(os.environ.get("CONFIDENCE_MIN", "0.75")),
        price_in=_float("PRICE_IN_PER_MTOK"),
        price_out=_float("PRICE_OUT_PER_MTOK"),
        **extra,
    )
