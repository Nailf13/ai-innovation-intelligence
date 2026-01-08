# src/innovation_intelligence/api/routers/podcasts.py
"""
Podcast-related API endpoints.

Handles:
- Searching for podcasts via Podcast Index API
- Listing episodes from a podcast
- Selecting episodes for processing
- Downloading audio files
"""
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import requests

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.api.schemas import (
    PodcastSearchRequest,
    PodcastSearchResponse,
    PodcastSearchResult,
    EpisodeListResponse,
    PodcastEpisodeInfo,
    EpisodeSelectRequest,
    PodcastEpisodeDB,
    SuccessResponse,
    ErrorResponse,
)
from innovation_intelligence.config import settings
from innovation_intelligence.db.models import PodcastEpisode
from innovation_intelligence.ingestion.podcasts.podcast_index_client import PodcastIndexClient
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/podcasts", tags=["podcasts"])


def get_podcast_client() -> PodcastIndexClient:
    """Get configured podcast index client."""
    return PodcastIndexClient(
        api_key=settings.podcast_index.api_key,
        api_secret=settings.podcast_index.api_secret,
    )


@router.post("/search", response_model=PodcastSearchResponse)
def search_podcasts(request: PodcastSearchRequest):
    """
    Search for podcasts by term using Podcast Index API.
    """
    try:
        client = get_podcast_client()
        
        # Get raw results from API
        url = f"{client.BASE_URL}/search/byterm"
        params = {"q": request.query}
        
        r = requests.get(url, headers=client._headers(), params=params, timeout=20)
        r.raise_for_status()
        feeds = r.json().get("feeds", [])
        
        results = []
        for feed in feeds[:20]:  # Limit to 20 results
            results.append(PodcastSearchResult(
                feed_id=feed.get("id", 0),
                title=feed.get("title", "Unknown"),
                author=feed.get("author"),
                description=feed.get("description"),
                image_url=feed.get("image"),
                url=feed.get("url"),
                episode_count=feed.get("episodeCount"),
            ))
        
        return PodcastSearchResponse(results=results, count=len(results))
    
    except requests.RequestException as e:
        log.error(f"Podcast search failed: {e}")
        raise HTTPException(status_code=503, detail=f"Podcast Index API error: {str(e)}")
    except Exception as e:
        log.error(f"Podcast search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/episodes/{feed_id}", response_model=EpisodeListResponse)
def list_episodes(feed_id: int, limit: int = 20, offset: int = 0):
    """
    List episodes from a podcast feed with offset-based pagination.

    Args:
        feed_id: The podcast feed ID
        limit: Maximum number of episodes to return (default 20)
        offset: Number of episodes to skip (for pagination)
    """
    try:
        client = get_podcast_client()
        # Fetch enough episodes to cover offset + limit + 1 (to check for more)
        total_needed = offset + limit + 1
        episodes_data = client.get_episodes(feed_id, limit=total_needed)

        # Apply offset
        episodes_data = episodes_data[offset:]

        # Check if there are more episodes
        has_more = len(episodes_data) > limit
        if has_more:
            episodes_data = episodes_data[:limit]

        episodes = []
        for ep in episodes_data:
            date_published = None
            ep_timestamp = ep.get("datePublished")
            if ep_timestamp:
                try:
                    date_published = datetime.fromtimestamp(ep_timestamp)
                except (ValueError, TypeError):
                    pass

            episodes.append(PodcastEpisodeInfo(
                id=ep.get("id", 0),
                title=ep.get("title", "Unknown"),
                description=ep.get("description"),
                date_published=date_published,
                duration=ep.get("duration"),
                audio_url=ep.get("enclosureUrl", ""),
                image_url=ep.get("image"),
            ))

        return EpisodeListResponse(
            feed_id=feed_id,
            episodes=episodes,
            count=len(episodes),
            has_more=has_more,
            next_offset=offset + len(episodes) if has_more else None,
        )

    except Exception as e:
        log.error(f"Episode list error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/select", response_model=List[PodcastEpisodeDB])
def select_episodes(
    request: EpisodeSelectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Select episodes for processing and optionally start download.
    
    Creates PodcastEpisode records in the database and
    triggers background download of audio files.
    """
    try:
        client = get_podcast_client()
        episodes_data = client.get_episodes(request.feed_id, limit=100)
        
        # Filter to selected episodes
        selected = {ep["id"]: ep for ep in episodes_data if ep["id"] in request.episode_ids}
        
        if not selected:
            raise HTTPException(status_code=404, detail="No matching episodes found")
        
        created_episodes = []
        
        for ep_id in request.episode_ids:
            if ep_id not in selected:
                continue
            
            ep_data = selected[ep_id]
            
            # Check if already exists
            existing = db.query(PodcastEpisode).filter(
                PodcastEpisode.audio_url == ep_data.get("enclosureUrl")
            ).first()
            
            if existing:
                created_episodes.append(existing)
                continue
            
            # Parse date
            episode_date = None
            if ep_data.get("datePublished"):
                try:
                    episode_date = datetime.fromtimestamp(ep_data["datePublished"])
                except (ValueError, TypeError):
                    pass
            
            # Create episode record
            episode = PodcastEpisode(
                podcast_name=request.podcast_name,
                episode_title=ep_data.get("title", f"Episode {ep_id}"),
                audio_url=ep_data.get("enclosureUrl", ""),
                audio_path="",  # Will be set after download
                episode_date=episode_date,
            )
            db.add(episode)
            db.flush()
            
            created_episodes.append(episode)
            
            # Queue download in background
            background_tasks.add_task(
                download_episode_audio,
                episode.id,
                ep_data.get("enclosureUrl", ""),
            )
        
        db.commit()
        
        # Refresh to get final state
        for ep in created_episodes:
            db.refresh(ep)
        
        return [PodcastEpisodeDB.model_validate(ep) for ep in created_episodes]
    
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Episode selection error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def download_episode_audio(episode_id: int, audio_url: str):
    """
    Background task to download episode audio.
    """
    from innovation_intelligence.db.session import SessionLocal
    
    db = SessionLocal()
    try:
        episode = db.query(PodcastEpisode).get(episode_id)
        if not episode:
            log.error(f"Episode {episode_id} not found for download")
            return
        
        # Create audio directory
        audio_dir = settings.paths.audio_dir
        audio_dir.mkdir(parents=True, exist_ok=True)
        
        # Download file
        filename = f"{episode_id}_{episode.episode_title[:50].replace(' ', '_')}.mp3"
        filename = "".join(c for c in filename if c.isalnum() or c in "._-")
        audio_path = audio_dir / filename
        
        log.info(f"Downloading audio for episode {episode_id}: {audio_url}")
        
        response = requests.get(audio_url, stream=True, timeout=300)
        response.raise_for_status()
        
        with open(audio_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Update episode record
        episode.audio_path = str(audio_path)
        db.commit()
        
        log.info(f"Downloaded audio for episode {episode_id}: {audio_path}")
    
    except Exception as e:
        log.error(f"Failed to download episode {episode_id}: {e}")
    finally:
        db.close()


@router.get("/", response_model=List[PodcastEpisodeDB])
def list_podcast_episodes(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    List all podcast episodes in the database.
    """
    episodes = db.query(PodcastEpisode).offset(skip).limit(limit).all()
    return [PodcastEpisodeDB.model_validate(ep) for ep in episodes]


@router.get("/{episode_id}", response_model=PodcastEpisodeDB)
def get_podcast_episode(episode_id: int, db: Session = Depends(get_db)):
    """
    Get a specific podcast episode by ID.
    """
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    return PodcastEpisodeDB.model_validate(episode)


@router.delete("/{episode_id}", response_model=SuccessResponse)
def delete_podcast_episode(episode_id: int, db: Session = Depends(get_db)):
    """
    Delete a podcast episode.
    """
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    db.delete(episode)
    db.commit()

    return SuccessResponse(message=f"Episode {episode_id} deleted")


@router.post("/download/{episode_id}", response_model=SuccessResponse)
def trigger_download(
    episode_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Trigger audio download for an episode that hasn't been downloaded yet.
    """
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    if episode.audio_path:
        return SuccessResponse(message="Audio already downloaded")

    if not episode.audio_url:
        raise HTTPException(status_code=400, detail="No audio URL available for this episode")

    # Queue download in background
    background_tasks.add_task(
        download_episode_audio,
        episode.id,
        episode.audio_url,
    )

    return SuccessResponse(message=f"Download started for episode {episode_id}")
