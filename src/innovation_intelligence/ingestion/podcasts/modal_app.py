import os 
from dotenv import load_dotenv

load_dotenv()

import json
import modal


# CUDA base image config
cuda_version = "12.4.0"
flavor = "devel"
operating_sys = "ubuntu22.04"
tag = f"{cuda_version}-{flavor}-{operating_sys}"

image = (
    modal.Image.from_registry(f"nvidia/cuda:{tag}", add_python="3.11")
    .apt_install(
        "git",
        "ffmpeg",
        "pkg-config",
        "build-essential",
        "clang",
        "libavformat-dev",
        "libavcodec-dev",
        "libavdevice-dev",
        "libavfilter-dev",
        "libswresample-dev",
        "libswscale-dev",
        "libssl-dev",
        "libffi-dev",
        "python3-dev",
    )
    .pip_install(
        "torch==2.3.1",
        "torchaudio==2.3.1",
        "numpy>=2.0,<3.0",
        "pgvector==0.4.2",
        "psycopg2-binary>=2.0",
        "boto3==1.42.21"
    )
    .pip_install(
        "ctranslate2==4.4.0",
        "ffmpeg-python",
        "matplotlib",
        "python-dotenv",
        "git+https://github.com/m-bain/whisperx.git@v3.2.0",
    )
)

app = modal.App("whisperx-podcast-pipeline", image=image)

GPU_CONFIG = "H100"
CACHE_DIR = "/cache"
cache_vol = modal.Volume.from_name("whisper-cache", create_if_missing=True)


@app.cls(
    gpu=GPU_CONFIG,
    volumes={CACHE_DIR: cache_vol},
    scaledown_window=60 * 10,
    timeout=60 * 60,
    secrets=[modal.Secret.from_name("hf_token")],
)
@modal.concurrent(max_inputs=15)
class Model:
    @modal.enter()
    def setup(self):
        import numpy as np

        # compatibility shims for older pyannote that still uses NaN / NAN
        for attr in ("NaN", "NAN"):
            if not hasattr(np, attr):
                setattr(np, attr, np.nan)

        import whisperx

        self.device = "cuda"
        self.asr_model = whisperx.load_model(
            "large-v2",
            self.device,
            compute_type="float16",
            download_root=CACHE_DIR,
        )

        hf_token = os.getenv("HF_TOKEN", "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")
        if not hf_token:
            raise RuntimeError(
                "HF_TOKEN is not set in environment/.env (needed for pyannote models)."
            )

        self.diar_model = whisperx.diarize.DiarizationPipeline(
            use_auth_token=hf_token,
            device=self.device,
        )

        self.align_cache = {}  # lang_code -> (align_model, metadata)

    @modal.method()
    def transcribe_with_diarization(self, audio_bytes: bytes) -> str:
        """
        Takes raw audio bytes.
        Returns a JSON string (WhisperX+pyannote result) to avoid numpy in client.
        """
        import whisperx

        tmp_path = "input_audio.m4a"
        with open(tmp_path, "wb") as f:
            f.write(audio_bytes)

        audio = whisperx.load_audio(tmp_path)

        # 1) ASR
        asr = self.asr_model.transcribe(audio, batch_size=16)
        lang = asr.get("language", "en")

        # 2) Alignment (per-language cache)
        if lang not in self.align_cache:
            align_model, metadata = whisperx.load_align_model(
                language_code=lang,
                device=self.device,
            )
            self.align_cache[lang] = (align_model, metadata)
        else:
            align_model, metadata = self.align_cache[lang]

        aligned = whisperx.align(
            asr["segments"],
            align_model,
            metadata,
            audio,
            self.device,
            return_char_alignments=False,
        )

        # 3) Diarization
        diar_segments = self.diar_model(audio)

        # 4) Merge speakers
        final = whisperx.assign_word_speakers(diar_segments, aligned)

        # JSON encode on the server → client gets plain string
        return json.dumps(final, ensure_ascii=False)


@app.local_entrypoint()
def main(
    podcast_name: str = "Huberman Lab",
    max_episodes: int = 3,
    trim_seconds: int = 60,
    skip_speaker_id: bool = False,
    skip_indexing: bool = False,
):
    """
    Run the podcast ingestion pipeline via Modal.

    Usage:
        modal run src/innovation_intelligence/ingestion/podcasts/modal_app.py
        modal run src/innovation_intelligence/ingestion/podcasts/modal_app.py --podcast-name "The Peter Attia Drive" --max-episodes 5

    Args:
        podcast_name: Name of the podcast to ingest
        max_episodes: Maximum episodes to process
        trim_seconds: Seconds to trim from audio start (for ads)
        skip_speaker_id: Skip speaker identification step
        skip_indexing: Skip vector indexing step
    """
    from datetime import datetime
    from pathlib import Path

    from innovation_intelligence.config import settings
    from innovation_intelligence.db.session import init_db, SessionLocal
    from innovation_intelligence.db.repositories.episode_repository import EpisodeRepository
    from innovation_intelligence.ingestion.podcasts.services import (
        PodcastSearchService,
        AudioDownloadService,
    )
    from innovation_intelligence.ingestion.podcasts.transcript_transformer import (
        transform_transcript,
    )
    from innovation_intelligence.logger import get_logger

    log = get_logger(__name__)

    # Ensure database tables exist
    init_db()
    settings.paths.ensure_dirs()

    print(f"\n{'='*60}")
    print(f"Podcast Ingestion Pipeline (Modal)")
    print(f"{'='*60}")
    print(f"Podcast: {podcast_name}")
    print(f"Max episodes: {max_episodes}")
    print(f"Trim seconds: {trim_seconds}")
    print(f"Skip speaker ID: {skip_speaker_id}")
    print(f"Skip indexing: {skip_indexing}")
    print(f"{'='*60}\n")

    # Initialize services
    search_service = PodcastSearchService()
    download_service = AudioDownloadService(output_dir=settings.paths.audio_dir)

    # Initialize Modal model for transcription
    model = Model()

    # Search for podcast and get relevant episodes
    log.info(f"[PIPELINE] Searching for podcast: {podcast_name}")
    podcast_info, episodes = search_service.discover_relevant_episodes(
        podcast_name=podcast_name,
        limit=max_episodes,
    )

    if not episodes:
        print(f"No episodes found for: {podcast_name}")
        return

    print(f"Found {len(episodes)} relevant episodes\n")

    # Process each episode
    session = SessionLocal()
    repo = EpisodeRepository(session)

    results = {"succeeded": 0, "failed": 0, "skipped": 0}

    try:
        for i, episode in enumerate(episodes, 1):
            print(f"\n[{i}/{len(episodes)}] Processing: {episode.title[:60]}...")

            try:
                # Check if already exists
                if repo.exists_by_audio_url(episode.audio_url):
                    print(f"  ⏭️  Skipping (already in database)")
                    results["skipped"] += 1
                    continue

                # 1. Download audio
                print(f"  📥 Downloading audio...")
                if trim_seconds > 0:
                    audio_path = download_service.download_and_trim(
                        episode, trim_start=trim_seconds
                    )
                else:
                    audio_path = download_service.download(episode)

                # 2. Create DB entry
                db_episode, _ = repo.get_or_create(
                    podcast_name=podcast_info.title if podcast_info else f"Feed_{episode.feed_id}",
                    episode_title=episode.title,
                    audio_url=episode.audio_url,
                    audio_path=audio_path,
                    episode_date=episode.published_at,
                )

                # 3. Transcribe using Modal GPU
                print(f"  🎙️  Transcribing (Modal GPU)...")
                audio_bytes = audio_path.read_bytes()
                transcript_json = model.transcribe_with_diarization.remote(audio_bytes)

                # 4. Save transcript
                transcript_path = settings.paths.transcripts_dir / f"episode_{db_episode.id}.json"
                transcript_path.parent.mkdir(parents=True, exist_ok=True)

                transcript_data = json.loads(transcript_json)
                transcript_data["_metadata"] = {
                    "episode_title": episode.title,
                    "episode_id": episode.episode_id,
                    "feed_id": episode.feed_id,
                    "published_at": episode.published_at.isoformat() if episode.published_at else None,
                }

                with open(transcript_path, "w", encoding="utf-8") as f:
                    json.dump(transcript_data, f, ensure_ascii=False, indent=2)

                # Update DB with transcript path
                repo.update_transcript_path(db_episode.id, str(transcript_path))
                print(f"  💾 Transcript saved: {transcript_path.name}")

                # 5. Speaker identification (optional)
                speaker_map = {}
                segments_for_indexing = transcript_data.get("segments", [])

                if not skip_speaker_id:
                    print(f"  🗣️  Identifying speakers...")
                    try:
                        transformed = transform_transcript(
                            transcript_path,
                            episode_title=episode.title,
                            run_identification=True,
                        )
                        speaker_map = transformed.speaker_map
                        # Use transformed segments with speaker names for indexing
                        segments_for_indexing = transformed.segments
                        print(f"     Speakers: {speaker_map}")
                    except Exception as e:
                        print(f"     ⚠️  Speaker ID failed: {e}")

                # 6. Vector indexing (optional)
                if not skip_indexing:
                    print(f"  📊 Indexing vectors...")
                    try:
                        from innovation_intelligence.chunking.podcast_chunker import PodcastChunker
                        from innovation_intelligence.analysis.insights.embedder import embed_texts_batch
                        from innovation_intelligence.db.repositories.vector_repository import VectorRepository

                        chunker = PodcastChunker()

                        if segments_for_indexing:
                            chunks = chunker.chunk(
                                segments=segments_for_indexing,
                                source=transcript_path.stem,
                                episode_date=episode.published_at.isoformat() if episode.published_at else None,
                            )

                            if chunks:
                                embeddings = embed_texts_batch([c.full_text for c in chunks])
                                vector_repo = VectorRepository(session)
                                vector_repo.delete_podcast_chunks_by_source(transcript_path.stem)
                                stored = vector_repo.store_podcast_chunks_batch(chunks, embeddings)
                                print(f"     Indexed {stored} chunks")
                    except Exception as e:
                        print(f"     ⚠️  Indexing failed: {e}")

                print(f"  ✅ Completed!")
                results["succeeded"] += 1

            except Exception as e:
                print(f"  ❌ Failed: {e}")
                results["failed"] += 1
                log.exception(f"Failed to process episode: {episode.title}")

    finally:
        session.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"Pipeline Complete")
    print(f"{'='*60}")
    print(f"  Succeeded: {results['succeeded']}")
    print(f"  Failed: {results['failed']}")
    print(f"  Skipped: {results['skipped']}")
    print(f"{'='*60}\n")
