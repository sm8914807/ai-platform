"""Context engineering — token budgets, summarization, relevance filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) + 4 for m in messages)


@dataclass
class ContextBudget:
    max_tokens: int = 8000
    reserve_for_response: int = 1000
    system_reserve: int = 500

    @property
    def available(self) -> int:
        return max(0, self.max_tokens - self.reserve_for_response - self.system_reserve)


@dataclass
class ContextEngineeringResult:
    messages: list[dict[str, Any]]
    original_tokens: int
    final_tokens: int
    dropped: int = 0
    summarized: bool = False
    filtered: bool = False
    notes: list[str] = field(default_factory=list)


class ContextEngineer:
    """Prepares model context under token budgets with relevance filtering."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()

    def prepare(
        self,
        messages: list[dict[str, Any]],
        query: str | None = None,
        system_prompt: str | None = None,
    ) -> ContextEngineeringResult:
        original = estimate_messages_tokens(messages)
        notes: list[str] = []
        working = list(messages)
        filtered = False
        summarized = False
        dropped = 0

        # Separate system vs conversation
        system_msgs = [m for m in working if m.get("role") == "system"]
        conv = [m for m in working if m.get("role") != "system"]

        if system_prompt:
            system_msgs = [{"role": "system", "content": system_prompt}]

        # Relevance filter on conversation history when query provided
        if query and len(conv) > 4:
            filtered_conv = self._filter_relevant(conv, query)
            if len(filtered_conv) < len(conv):
                dropped += len(conv) - len(filtered_conv)
                notes.append(f"relevance_filter dropped {dropped} messages")
                filtered = True
                conv = filtered_conv

        # Enforce budget — keep newest messages, summarize older if needed
        available = self.budget.available
        system_tokens = estimate_messages_tokens(system_msgs)
        available_for_conv = max(0, available - system_tokens)

        while estimate_messages_tokens(conv) > available_for_conv and len(conv) > 2:
            # Summarize oldest half into one message
            if len(conv) >= 4:
                mid = len(conv) // 2
                older, newer = conv[:mid], conv[mid:]
                summary = self._summarize(older)
                conv = [{"role": "system", "content": f"[Conversation summary] {summary}"}] + newer
                summarized = True
                notes.append(f"summarized {mid} older messages")
            else:
                conv = conv[1:]
                dropped += 1
                notes.append("dropped oldest message for budget")

        # Final hard truncate of last user message if still over
        final = system_msgs + conv
        while estimate_messages_tokens(final) > self.budget.max_tokens and final:
            last = final[-1]
            content = str(last.get("content", ""))
            if len(content) > 200:
                last = {**last, "content": content[: len(content) // 2] + "…[truncated]"}
                final[-1] = last
                notes.append("truncated last message")
            else:
                final = final[:-1]
                dropped += 1

        return ContextEngineeringResult(
            messages=final,
            original_tokens=original,
            final_tokens=estimate_messages_tokens(final),
            dropped=dropped,
            summarized=summarized,
            filtered=filtered,
            notes=notes,
        )

    def _filter_relevant(
        self, messages: list[dict[str, Any]], query: str
    ) -> list[dict[str, Any]]:
        """Keep messages that share keywords with the query, plus always keep last 2."""
        keywords = set(re.findall(r"[a-zA-Z0-9_]{3,}", query.lower()))
        if not keywords:
            return messages[-6:]

        scored: list[tuple[float, int, dict[str, Any]]] = []
        for i, m in enumerate(messages):
            text = str(m.get("content", "")).lower()
            words = set(re.findall(r"[a-zA-Z0-9_]{3,}", text))
            overlap = len(keywords.intersection(words))
            # Recency boost
            score = overlap + (0.1 * i / max(len(messages), 1))
            scored.append((score, i, m))

        # Always keep last 2
        keep_idx = {len(messages) - 1, len(messages) - 2}
        keep_idx |= {i for score, i, _ in scored if score >= 1.0}

        # Cap to top messages by index order
        ordered = sorted(i for i in keep_idx if 0 <= i < len(messages))
        if len(ordered) < 2:
            return messages[-4:]
        return [messages[i] for i in ordered]

    def _summarize(self, messages: list[dict[str, Any]]) -> str:
        """Extractive summary — first sentence of each message, capped."""
        parts: list[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = str(m.get("content", "")).strip()
            sentence = re.split(r"[.!\n]", content)[0].strip()
            if sentence:
                parts.append(f"{role}: {sentence[:120]}")
        summary = " | ".join(parts)
        return summary[:800] if summary else "(empty)"
