# src/innovation_intelligence/api/sse_broadcaster.py
"""
Server-Sent Events (SSE) broadcaster for real-time status updates.

Manages client connections and broadcasts state changes for:
- Podcast episode processing (download → transcribe → index)
- Analysis pipeline execution
"""
import asyncio
import json
import threading
from typing import Dict, Set, Any, Optional
from datetime import datetime


class SSEBroadcaster:
    """
    Manages SSE connections and broadcasts events to connected clients.

    Thread-safe broadcaster that can be called from background tasks.
    """

    def __init__(self):
        self._clients: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock_sync = threading.Lock()

    async def connect(self) -> asyncio.Queue:
        """
        Register a new client connection.

        Returns:
            Queue for receiving events
        """
        # Store the main event loop on first connection
        if self._main_loop is None:
            self._main_loop = asyncio.get_event_loop()

        queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._clients.add(queue)
        return queue

    async def disconnect(self, queue: asyncio.Queue):
        """Remove a client connection."""
        async with self._lock:
            self._clients.discard(queue)

    async def broadcast(self, event: Dict[str, Any]):
        """
        Broadcast an event to all connected clients.

        Args:
            event: Event data to broadcast (will be JSON serialized)
        """
        # Add timestamp if not present
        if "timestamp" not in event:
            event["timestamp"] = datetime.utcnow().isoformat()

        # Remove disconnected clients
        dead_clients = set()
        async with self._lock:
            for queue in self._clients:
                try:
                    # Non-blocking put - drop events if queue is full
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Client is not consuming fast enough - disconnect them
                    dead_clients.add(queue)

            # Clean up dead clients
            self._clients -= dead_clients

    def broadcast_sync(self, event: Dict[str, Any]):
        """
        Synchronous version for calling from non-async code (background tasks).

        Schedules the broadcast in the main event loop using run_coroutine_threadsafe.
        """
        # Add timestamp if not present
        if "timestamp" not in event:
            event["timestamp"] = datetime.utcnow().isoformat()

        # If we have a main loop, schedule the broadcast there
        if self._main_loop is not None and not self._main_loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.broadcast(event), self._main_loop)
            except Exception as e:
                print(f"[SSE] Error broadcasting event: {e}")
        else:
            # Fallback: store event for next connection
            # This happens if broadcast is called before any client connects
            print(f"[SSE] Warning: No event loop available yet, event dropped: {event}")

    @property
    def client_count(self) -> int:
        """Return the number of connected clients."""
        return len(self._clients)


# Global broadcaster instance
_broadcaster = SSEBroadcaster()


def get_broadcaster() -> SSEBroadcaster:
    """Get the global SSE broadcaster instance."""
    return _broadcaster


def broadcast_episode_status(episode_id: int, status: str, **kwargs):
    """
    Broadcast episode status change.

    Args:
        episode_id: The episode ID
        status: New status (downloading, transcribing, indexing, ready, analyzing, analyzed)
        **kwargs: Additional data to include in the event
    """
    event = {
        "type": "episode_status",
        "episode_id": episode_id,
        "status": status,
        **kwargs
    }
    print(f"[SSE] Broadcasting episode {episode_id} status: {status}")
    _broadcaster.broadcast_sync(event)


def broadcast_analysis_status(task_id: str, status: str, **kwargs):
    """
    Broadcast analysis task status change.

    Args:
        task_id: The analysis task ID
        status: New status (pending, running, completed, failed)
        **kwargs: Additional data to include in the event
    """
    event = {
        "type": "analysis_status",
        "task_id": task_id,
        "status": status,
        **kwargs
    }
    print(f"[SSE] Broadcasting analysis {task_id} status: {status}")
    _broadcaster.broadcast_sync(event)


def broadcast_document_status(document_id: int, status: str, **kwargs):
    """
    Broadcast document status change.

    Args:
        document_id: The document ID
        status: New status (uploading, indexing, ready, analyzing, analyzed)
        **kwargs: Additional data to include in the event
    """
    event = {
        "type": "document_status",
        "document_id": document_id,
        "status": status,
        **kwargs
    }
    print(f"[SSE] Broadcasting document {document_id} status: {status}")
    _broadcaster.broadcast_sync(event)
