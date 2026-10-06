"""Coordinate archive writes across QQ commands, WebUI and polling."""
import asyncio
workflow_lock = asyncio.Lock()
