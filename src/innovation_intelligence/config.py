import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


# -------------------------------------------------------------------
# 🔧 Base paths & .env loading
# -------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]

ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


# -------------------------------------------------------------------
# 🔧 Dataclasses for settings
# -------------------------------------------------------------------
@dataclass
class DatabaseSettings:
    # PostgreSQL DSN, e.g. postgresql+psycopg2://user:pass@host:5432/dbname
    url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://user:password@localhost:5432/health_intel",
    )


@dataclass
class AwsSettings:
    region: str = os.getenv("AWS_REGION", "eu-west-3")
    profile: str = os.getenv("AWS_PROFILE", "nail")

    # Default Bedrock Claude model ID
    bedrock_model_id: str = os.getenv(
        "BEDROCK_MODEL_ID",
        "eu.anthropic.claude-sonnet-4-20250514-v1:0",
    )

    # Global analysis period label (e.g. '2025')
    period_name: str = os.getenv("PERIOD_NAME", "2025")


@dataclass
class PodcastIndexSettings:
    api_key: str = os.getenv("PODCAST_API_KEY", "")
    api_secret: str = os.getenv("PODCAST_API_SECRET", "")


@dataclass
class TranscriptionSettings:
    # WhisperX / device config
    device: str = os.getenv("DEVICE", "cuda")
    model_size: str = os.getenv("MODEL_SIZE", "large-v2")
    compute_type: str = os.getenv("COMPUTE_TYPE", "float16")  # or "int8"

    # Hugging Face token for diarization
    hf_token: str = os.getenv("HF_TOKEN", "")


@dataclass
class EmbeddingsSettings:
    # Hugging Face model
    model_name: str = os.getenv(
        "EMBEDDING_MODEL_NAME",
        "BAAI/bge-m3",
    )

    # Device: "cpu" | "cuda"
    device: str = os.getenv("EMBEDDING_DEVICE", "cpu")

    # Normalize vectors (recommended for cosine similarity)
    normalize: bool = True


@dataclass
class PathSettings:
    # Base data dir: override with DATA_ROOT in .env if you want
    data_root: Path = Path(
        os.getenv("DATA_ROOT", PROJECT_ROOT / "data")
    ).resolve()

    # -------------------------
    # Podcast paths
    # -------------------------
    @property
    def audio_dir(self) -> Path:
        """Where raw / trimmed audio files are stored."""
        return (self.data_root / "podcasts" / "audio").resolve()

    @property
    def transcripts_dir(self) -> Path:
        """Where podcast transcript JSON files are stored (legacy alias)."""
        return self.podcasts_transcripts_dir

    @property
    def podcasts_transcripts_dir(self) -> Path:
        """Where podcast transcript JSON files are stored."""
        return (self.data_root / "podcasts" / "transcripts").resolve()

    # -------------------------
    # Document paths
    # -------------------------
    @property
    def documents_raw_dir(self) -> Path:
        """Where raw document files (PDFs) are stored."""
        return (self.data_root / "documents" / "raw").resolve()

    @property
    def documents_transcripts_dir(self) -> Path:
        """Where document text extractions are stored."""
        return (self.data_root / "documents" / "transcripts").resolve()

    # -------------------------
    # Output paths
    # -------------------------
    @property
    def experiments_dir(self) -> Path:
        """Generic output / experiments folder for analysis results."""
        return (self.data_root / "output" / "experiments").resolve()

    @property
    def vector_index_dir(self) -> Path:
        """Where FAISS vector indices are stored (for RAG / dimensions assessment)."""
        return (self.data_root / "vector_index" / "faiss_store_podcast").resolve()

    def ensure_dirs(self) -> None:
        """
        Create all standard directories if missing.
        Call this once at startup if desired.
        """
        for p in [
            self.audio_dir,
            self.podcasts_transcripts_dir,
            self.documents_raw_dir,
            self.documents_transcripts_dir,
            self.experiments_dir,
            self.vector_index_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)


@dataclass
class AppSettings:
    db: DatabaseSettings = field(default_factory=DatabaseSettings)
    aws: AwsSettings = field(default_factory=AwsSettings)
    podcast_index: PodcastIndexSettings = field(default_factory=PodcastIndexSettings)
    transcription: TranscriptionSettings = field(default_factory=TranscriptionSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    embeddings: EmbeddingsSettings = field(default_factory=EmbeddingsSettings)

    log_level: str = os.getenv("LOG_LEVEL", "INFO")


# Global settings instance
settings = AppSettings()