"""
Brain — LLM wrapper supporting Ollama (local) and Anthropic (cloud).
"""

from __future__ import annotations

import json
from typing import Any
from dataclasses import dataclass, field

from config import settings
from agent.prompts import SYSTEM_PROMPT, HARD_PROBLEM_ADDON


# ── Lightweight response wrappers so the agent loop doesn't care which provider ──

@dataclass
class ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class UnifiedResponse:
    """Provider-agnostic response object."""
    content: list = field(default_factory=list)


class AgentBrain:
    """LLM brain that works with Ollama (local) or Anthropic (cloud)."""

    def __init__(self, tools: list[dict]):
        self.provider = settings.llm_provider.lower()
        self.tools = tools
        self.messages: list[dict] = []
        self.thinking_budget = settings.thinking_budget_tokens

        if self.provider == "ollama":
            self._init_ollama()
        else:
            self._init_anthropic()

    # ── Provider init ──

    def _init_ollama(self):
        from openai import OpenAI
        self.client = OpenAI(
            base_url=f"{settings.ollama_base_url}/v1",
            api_key="ollama",  # Ollama doesn't need a real key
        )
        self.model = settings.ollama_model
        # Convert Anthropic tool schemas to OpenAI function-calling format
        self.openai_tools = self._convert_tools_to_openai(self.tools)
        print(f"🧠 Brain: Ollama ({self.model}) @ {settings.ollama_base_url}")

    def _init_anthropic(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.model_name
        print(f"🧠 Brain: Anthropic ({self.model})")

    # ── Main interface ──

    def think(self, user_message: str, hard_problem: bool = False) -> UnifiedResponse:
        """Send a message and get a response. Works with any provider."""
        if user_message:
            self.messages.append({"role": "user", "content": user_message})

        if self.provider == "ollama":
            return self._think_ollama(hard_problem)
        else:
            return self._think_anthropic(hard_problem)

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

    def extract_tool_calls(self, response: UnifiedResponse) -> list:
        """Extract tool_use blocks from a response."""
        return [block for block in response.content if block.type == "tool_use"]

    def extract_text(self, response: UnifiedResponse) -> str:
        """Extract text blocks from a response."""
        texts = []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
        return " ".join(texts)

    def inject_tool_results(self, tool_results: list[dict]):
        """Feed tool execution results back into the conversation."""
        if self.provider == "ollama":
            self._inject_ollama(tool_results)
        else:
            self.messages.append({"role": "user", "content": tool_results})

    # ── Ollama (OpenAI-compatible) ──

    def _think_ollama(self, hard_problem: bool = False) -> UnifiedResponse:
        system_prompt = SYSTEM_PROMPT
        if hard_problem:
            system_prompt += "\n\n" + HARD_PROBLEM_ADDON

        # Build messages with system prompt
        openai_messages = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(self.messages)

        # Build kwargs — only include tools if we have them
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "max_tokens": 8192,
            "temperature": 0,
        }
        if self.openai_tools:
            kwargs["tools"] = self.openai_tools
            kwargs["tool_choice"] = "auto"

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            print(f"❌ Ollama API error: {e}")
            return UnifiedResponse(content=[TextBlock(text=f"LLM Error: {e}")])

        choice = response.choices[0]
        content_blocks = []

        # Extract text
        if choice.message.content:
            content_blocks.append(TextBlock(text=choice.message.content))

        # Extract tool calls
        if choice.message.tool_calls:
            # Store assistant message with tool_calls for conversation history
            assistant_msg = {
                "role": "assistant",
                "content": choice.message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ],
            }
            self.messages.append(assistant_msg)

            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                content_blocks.append(ToolUseBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=args,
                ))
        else:
            # No tool calls — just text
            self.messages.append({
                "role": "assistant",
                "content": choice.message.content or "",
            })

        return UnifiedResponse(content=content_blocks)

    def _inject_ollama(self, tool_results: list[dict]):
        """Inject tool results back in OpenAI function-calling format."""
        for result in tool_results:
            tool_call_id = result.get("tool_use_id", "")
            content = result.get("content", "")
            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            })

    def _convert_tools_to_openai(self, anthropic_tools: list[dict]) -> list[dict]:
        """Convert Anthropic tool schemas to OpenAI function-calling format."""
        openai_tools = []
        for tool in anthropic_tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return openai_tools

    # ── Anthropic ──

    def _think_anthropic(self, hard_problem: bool = False) -> UnifiedResponse:
        import anthropic as _anthropic

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

        # Wrap in unified response
        content_blocks = []
        for block in response.content:
            if block.type == "tool_use":
                content_blocks.append(ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))
            elif block.type == "text":
                content_blocks.append(TextBlock(text=block.text))

        return UnifiedResponse(content=content_blocks)
