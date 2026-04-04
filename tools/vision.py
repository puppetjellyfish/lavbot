import base64
import json
import os

import requests

from config import get_local_provider_kind, get_ollama_base_url
from user_db import get_setting

DEFAULT_VISION_MODEL = "qwen3.5"


def _get_vision_model() -> str:
    return get_setting("VISION_MODEL") or os.getenv("VISION_MODEL") or DEFAULT_VISION_MODEL


def _extract_local_text_response(data: dict) -> str:
    if not isinstance(data, dict):
        return ""

    raw = data.get("response")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    message = data.get("message")
    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()

    choices = data.get("choices") or []
    if choices:
        first_choice = choices[0] or {}
        message = first_choice.get("message") or {}
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join(part for part in text_parts if part).strip()

    return ""


def _post_vision_request(prompt: str, encoded_image: str) -> str:
    base_url = get_ollama_base_url()
    provider_kind = get_local_provider_kind()
    vision_model = _get_vision_model()

    if provider_kind == "ollama":
        response = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": vision_model,
                "prompt": prompt,
                "images": [encoded_image],
                "stream": False,
            },
            timeout=180,
        )
    else:
        response = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{encoded_image}"},
                            },
                        ],
                    }
                ],
                "stream": False,
            },
            timeout=180,
        )

    response.raise_for_status()
    return _extract_local_text_response(response.json())


def ask_ollama_vision(image_path: str) -> dict:
    """
    Sends an image to a local vision model and returns:
    - description: short, gentle description in Lavender's voice
    - tags: visual keywords
    - emotion: detected emotional content
    - themes: visual themes (color palette, mood, style)
    - detailed_description: longer, more poetic description
    Always returns valid JSON (with fallbacks).
    """

    prompt = """
You are Lavender, a cute lamb AI companion analyzing an image in your own gentle, kind voice.

You MUST respond ONLY with valid JSON.
No explanations, no extra text, no markdown.

The JSON format MUST be exactly:

{
  "description": "a short, 1-2 sentence gentle description from Lavender's perspective",
  "detailed_description": "a poetic 2-3 sentence description in Lavender's voice, sharing what emotions the image evokes",
  "tags": ["tag1", "tag2", "tag3"],
  "emotion": "detected_emotion",
  "emotional_intensity": 0.5,
  "emotional_content": "description of emotional elements in the image",
  "visual_themes": ["theme1", "theme2"],
  "color_palette": ["color1", "color2"],
  "subject": "main subject of image"
}

Rules:
- "description": Lavender's soft take on what she sees (1-2 sentences)
- "detailed_description": More poetic, sharing emotional reactions from Lavender's perspective
- "tags": Simple visual keywords (objects, activities, styles)
- "emotion": One of: "happy", "warm", "peaceful", "melancholic", "playful", "neutral", "inspired", "concerned"
- "emotional_intensity": 0.0 (low) to 1.0 (high)
- "emotional_content": What in the image conveys emotion?
- "visual_themes": Abstract themes like "cozy", "natural", "vibrant", "serene", "chaotic"
- "color_palette": Main colors in the image
- "subject": What is the main focus?

If you cannot produce valid JSON, output exactly:
{"description": "baa… I had trouble with that picture", "tags": [], "emotion": "neutral", "emotional_intensity": 0, "visual_themes": []}
"""

    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        encoded = base64.b64encode(image_bytes).decode("utf-8")

        raw = _post_vision_request(prompt, encoded)
        return json.loads(raw)

    except Exception:
        return {
            "description": "baa… I had trouble looking at that picture…",
            "detailed_description": "I couldn't quite understand this image, sorry…",
            "tags": [],
            "emotion": "neutral",
            "emotional_intensity": 0,
            "emotional_content": "unknown",
            "visual_themes": [],
            "color_palette": [],
            "subject": "unknown"
        }


def analyze_image_emotions(image_path: str) -> dict:
    """
    Deep emotional analysis of an image.
    Returns detailed emotional breakdown and content analysis.
    """
    prompt = """
Analyze the emotional and thematic content of this image in detail.

Respond ONLY with valid JSON:

{
  "primary_emotion": "emotion",
  "secondary_emotions": ["emotion1", "emotion2"],
  "emotion_analysis": "detailed explanation of emotional elements",
  "sentiment_score": 0.5,
  "visual_composition": "description of composition and layout",
  "color_mood": "how colors affect the mood",
  "narrative_elements": ["element1", "element2"],
  "potential_memories": "what kind of memories or feelings might this evoke?"
}

Emotion options: happy, sad, peaceful, energetic, melancholic, romantic, playful, mysterious, inspiring, concerned, cozy, nostalgic, beautiful, unsettling
"""

    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        encoded = base64.b64encode(image_bytes).decode("utf-8")

        raw = _post_vision_request(prompt, encoded)
        return json.loads(raw)

    except Exception:
        return {
            "primary_emotion": "neutral",
            "secondary_emotions": [],
            "emotion_analysis": "Unable to analyze",
            "sentiment_score": 0.5
        }


def extract_visual_themes(image_path: str) -> dict:
    """
    Extract dominant visual themes and aesthetic qualities.
    """
    prompt = """
Identify the visual and aesthetic themes in this image.

Respond ONLY with valid JSON:

{
  "primary_theme": "main aesthetic theme",
  "secondary_themes": ["theme1", "theme2"],
  "aesthetic_style": "artistic/photographic style",
  "dominant_colors": ["color1", "color2", "color3"],
  "lighting": "lighting description",
  "texture_qualities": ["quality1", "quality2"],
  "mood_descriptors": ["descriptor1", "descriptor2"],
  "similarity_keywords": ["keyword1", "keyword2"]
}

Examples of themes: minimalist, cluttered, natural, urban, vintage, modern, dreamy, chaotic, serene, vibrant
"""

    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        encoded = base64.b64encode(image_bytes).decode("utf-8")

        raw = _post_vision_request(prompt, encoded)
        return json.loads(raw)

    except Exception:
        return {
            "primary_theme": "unknown",
            "secondary_themes": [],
            "dominant_colors": []
        }