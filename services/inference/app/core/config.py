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
        repo_root = Path(__file__).resolve().parents[4]
        default_legacy_repo = repo_root.parent / "blueprint-rec"
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
        self.openrouter_vision_model = os.getenv("INFERENCE_OPENROUTER_VISION_MODEL", "google/gemini-3.1-pro-preview")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self.enable_gemini_vision = os.getenv("INFERENCE_ENABLE_GEMINI", "true").lower() in {"1", "true", "yes"}
        self.gemini_vision_model = os.getenv("INFERENCE_GEMINI_VISION_MODEL", "gemini-2.5-flash")
        self.legacy_pipeline_repo = str(Path(os.getenv("INFERENCE_LEGACY_PIPELINE_REPO", str(default_legacy_repo))).resolve())
        self.legacy_pipeline_script = str(
            Path(
                os.getenv(
                    "INFERENCE_LEGACY_PIPELINE_SCRIPT",
                    str(Path(self.legacy_pipeline_repo) / "scripts" / "run_v3_number_pipeline.py"),
                )
            ).resolve()
        )
        self.legacy_pipeline_timeout_seconds = max(30, int(os.getenv("INFERENCE_LEGACY_PIPELINE_TIMEOUT_SECONDS", "3600")))
        self.legacy_fallback_pipeline_timeout_seconds = max(
            30,
            int(os.getenv("INFERENCE_LEGACY_FALLBACK_PIPELINE_TIMEOUT_SECONDS", "180")),
        )
        self.legacy_fallback_detect_scales = os.getenv("INFERENCE_LEGACY_FALLBACK_DETECT_SCALES", "1.0")
        self.legacy_fallback_tile_size = max(256, int(os.getenv("INFERENCE_LEGACY_FALLBACK_TILE_SIZE", "1536")))
        self.legacy_fallback_tile_overlap = max(0, int(os.getenv("INFERENCE_LEGACY_FALLBACK_TILE_OVERLAP", "96")))
        self.legacy_fallback_disable_gemini = os.getenv(
            "INFERENCE_LEGACY_FALLBACK_DISABLE_GEMINI",
            "false",
        ).lower() in {"1", "true", "yes"}
        self.legacy_fallback_disable_gemini_tile_proposals = os.getenv(
            "INFERENCE_LEGACY_FALLBACK_DISABLE_GEMINI_TILE_PROPOSALS",
            "true",
        ).lower() in {"1", "true", "yes"}
        self.legacy_emergency_fallback_enabled = os.getenv(
            "INFERENCE_LEGACY_EMERGENCY_FALLBACK_ENABLED",
            "true",
        ).lower() in {"1", "true", "yes"}
        self.legacy_emergency_fallback_pipeline_timeout_seconds = max(
            30,
            int(os.getenv("INFERENCE_LEGACY_EMERGENCY_FALLBACK_PIPELINE_TIMEOUT_SECONDS", "120")),
        )


settings = Settings()
