import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/certificates.db"
    storage_dir: Path = Path("./data/certificates")
    max_recipients_per_job: int = 1000
    # Re-queue jobs left PENDING/PROCESSING by a crash or restart.
    resume_on_startup: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            database_url=os.getenv("DATABASE_URL", defaults.database_url),
            storage_dir=Path(os.getenv("STORAGE_DIR", str(defaults.storage_dir))),
            max_recipients_per_job=int(
                os.getenv("MAX_RECIPIENTS_PER_JOB", defaults.max_recipients_per_job)
            ),
            resume_on_startup=os.getenv("RESUME_ON_STARTUP", "true").lower()
            in ("1", "true", "yes"),
        )
