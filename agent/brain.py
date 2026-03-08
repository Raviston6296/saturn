"""
Brain — Claude API wrapper with extended thinking for hard problems.
"""

from __future__ import annotations

from typing import Any

import anthropic

from config import settings
from agent.prompts import SYSTEM_PROMPT, HARD_PROBLEM_ADDON


class AgentBrain:
    """Claude Opus/Sonnet brain with extended thinking for hard problems."""

    def __init__(self, tools: list[dict]):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.model_name
        self.tools = tools
        self.messages: list[dict] = []
        self.thinking_budget = settings.thinking_budget_tokens

    def think(self, user_message: str, hard_problem: bool = False) -> Any:
        """
        Send a message to Claude and get a response.
        hard_problem=True enables extended thinking (deeper reasoning).
        """
        if user_message:
            self.messages.append({
                "role": "user",
                "content": user_message,
            })

        system_prompt = SYSTEM_PROMPT
        if hard_problem:
            system_prompt += "\n\n" + HARD_PROBLEM_ADDON

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system_prompt,
            "tools": self.tools,
            "messages": self.messages,
        }

        if hard_problem:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget,
            }

        response = self.client.messages.create(**kwargs)

        # Store assistant response in history
        self.messages.append({
            "role": "assistant",
            "content": response.content,
        })

        return response

    def classify_difficulty(self, task: str) -> bool:
        """Returns True if the task likely needs extended thinking."""
        hard_signals = [
            "architecture", "design", "refactor", "migrate",
            "debug", "mysterious", "intermittent", "race condition",
            "performance", "security", "scale", "why", "figure out",
            "broken", "fails randomly", "production issue", "crash",
            "memory leak", "deadlock", "complex",
        ]
        task_lower = task.lower()
        return any(signal in task_lower for signal in hard_signals)

    def extract_tool_calls(self, response: Any) -> list:
        """Extract tool_use blocks from a Claude response."""
        return [
            block for block in response.content
            if block.type == "tool_use"
        ]

    def extract_text(self, response: Any) -> str:
        """Extract text blocks from a Claude response."""
        texts = []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
        return " ".join(texts)

    def inject_tool_results(self, tool_results: list[dict]):
        """Feed tool execution results back into the conversation."""
        self.messages.append({
            "role": "user",
            "content": tool_results,
        })

