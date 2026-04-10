from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
ENV_LOCAL_PATH = Path(__file__).resolve().parents[2] / ".env.local"
load_dotenv(ENV_PATH)
load_dotenv(ENV_LOCAL_PATH, override=True)


class Settings:
    def __init__(self) -> None:
        self.debug = os.getenv("INFERENCE_DEBUG", "").lower() in {"1", "true", "yes"}
        self.storage_dir = str((Path(__file__).resolve().parents[2] / os.getenv("INFERENCE_STORAGE_DIR", "var")).resolve())
        self.storage_mount_path = os.getenv("INFERENCE_STORAGE_MOUNT_PATH", "/storage")
        self.app_title = os.getenv("INFERENCE_APP_TITLE", "Blueprint annotation session service")
        default_origins = [
            *(f"http://localhost:{port}" for port in range(3000, 3101)),
            *(f"http://127.0.0.1:{port}" for port in range(3000, 3101)),
        ]
        raw_origins = os.getenv(
            "INFERENCE_CORS_ORIGINS",
            ",".join(default_origins),
        )
        self.cors_origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.enable_openai_vision = os.getenv("INFERENCE_ENABLE_OPENAI_VISION", "true").lower() in {"1", "true", "yes"}
        self.openai_vision_model = os.getenv("INFERENCE_OPENAI_VISION_MODEL", "gpt-4.1")
        self.openai_vision_max_candidates = max(1, int(os.getenv("INFERENCE_OPENAI_VISION_MAX_CANDIDATES", "36")))
        self.openai_vision_max_candidates_low_res = max(1, int(os.getenv("INFERENCE_OPENAI_VISION_MAX_CANDIDATES_LOW_RES", "24")))
        self.openai_vision_vocab_max_tiles = max(1, int(os.getenv("INFERENCE_OPENAI_VISION_VOCAB_MAX_TILES", "4")))
        self.openai_vision_timeout_seconds = max(5.0, float(os.getenv("INFERENCE_OPENAI_VISION_TIMEOUT_SECONDS", "45")))
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.enable_openrouter_vision = os.getenv("INFERENCE_ENABLE_OPENROUTER_VISION", "true").lower() in {"1", "true", "yes"}
        self.openrouter_vision_model = os.getenv("INFERENCE_OPENROUTER_VISION_MODEL", "openai/gpt-4.1")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self.enable_gemini_vision = os.getenv("INFERENCE_ENABLE_GEMINI", "true").lower() in {"1", "true", "yes"}
        self.gemini_vision_model = os.getenv("INFERENCE_GEMINI_VISION_MODEL", "gemini-2.5-flash")


settings = Settings()
