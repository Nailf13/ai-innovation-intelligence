# src/innovation_intelligence/ingestion/podcasts/services/search_service.py
"""
Podcast search service using Podcast Index API.

Provides:
- Podcast search by term
- Episode listing and filtering
- Relevance scoring for health-related content
"""
from __future__ import annotations

import hashlib
import time
from typing import List, Optional

import requests

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.ingestion.podcasts.models import (
    PodcastInfo,
    EpisodeInfo,
)

log = get_logger(__name__)


# Default health/nutrition keywords for relevance scoring
DEFAULT_RELEVANCE_KEYWORDS = [
    "diet", "nutrition", "health", "mental health", "longevity",
    "fitness", "well-being", "biohacking", "sleep", "wellness",
    "gut", "microbiome", "metabolism", "fasting", "supplements",
    "stress", "anxiety", "depression", "brain", "cognitive",
]


class PodcastSearchService:
    """
    Service for searching and discovering podcasts via Podcast Index API.

    Usage:
        service = PodcastSearchService()
        podcasts = service.search_podcasts("health nutrition")
        episodes = service.get_episodes(podcast.feed_id, limit=20)
        relevant = service.filter_by_relevance(episodes, min_score=2)
    """

    BASE_URL = "https://api.podcastindex.org/api/1.0"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        user_agent: str = "innovation-intelligence/1.0",
        timeout: int = 30,
    ):
        self.api_key = api_key or settings.podcast_index.api_key
        self.api_secret = api_secret or settings.podcast_index.api_secret
        self.user_agent = user_agent
        self.timeout = timeout

        if not self.api_key or not self.api_secret:
            raise ValueError(
                "Podcast Index API credentials required. "
                "Set PODCAST_API_KEY and PODCAST_API_SECRET in .env"
            )

    def _get_auth_headers(self) -> dict:
        """Generate authentication headers for Podcast Index API."""
        ts = int(time.time())
        auth_string = f"{self.api_key}{self.api_secret}{ts}"
        auth_hash = hashlib.sha1(auth_string.encode()).hexdigest()

        return {
            "User-Agent": self.user_agent,
            "X-Auth-Date": str(ts),
            "X-Auth-Key": self.api_key,
            "Authorization": auth_hash,
        }

    def _request(self, endpoint: str, params: dict) -> dict:
        """Make authenticated request to Podcast Index API."""
        url = f"{self.BASE_URL}/{endpoint}"

        try:
            response = requests.get(
                url,
                headers=self._get_auth_headers(),
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            log.error(f"[SEARCH] Request timeout: {endpoint}")
            raise
        except requests.exceptions.RequestException as e:
            log.error(f"[SEARCH] API request failed: {e}")
            raise

    # -------------------------------------------------------------------------
    # Podcast Search
    # -------------------------------------------------------------------------
    def search_podcasts(
        self,
        query: str,
        limit: int = 10,
    ) -> List[PodcastInfo]:
        """
        Search for podcasts by term.

        Args:
            query: Search term (podcast name, topic, etc.)
            limit: Maximum results to return

        Returns:
            List of matching podcasts
        """
        log.info(f"[SEARCH] Searching podcasts: {query}")

        data = self._request("search/byterm", {"q": query, "max": limit})
        feeds = data.get("feeds", [])

        podcasts = [PodcastInfo.from_api_response(feed) for feed in feeds]
        log.info(f"[SEARCH] Found {len(podcasts)} podcasts for '{query}'")

        return podcasts

    def search_podcast_by_name(self, name: str) -> Optional[PodcastInfo]:
        """
        Search for a specific podcast by name (returns best match).

        Args:
            name: Podcast name to search

        Returns:
            Best matching podcast or None
        """
        podcasts = self.search_podcasts(name, limit=1)
        if podcasts:
            return podcasts[0]
        return None

    def get_podcast_by_feed_id(self, feed_id: int) -> Optional[PodcastInfo]:
        """
        Get podcast info by feed ID.

        Args:
            feed_id: Podcast Index feed ID

        Returns:
            Podcast info or None
        """
        try:
            data = self._request("podcasts/byfeedid", {"id": feed_id})
            feed = data.get("feed")
            if feed:
                return PodcastInfo.from_api_response(feed)
        except Exception as e:
            log.error(f"[SEARCH] Failed to get podcast {feed_id}: {e}")

        return None

    # -------------------------------------------------------------------------
    # Episode Listing
    # -------------------------------------------------------------------------
    def get_episodes(
        self,
        feed_id: int,
        limit: int = 50,
    ) -> List[EpisodeInfo]:
        """
        Get episodes for a podcast.

        Args:
            feed_id: Podcast Index feed ID
            limit: Maximum episodes to return

        Returns:
            List of episodes (newest first)
        """
        log.info(f"[SEARCH] Fetching episodes for feed {feed_id}")

        data = self._request("episodes/byfeedid", {"id": feed_id, "max": limit})
        items = data.get("items", [])

        episodes = [EpisodeInfo.from_api_response(item, feed_id) for item in items]
        log.info(f"[SEARCH] Found {len(episodes)} episodes")

        return episodes

    def get_recent_episodes(
        self,
        feed_id: int,
        since_timestamp: Optional[int] = None,
        limit: int = 20,
    ) -> List[EpisodeInfo]:
        """
        Get recent episodes, optionally filtered by date.

        Args:
            feed_id: Podcast Index feed ID
            since_timestamp: Unix timestamp - only return episodes after this
            limit: Maximum episodes

        Returns:
            List of recent episodes
        """
        episodes = self.get_episodes(feed_id, limit=limit)

        if since_timestamp:
            episodes = [
                e for e in episodes
                if e.published_at and e.published_at.timestamp() > since_timestamp
            ]

        return episodes

    # -------------------------------------------------------------------------
    # Relevance Filtering
    # -------------------------------------------------------------------------
    def score_episode_relevance(
        self,
        episode: EpisodeInfo,
        keywords: Optional[List[str]] = None,
    ) -> float:
        """
        Score an episode's relevance based on keywords in title/description.

        Args:
            episode: Episode to score
            keywords: Keywords to match (defaults to health keywords)

        Returns:
            Relevance score (higher = more relevant)
        """
        keywords = keywords or DEFAULT_RELEVANCE_KEYWORDS

        text = f"{episode.title} {episode.description or ''}".lower()

        score = 0.0
        for keyword in keywords:
            if keyword.lower() in text:
                # Title matches worth more
                if keyword.lower() in episode.title.lower():
                    score += 2.0
                else:
                    score += 1.0

        return score

    def filter_by_relevance(
        self,
        episodes: List[EpisodeInfo],
        keywords: Optional[List[str]] = None,
        min_score: float = 0.0,
        top_k: Optional[int] = None,
    ) -> List[EpisodeInfo]:
        """
        Filter and rank episodes by relevance.

        Args:
            episodes: Episodes to filter
            keywords: Keywords for scoring
            min_score: Minimum relevance score
            top_k: If set, return only top K most relevant

        Returns:
            Filtered and ranked episodes
        """
        # Score all episodes
        for episode in episodes:
            episode.relevance_score = self.score_episode_relevance(episode, keywords)

        # Filter by minimum score
        filtered = [e for e in episodes if e.relevance_score >= min_score]

        # Sort by relevance (descending)
        filtered.sort(key=lambda e: e.relevance_score, reverse=True)

        # Limit to top K if specified
        if top_k:
            filtered = filtered[:top_k]

        log.info(
            f"[SEARCH] Filtered {len(episodes)} episodes to {len(filtered)} "
            f"(min_score={min_score})"
        )

        return filtered

    # -------------------------------------------------------------------------
    # Convenience Methods
    # -------------------------------------------------------------------------
    def discover_relevant_episodes(
        self,
        podcast_name: str,
        keywords: Optional[List[str]] = None,
        limit: int = 10,
        min_relevance: float = 1.0,
    ) -> tuple[Optional[PodcastInfo], List[EpisodeInfo]]:
        """
        Discover relevant episodes from a podcast by name.

        Combines search + episode listing + relevance filtering.

        Args:
            podcast_name: Podcast name to search
            keywords: Relevance keywords
            limit: Maximum episodes to return
            min_relevance: Minimum relevance score

        Returns:
            Tuple of (podcast_info, relevant_episodes)
        """
        # Find the podcast
        podcast = self.search_podcast_by_name(podcast_name)
        if not podcast:
            log.warning(f"[SEARCH] Podcast not found: {podcast_name}")
            return None, []

        # Get episodes
        episodes = self.get_episodes(podcast.feed_id, limit=50)

        # Filter by relevance
        relevant = self.filter_by_relevance(
            episodes,
            keywords=keywords,
            min_score=min_relevance,
            top_k=limit,
        )

        return podcast, relevant
