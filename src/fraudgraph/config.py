"""Central configuration. All secrets come from the environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional, so the package imports without python-dotenv installed
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_a, **_k):  # type: ignore
        return False

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _path(env_name: str, default: Path) -> Path:
    raw = os.getenv(env_name)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Paths:
    root: Path = PROJECT_ROOT
    raw: Path = field(default_factory=lambda: _path("FG_RAW_DIR", PROJECT_ROOT / "data" / "raw"))
    build: Path = field(default_factory=lambda: _path("FG_BUILD_DIR", PROJECT_ROOT / "build"))
    cases_out: Path = field(default_factory=lambda: _path("FG_CASES_DIR", PROJECT_ROOT / "cases"))
    docs: Path = field(default_factory=lambda: PROJECT_ROOT / "docs")

    # raw dataset files
    @property
    def transactions_csv(self) -> Path:
        return self.raw / "transactions.csv"

    @property
    def identity_csv(self) -> Path:
        return self.raw / "identity.csv"

    @property
    def closed_cases_csv(self) -> Path:
        return self.raw / "closed_cases_history.csv"

    @property
    def case_pack_csv(self) -> Path:
        return self.raw / "case_pack.csv"

    @property
    def dataset_readme(self) -> Path:
        return self.raw / "README.md"

    # build artefacts
    @property
    def tx_core(self) -> Path:
        return self.build / "transactions_core.parquet"

    @property
    def tx_vcols(self) -> Path:
        return self.build / "transactions_vcols.parquet"

    @property
    def identity_parquet(self) -> Path:
        return self.build / "identity.parquet"

    @property
    def cards_parquet(self) -> Path:
        return self.build / "cards.parquet"

    @property
    def devices_parquet(self) -> Path:
        return self.build / "device_profiles.parquet"

    @property
    def closed_cases_parquet(self) -> Path:
        return self.build / "closed_cases.parquet"

    @property
    def quality_report(self) -> Path:
        return self.build / "ingest_quality_report.json"


@dataclass(frozen=True)
class TigerGraphSettings:
    host: str = os.getenv("TG_HOST", "")
    graph: str = os.getenv("TG_GRAPH", "FraudInvestigation")
    username: str = os.getenv("TG_USERNAME", "")
    password: str = os.getenv("TG_PASSWORD", "")
    secret: str = os.getenv("TG_SECRET", "")
    token: str = os.getenv("TG_TOKEN", "")
    rest_port: str = os.getenv("TG_REST_PORT", "443")
    gs_port: str = os.getenv("TG_GS_PORT", "443")
    # Savanna workspaces speak https on 443; CE on 9000/14240.
    use_tls: bool = os.getenv("TG_USE_TLS", "true").lower() == "true"

    @property
    def configured(self) -> bool:
        return bool(self.host and (self.password or self.secret or self.token))


@dataclass(frozen=True)
class LLMSettings:
    provider: str = os.getenv("FG_LLM_PROVIDER", "gemini")  # gemini | anthropic | none
    model: str = os.getenv("FG_LLM_MODEL", "gemini-flash-latest")
    api_key: str = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or ""
    )
    max_output_tokens: int = int(os.getenv("FG_LLM_MAX_TOKENS", "2048"))
    temperature: float = float(os.getenv("FG_LLM_TEMPERATURE", "0.2"))
    timeout_s: float = float(os.getenv("FG_LLM_TIMEOUT", "60"))
    # free tiers are rate limited per minute; space calls out rather than
    # burning the allowance in the first few cases and degrading silently
    min_interval_s: float = float(os.getenv("FG_LLM_MIN_INTERVAL", "4.5"))

    @property
    def configured(self) -> bool:
        return self.provider != "none" and bool(self.api_key)


@dataclass(frozen=True)
class RuntimeSettings:
    """Knobs that change agent behaviour, kept out of the code paths."""

    # graph backend preference: "tigergraph" | "mcp" | "local" | "auto"
    graph_backend: str = os.getenv("FG_GRAPH_BACKEND", "auto")
    max_investigation_steps: int = int(os.getenv("FG_MAX_STEPS", "12"))
    # Historical window: closed cases cover Jul-Oct; the exam covers Nov-Dec.
    history_end: str = os.getenv("FG_HISTORY_END", "2016-11-01")
    api_host: str = os.getenv("FG_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("FG_API_PORT", "8077"))


PATHS = Paths()
TG = TigerGraphSettings()
LLM = LLMSettings()
RUNTIME = RuntimeSettings()


def ensure_dirs() -> None:
    for p in (PATHS.build, PATHS.cases_out):
        p.mkdir(parents=True, exist_ok=True)
