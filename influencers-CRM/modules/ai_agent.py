"""LLM-powered extraction and reply drafting for influencer outreach."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini"

EXTRACT_SYSTEM_PROMPT = """\
You are a data extraction assistant for an Amazon influencer CRM system.
Given a messy Amazon Seller Central message thread, extract influencer contact details.

Return ONLY a valid JSON object with exactly these keys:
- name (string): influencer display name or nickname; use empty string if unknown
- email (string): email address; use empty string if not found
- social_links (object): platform-to-URL mapping, e.g. {"youtube": "...", "tiktok": "..."}; use {} if none
- shipping_address (string): full mailing address; use empty string if not found
- phone (string): phone number; use empty string if not found

Do not include markdown, explanations, or extra keys."""

DRAFT_SYSTEM_PROMPT = """\
You are a senior Amazon Influencer Marketing Specialist.
Draft a professional English email reply based on the provided context.

CRITICAL RULES:
1. READ the Chat History and Extracted Influencer Info carefully. DO NOT ask for info we already have.
2. You MUST return ONLY a valid JSON object with exactly two keys:
   - "english_draft": The drafted email in English.
   - "chinese_translation": A direct, accurate, and unembellished Chinese translation of the drafted email.
Do not output any markdown code blocks, just the JSON object.
"""


class InfluencerAIAgent:
    """Central AI handler for influencer message parsing and reply drafting."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("API key is required to initialize InfluencerAIAgent.")

        client_kwargs: dict[str, Any] = {"api_key": api_key.strip()}
        if base_url and base_url.strip():
            client_kwargs["base_url"] = base_url.strip().rstrip("/")

        self.model = model
        self.client = OpenAI(**client_kwargs)

    def extract_influencer_info(self, text: str) -> dict[str, Any]:
        """
        Parse a raw Amazon message thread and return structured influencer data.

        Returns a dict with keys: name, email, social_links, shipping_address, phone.
        """
        if not text or not text.strip():
            raise ValueError("Message text cannot be empty.")

        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Extract influencer info from this message:\n\n{text.strip()}",
                },
            ],
            temperature=0.1,
        )

        raw_content = response.choices[0].message.content or "{}"
        parsed = self._parse_json_response(raw_content)
        return self._normalize_extracted_info(parsed)

    def draft_reply(
        self,
        product_info: dict[str, Any],
        chat_history: str,
        extracted_info: dict[str, Any],
        custom_prompt: str = "",
    ) -> dict[str, str]:
        """
        Draft a bilingual reply using product info, chat history, and extracted info.

        Returns a dict with keys: english_draft, chinese_translation.
        """
        if not chat_history or not chat_history.strip():
            raise ValueError("Chat history cannot be empty.")

        user_content = f"""
--- PRODUCT INFO ---
{json.dumps(product_info, ensure_ascii=False, indent=2)}

--- EXTRACTED INFLUENCER INFO (DO NOT ASK FOR THESE AGAIN) ---
{json.dumps(extracted_info, ensure_ascii=False, indent=2)}

--- CHAT HISTORY ---
{chat_history}
"""
        if custom_prompt:
            user_content += f"\n--- ADDITIONAL INSTRUCTIONS ---\n{custom_prompt}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.7,
            )
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("The model returned an empty reply.")
            parsed = self._parse_json_response(content.strip())
            return self._normalize_draft_response(parsed)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Failed to generate draft: {str(exc)}") from exc

    @staticmethod
    def _normalize_draft_response(data: dict[str, Any]) -> dict[str, str]:
        """Validate and normalize bilingual draft fields from model output."""
        english_draft = str(data.get("english_draft", "")).strip()
        chinese_translation = str(data.get("chinese_translation", "")).strip()
        if not english_draft:
            raise ValueError(
                "Failed to parse draft JSON: missing or empty 'english_draft' field."
            )
        return {
            "english_draft": english_draft,
            "chinese_translation": chinese_translation,
        }

    @staticmethod
    def _parse_json_response(raw_content: str) -> dict[str, Any]:
        """Parse JSON from model output, with a lightweight fallback extractor."""
        try:
            data = json.loads(raw_content)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if match:
            data = json.loads(match.group())
            if isinstance(data, dict):
                return data

        raise ValueError("Failed to parse JSON from model response.")

    @staticmethod
    def _normalize_extracted_info(data: dict[str, Any]) -> dict[str, Any]:
        """Ensure extracted fields follow the expected schema and types."""
        social_links = data.get("social_links", {})
        if isinstance(social_links, str):
            try:
                social_links = json.loads(social_links)
            except json.JSONDecodeError:
                social_links = {}
        if not isinstance(social_links, dict):
            social_links = {}

        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip()
        shipping_address = str(data.get("shipping_address", "")).strip()
        phone = str(data.get("phone", "")).strip()

        if not name and not email:
            raise ValueError(
                "Could not extract a usable influencer name or email from the message."
            )

        return {
            "name": name or "Unknown Influencer",
            "email": email or None,
            "social_links": json.dumps(social_links, ensure_ascii=False),
            "shipping_address": shipping_address or None,
            "phone": phone or None,
        }
