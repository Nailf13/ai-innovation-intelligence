# src/transcription/services/podcast_index_client.py
import time
import hashlib
import requests
from typing import Dict, Any, List


class PodcastIndexClient:
    BASE_URL = "https://api.podcastindex.org/api/1.0"

    def __init__(self, api_key: str, api_secret: str, user_agent: str = "transcription-app"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.user_agent = user_agent

    def _headers(self) -> Dict[str, str]:
        ts = int(time.time())
        to_sign = f"{self.api_key}{self.api_secret}{ts}"
        sha1 = hashlib.sha1(to_sign.encode()).hexdigest()

        return {
            "User-Agent": self.user_agent,
            "X-Auth-Date": str(ts),
            "X-Auth-Key": self.api_key,
            "Authorization": sha1,
        }

    def search_podcast(self, term: str) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/search/byterm"
        params = {"q": term}

        r = requests.get(url, headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        feeds = r.json().get("feeds", [])
        if not feeds:
            raise RuntimeError(f"No podcast found for: {term}")

        return feeds[0]  # best match

    def get_episodes(self, feed_id: int, limit: int = 5, fulltext: bool = False) -> List[Dict[str, Any]]:
        """
        Get episodes from a podcast feed.

        Args:
            feed_id: The podcast feed ID
            limit: Maximum number of episodes to return (API max is 1000)
            fulltext: Include full description text
        """
        url = f"{self.BASE_URL}/episodes/byfeedid"
        params = {"id": feed_id, "max": min(limit, 1000)}
        if fulltext:
            params["fulltext"] = "true"

        r = requests.get(url, headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("items", [])