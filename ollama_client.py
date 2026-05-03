"""
ollama_client.py — Async Ollama API client with streaming + model routing.
"""

import httpx
import json
import asyncio
from config import OLLAMA_BASE_URL, MODELS


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE_URL):
        self.base_url = base_url

    async def chat(
        self,
        model: str,
        messages: list[dict],
        stream: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Send a chat request to Ollama. Returns full response text."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            if stream:
                full = ""
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=payload
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            chunk = json.loads(line)
                            full += chunk.get("message", {}).get("content", "")
                            if chunk.get("done"):
                                break
                return full
            else:
                resp = await client.post(
                    f"{self.base_url}/api/chat", json=payload
                )
                resp.raise_for_status()
                data = resp.json()
                return data["message"]["content"]

    async def generate(self, model: str, prompt: str, **kwargs) -> str:
        """Raw generate endpoint."""
        payload = {"model": model, "prompt": prompt, "stream": False, **kwargs}
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/generate", json=payload
            )
            resp.raise_for_status()
            return resp.json()["response"]

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
            return [m["name"] for m in resp.json().get("models", [])]

    async def is_running(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    # ─── Convenience role-based methods ──────────────────────────────────────

    async def score(self, prompt: str) -> str:
        """Use phi4-mini — fast classification/scoring."""
        return await self.generate(MODELS["scorer"], prompt, options={"temperature": 0.1})

    async def write(self, messages: list[dict]) -> str:
        """Use llama3 — creative writing, pitch generation."""
        return await self.chat(MODELS["writer"], messages, temperature=0.8)

    async def audit(self, messages: list[dict]) -> str:
        """Use qwen3:8b — deep analytical reasoning."""
        return await self.chat(MODELS["auditor"], messages, temperature=0.3)

    async def bulk_analyze(self, messages: list[dict]) -> str:
        """Use qwen3-8b-ctx8k — long context batch jobs."""
        return await self.chat(MODELS["bulk"], messages, temperature=0.3, max_tokens=4096)

    async def analyze(self, messages: list[dict]) -> str:
        """Use deephat — niche/domain analysis."""
        return await self.chat(MODELS["analyst"], messages, temperature=0.4)


# Global singleton
ollama = OllamaClient()
