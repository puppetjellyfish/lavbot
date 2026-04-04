import asyncio
import json
import os
import re
from datetime import datetime

import discord
import requests
from discord.ext import commands
from dotenv import load_dotenv

from config import (
	get_discord_token,
	get_local_model,
	get_local_provider_kind,
	get_news_key,
	get_ollama_base_url,
	is_allowed_user,
	who_is,
)
from data_paths import FAVORITES_PATH, IMAGES_DIR, USER_ENV_PATH, ensure_userdata_dirs
from memory import (
	add_moment,
	add_note,
	add_notes_batch,
	add_persona_memory,
	clear_moments,
	count_moments,
	create_tag,
	delete_moment_by_number,
	delete_note_by_number,
	delete_persona_memory,
	delete_persona_memory_by_text,
	delete_tag,
	delete_untagged_notes,
	get_distillation,
	list_moments,
	list_notes,
	list_notes_by_tag,
	list_persona_memories,
	list_tags,
	list_tags_with_counts,
	load_persona_memory_texts,
	normalize_tag_name,
	recent_moments,
	replace_persona_memory_by_text,
	search_notes,
	set_distillation,
)
from personality import get_custom_personality_prompt, personality_for
from security import safe_output, sanitize_input, run_pip_audit, run_bandit, run_safety_check, run_full_security_audit
from tools.vision import ask_ollama_vision
from user_db import get_persona_for_user, get_setting, set_setting

ensure_userdata_dirs()
load_dotenv(dotenv_path=str(USER_ENV_PATH))
load_dotenv()

IMAGE_FOLDER = str(IMAGES_DIR)
FAVORITES_FILE = str(FAVORITES_PATH)
DEFAULT_LOCATION = "Vancouver, BC, Canada"
MAX_IMAGE_AGE_DAYS = 30
DISTILLATION_THRESHOLD = 50
NOTE_STOPWORDS = {
	"a", "an", "the", "and", "or", "but", "if", "then", "than", "to", "for",
	"of", "in", "on", "at", "by", "with", "from", "up", "about", "into", "over",
	"after", "before", "between", "can", "could", "would", "should", "please", "check",
	"look", "find", "tell", "what", "when", "where", "who", "why", "how", "is", "are",
	"was", "were", "be", "being", "been", "do", "does", "did", "my", "me", "you",
	"your", "yours", "i", "we", "our", "ours", "notes", "note"
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

last_interaction_ts = None


def update_last_interaction():
	global last_interaction_ts
	import time

	last_interaction_ts = time.time()


def load_favorites() -> dict:
	if not os.path.exists(FAVORITES_FILE):
		return {"images": []}
	with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
		data = json.load(f)

	images = []
	for raw in data.get("images", []):
		value = str(raw or "").strip()
		if not value:
			continue
		# Keep compatibility with old favorites entries that stored relative paths
		# like "lavender_images/0001.jpg" by mapping everything to the new folder.
		if os.path.isabs(value):
			normalized = os.path.normpath(value)
		else:
			normalized = os.path.normpath(os.path.join(IMAGE_FOLDER, os.path.basename(value)))
		if normalized not in images:
			images.append(normalized)

	return {"images": images}


def save_favorites(data: dict):
	with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
		json.dump(data, f, indent=2)


def quoted_value(raw: str) -> str:
	text = (raw or "").strip()
	if text.startswith('"') and text.endswith('"') and len(text) >= 2:
		return text[1:-1].strip()
	return text


def normalize_memory_text(text: str) -> str:
	cleaned = " ".join((text or "").strip().lower().split())
	return cleaned.strip(" .!?\"'")


# Maps canonical subject names to keyword aliases found in memory text.
# Used by resolve_memory_reference to match vague references like "birthday" or "pronouns"
# against stored memories that describe that subject without using the exact same wording.
MEMORY_SUBJECT_ALIASES: dict[str, list[str]] = {
	"birthday":       ["birthday", "born", "birth date", "bday", "birth"],
	"name":           ["name", "called", "goes by", "known as", "full name"],
	"pronouns":       ["pronouns", "he/him", "she/her", "they/them", "he him", "she her", "they them"],
	"favorite food":  ["favorite food", "favourite food", "likes to eat", "loves eating"],
	"favorite color": ["favorite color", "favourite color", "colour preference", "color preference"],
	"pet":            ["pet", "dog", "cat", "animal", "kitten", "puppy"],
	"job":            ["job", "career", "profession", "occupation", "works as"],
	"location":       ["lives in", "location", "city", "town", "home", "based in"],
	"relationship":   ["relationship", "dating", "partner", "boyfriend", "girlfriend", "spouse", "married"],
	"hobby":          ["hobby", "hobbies", "likes to do", "enjoys", "pastime", "interest"],
	"age":            ["years old", "born in", "born on"],
}


def _subject_keywords(text: str) -> set[str]:
	"""Return canonical subject keys whose aliases appear in the normalized text."""
	normalized = normalize_memory_text(text)
	matched: set[str] = set()
	for canonical, aliases in MEMORY_SUBJECT_ALIASES.items():
		for alias in aliases:
			if alias in normalized:
				matched.add(canonical)
				break
	return matched


def build_personality_guidance(user_id: int) -> str:
	style = personality_for(user_id)
	if style == "affectionate":
		base_guidance = "Keep the tone affectionate, warm, and caring."
	elif style == "playful":
		base_guidance = "Keep the tone playful, light, and teasing in a kind way."
	else:
		base_guidance = "Keep the tone gentle, friendly, and balanced."

	custom_prompt = get_custom_personality_prompt(user_id)
	if not custom_prompt:
		return base_guidance

	return f"{base_guidance}\nUser custom personality prompt:\n{custom_prompt}"


async def build_notes_context(query: str, limit: int = 5) -> str:
	cleaned_query = (query or "").strip()
	if not cleaned_query:
		return "(no relevant notes found)"

	tokens = re.findall(r"[a-zA-Z0-9']+", cleaned_query.lower())
	keywords = [token for token in tokens if len(token) >= 3 and token not in NOTE_STOPWORDS]
	search_terms = list(dict.fromkeys(keywords))[:8]
	if not search_terms:
		search_terms = [cleaned_query[:80]]

	scored = {}
	for term in [cleaned_query[:120], *search_terms]:
		rows = list(await search_notes(term))
		for note_number, note_text, taken_date in rows[:20]:
			item = scored.setdefault(
				note_number,
				{"text": note_text, "date": taken_date, "score": 0},
			)
			item["score"] += 2 if term == cleaned_query[:120] else 1

	if not scored:
		return "(no relevant notes found)"

	ranked = sorted(
		scored.items(),
		key=lambda item: (item[1]["score"], item[0]),
		reverse=True,
	)[:limit]
	return "\n".join(
		f"- #{note_number} ({payload['date']}) {str(payload['text']).replace(chr(10), ' ')[:220]}"
		for note_number, payload in ranked
	)


async def build_persona_memory_context(user_id: int) -> str:
	persona = get_persona_for_user(user_id)
	if not persona:
		return "(no persona assigned)"

	memories = await load_persona_memory_texts(persona)
	if not memories:
		return "(no persona memories saved yet)"

	return "\n".join(f"- {memory_text}" for memory_text in memories)


async def build_recent_moments_context(limit: int = 5) -> str:
	rows = await recent_moments(limit)
	if not rows:
		return "(no recent moments saved)"

	lines = []
	for _, moment_text, created_at in rows:
		preview = moment_text.replace("\n", " ")[:220]
		lines.append(f"- ({created_at}) {preview}")
	return "\n".join(lines)


MEMORY_DIRECTIVE_RE = re.compile(
	r"<persona-memory\s+action=\"(?P<action>add|replace|delete)\"(?:\s+old=\"(?P<old>.*?)\")?>(?P<body>.*?)</persona-memory>",
	re.IGNORECASE | re.DOTALL,
)


def strip_memory_directives(text: str) -> str:
	return MEMORY_DIRECTIVE_RE.sub("", text).strip()


def resolve_memory_reference(memories: list[tuple[int, str, str]], reference: str) -> tuple[int, str, str] | None:
	target = normalize_memory_text(reference)
	if not target:
		return None

	exact_matches = [row for row in memories if normalize_memory_text(row[1]) == target]
	if len(exact_matches) == 1:
		return exact_matches[0]

	contains_matches = [
		row for row in memories
		if target in normalize_memory_text(row[1]) or normalize_memory_text(row[1]) in target
	]
	if len(contains_matches) == 1:
		return contains_matches[0]

	# Subject-aware fallback: find memories sharing a subject keyword with the reference
	# (e.g. "birthday" matches "her birthday is march 5th" even with no substring overlap).
	ref_subjects = _subject_keywords(reference)
	if ref_subjects:
		subject_matches = [row for row in memories if ref_subjects & _subject_keywords(row[1])]
		if len(subject_matches) == 1:
			return subject_matches[0]

	return None


async def process_explicit_memory_request(user_message: str, user_id: int) -> tuple[str | None, str | None, bool]:
	persona = get_persona_for_user(user_id)
	if not persona:
		return None, None, False

	cleaned = sanitize_input(user_message).strip()
	if not cleaned:
		return None, None, False

	memories = await list_persona_memories(persona)

	replace_patterns = [
		r'^(?:please\s+)?(?:change|update|modify)\s+(?:what you remember(?: about)?|the memory(?: about)?|your memory(?: about)?|memory(?: about)?)\s+(?P<old>.+?)\s+(?:to|with)\s+(?P<new>.+?)\s*[.!?]*$',
		r'^(?:please\s+)?remember\s+(?P<new>.+?)\s+instead of\s+(?P<old>.+?)\s*[.!?]*$',
	]
	forget_patterns = [
		r'^(?:please\s+)?(?:forget|delete|remove)\s+(?:what you remember(?: about)?|the memory(?: about)?|your memory(?: about)?|memory(?: about)?|that\s+)?(?P<target>.+?)\s*[.!?]*$',
	]
	remember_patterns = [
		r'^(?:please\s+)?remember(?:\s+that)?\s+(?P<text>.+?)\s*[.!?]*$',
	]

	for pattern in replace_patterns:
		match = re.match(pattern, cleaned, re.IGNORECASE)
		if not match:
			continue
		old_text = match.group("old").strip()
		new_text = match.group("new").strip()
		resolved = resolve_memory_reference(memories, old_text)
		if resolved is None:
			return None, f"I couldn't tell which memory to change for {persona}. Tell me the exact memory text or a more specific part of it.", True
		_, existing_text, _ = resolved
		await replace_persona_memory_by_text(persona, existing_text, new_text)
		return f"Updated persona memory for {persona}: '{existing_text}' -> '{new_text}'.", None, True

	for pattern in forget_patterns:
		match = re.match(pattern, cleaned, re.IGNORECASE)
		if not match:
			continue
		target = match.group("target").strip()
		resolved = resolve_memory_reference(memories, target)
		if resolved is None:
			return None, f"I couldn't find a single memory to forget for {persona}. Tell me the exact memory text or a more specific part of it.", True
		memory_number, existing_text, _ = resolved
		await delete_persona_memory(persona, memory_number)
		return f"Deleted persona memory for {persona}: '{existing_text}'.", None, True

	for pattern in remember_patterns:
		match = re.match(pattern, cleaned, re.IGNORECASE)
		if not match:
			continue
		memory_text = match.group("text").strip()
		if any(normalize_memory_text(existing_text) == normalize_memory_text(memory_text) for _, existing_text, _ in memories):
			return f"Persona memory for {persona} already includes '{memory_text}'.", None, True
		await add_persona_memory(persona, memory_text)
		return f"Saved persona memory for {persona}: '{memory_text}'.", None, True

	return None, None, False


async def apply_persona_memory_actions(user_id: int, reply: str):
	persona = get_persona_for_user(user_id)
	if not persona:
		return

	existing = set(await load_persona_memory_texts(persona))
	for match in MEMORY_DIRECTIVE_RE.finditer(reply):
		action = (match.group("action") or "").lower()
		body = (match.group("body") or "").strip()
		old_text = (match.group("old") or "").strip()
		if action == "add" and body and body not in existing:
			await add_persona_memory(persona, body)
			existing.add(body)
		elif action == "replace" and old_text and body:
			await replace_persona_memory_by_text(persona, old_text, body)
			if old_text in existing:
				existing.remove(old_text)
			existing.add(body)
		elif action == "delete" and body:
			deleted = await delete_persona_memory_by_text(persona, body)
			if deleted and body in existing:
				existing.remove(body)


def _extract_local_text_response(data: dict) -> str:
	"""Extract assistant text from Ollama or OpenAI-compatible responses."""
	if not isinstance(data, dict):
		return ""

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

	response_text = data.get("response", "")
	if isinstance(response_text, str):
		return response_text.strip()

	return ""


def _ollama_sync(prompt: str) -> str:
	base_url = get_ollama_base_url()
	provider_kind = get_local_provider_kind()
	chat_model = get_local_model("chat")

	try:
		if provider_kind == "ollama":
			response = requests.post(
				f"{base_url}/api/chat",
				json={
					"model": chat_model,
					"messages": [{"role": "user", "content": prompt}],
					"stream": False,
				},
				timeout=180,
			)
		else:
			response = requests.post(
				f"{base_url}/chat/completions",
				json={
					"model": chat_model,
					"messages": [{"role": "user", "content": prompt}],
					"stream": False,
				},
				timeout=180,
			)

		response.raise_for_status()
		try:
			data = response.json()
		except ValueError:
			data = None
			for line in response.text.strip().splitlines():
				try:
					data = json.loads(line)
				except Exception:
					continue
			if data is None:
				raise

		reply = _extract_local_text_response(data)
		return reply or "baa… My local AI provider answered with an empty reply."
	except Exception:
		return "baa… I can't connect to my local AI provider right now. Please check the configured provider and base URL."


async def ollama_chat(prompt: str) -> str:
	return await asyncio.to_thread(_ollama_sync, prompt)


def search_weather(location: str = DEFAULT_LOCATION) -> str:
	key = os.getenv("WEATHER_API_KEY", "") or get_setting("OPENWEATHER_KEY") or ""
	if not key:
		return "baa… I don't have weather access set up yet. (Missing API key)"
	try:
		resp = requests.get(
			"https://api.openweathermap.org/data/2.5/weather",
			params={"q": location, "appid": key, "units": "metric"},
			timeout=5,
		)
		data = resp.json()
		if resp.status_code != 200:
			return f"baa… I couldn't find weather for '{location}'."
		return (
			f"**Weather in {location}:**\n"
			f"🌡️ {data['main']['temp']}°C (feels like {data['main']['feels_like']}°C)\n"
			f"💧 Humidity: {data['main']['humidity']}%\n"
			f"☁️ {data['weather'][0]['description'].capitalize()}"
		)
	except Exception as e:
		return f"baa… I had trouble fetching weather: {e}"


def search_news(query: str, location: str = DEFAULT_LOCATION) -> str:
	key = get_news_key()
	if not key:
		return "baa… I don't have news access set up yet. (Missing API key)"
	try:
		term = f"{query} {location}" if query else location
		resp = requests.get(
			"https://newsapi.org/v2/everything",
			params={
				"q": term,
				"sortBy": "publishedAt",
				"language": "en",
				"pageSize": 5,
				"apiKey": key,
			},
			timeout=5,
		)
		data = resp.json()
		if data.get("totalResults", 0) == 0:
			return f"baa… I couldn't find news about '{query}' in {location}."
		msg = f"**Latest news about {query or 'your area'}:**\n"
		for i, art in enumerate(data.get("articles", [])[:3], 1):
			msg += f"{i}. **{art['title']}**\n   Source: {art['source']['name']}\n   {art['url']}\n\n"
		return msg
	except Exception as e:
		return f"baa… I had trouble fetching news: {e}"


def _image_counter_start() -> int:
	raw = get_setting("IMAGE_COUNTER") or "1"
	try:
		return int(raw) % 10000
	except ValueError:
		return 1


def allocate_image_filename(original_filename: str) -> str:
	os.makedirs(IMAGE_FOLDER, exist_ok=True)
	_, ext = os.path.splitext(original_filename or "")
	ext = ext.lower() or ".bin"
	candidate = _image_counter_start()
	for _ in range(10000):
		filename = f"{candidate:04d}{ext}"
		full_path = os.path.join(IMAGE_FOLDER, filename)
		if not os.path.exists(full_path):
			set_setting("IMAGE_COUNTER", str((candidate + 1) % 10000))
			return filename
		candidate = (candidate + 1) % 10000
	raise RuntimeError("No free image filenames are available.")


async def prune_old_unfavorited_images(delete_all_unfavorited: bool = False) -> int:
	favorites = set(load_favorites().get("images", []))
	if not os.path.exists(IMAGE_FOLDER):
		return 0

	deleted = 0
	now = datetime.now().timestamp()
	max_age = MAX_IMAGE_AGE_DAYS * 24 * 60 * 60
	for filename in os.listdir(IMAGE_FOLDER):
		path = os.path.join(IMAGE_FOLDER, filename)
		if not os.path.isfile(path):
			continue
		if path in favorites:
			continue
		age = now - os.path.getmtime(path)
		if delete_all_unfavorited or age > max_age:
			try:
				os.remove(path)
				deleted += 1
			except OSError:
				continue
	return deleted


async def save_incoming_image(attachment: discord.Attachment) -> str:
	filename = allocate_image_filename(attachment.filename)
	target_path = os.path.join(IMAGE_FOLDER, filename)
	image_bytes = await attachment.read()
	with open(target_path, "wb") as f:
		f.write(image_bytes)
	return target_path


def paginate_lines(header: str, lines: list[str], limit: int = 1900) -> list[str]:
	if not lines:
		return [header]

	messages = []
	current = header + "\n"
	for line in lines:
		pending = current + line + "\n"
		if len(pending) > limit and current.strip() != header.strip():
			messages.append(current.rstrip())
			current = header + "\n" + line + "\n"
		elif len(pending) > limit:
			messages.append((header + "\n" + line)[:limit])
			current = header + "\n"
		else:
			current = pending
	if current.strip():
		messages.append(current.rstrip())
	return messages


def split_message(content: str, limit: int = 2000) -> list[str]:
	text = (content or "").strip()
	if not text:
		return []
	if len(text) <= limit:
		return [text]

	messages = []
	remaining = text
	while len(remaining) > limit:
		split_at = remaining.rfind("\n\n", 0, limit + 1)
		if split_at <= 0:
			split_at = remaining.rfind("\n", 0, limit + 1)
		if split_at <= 0:
			split_at = remaining.rfind(" ", 0, limit + 1)
		if split_at <= 0:
			split_at = limit
		chunk = remaining[:split_at].rstrip()
		if chunk:
			messages.append(chunk)
		remaining = remaining[split_at:].lstrip()

	if remaining:
		messages.append(remaining)
	return messages


async def send_chunked_message(ctx: commands.Context, content: str, limit: int = 2000):
	for chunk in split_message(content, limit=limit):
		await ctx.send(chunk)


async def build_chat_prompt(user_message: str, user_id: int, memory_status: str | None = None, allow_model_memory_edits: bool = True) -> str:
	cleaned = sanitize_input(user_message)
	notes_context = await build_notes_context(cleaned)
	memory_context = await build_persona_memory_context(user_id)
	moments_context = await build_recent_moments_context()
	personality_guidance = build_personality_guidance(user_id)
	speaker = who_is(user_id)

	if speaker == "ally":
		greeting = "hi princess!"
	elif speaker == "muggy":
		greeting = "hi meowggy!"
	else:
		greeting = "hello."

	memory_instruction = (
		"Respond naturally. Only if the user explicitly asks you to remember, update, change, or forget something for their persona, append structured directives exactly in one of these forms:\n"
		"<persona-memory action=\"add\">memory text</persona-memory>\n"
		"<persona-memory action=\"replace\" old=\"old memory text\">new memory text</persona-memory>\n"
		"<persona-memory action=\"delete\">memory text</persona-memory>\n"
		"If the memory change request is ambiguous, ask a clarifying question and do not emit any directive."
	)
	if not allow_model_memory_edits:
		memory_instruction = "A persona memory request has already been handled in code. Acknowledge it naturally and do not emit persona-memory directives."

	status_block = f"Memory request status already handled: {memory_status}\n\n" if memory_status else ""

	return (
		f"{greeting}\n\n"
		"Personality guidance (highest priority after safety rules):\n"
		f"{personality_guidance}\n\n"
		"Persona memories for this speaker:\n"
		f"{memory_context}\n\n"
		"Relevant notes from the notes database:\n"
		f"{notes_context}\n\n"
		"Recent short-term moments:\n"
		f"{moments_context}\n\n"
		f"{status_block}"
		f"User says: {cleaned}\n\n"
		f"{memory_instruction}"
	)


async def trigger_distillation() -> None:
	"""Summarise all current moments into the distillation paragraph, then clear them."""
	moments = await list_moments()
	if not moments:
		return

	previous = await get_distillation()
	moments_text = "\n".join(
		f"- {m[1].replace(chr(10), ' ')}" for m in reversed(moments)
	)

	if previous:
		prompt = (
			f"Previous distillation:\n{previous}\n\n"
			f"New moments:\n{moments_text}\n\n"
			"Summarise the previous distillation and new moments into a single concise paragraph "
			"capturing the most important patterns, topics, and emotional themes."
		)
	else:
		prompt = (
			f"Moments:\n{moments_text}\n\n"
			"Summarise these moments into a single concise paragraph capturing the most important "
			"patterns, topics, and emotional themes."
		)

	summary = safe_output(await ollama_chat(prompt))
	summary = strip_memory_directives(summary).strip() or previous or ""
	if summary:
		await set_distillation(summary)
	await clear_moments()


async def process_chat_message(user_message: str, user_id: int) -> str:
	update_last_interaction()
	await prune_old_unfavorited_images()

	memory_status, direct_response, handled_memory_request = await process_explicit_memory_request(user_message, user_id)
	if direct_response:
		clean_reply = direct_response
	else:
		prompt = await build_chat_prompt(
			user_message,
			user_id,
			memory_status=memory_status,
			allow_model_memory_edits=not handled_memory_request,
		)
		reply = safe_output(await ollama_chat(prompt))
		if not handled_memory_request:
			await apply_persona_memory_actions(user_id, reply)
		clean_reply = strip_memory_directives(reply) or "baa… I had a thought, but I lost the words."

	moment_text = f"User ({who_is(user_id)}): {sanitize_input(user_message)}\nLavender: {clean_reply}"
	await add_moment(moment_text)
	if await count_moments() >= DISTILLATION_THRESHOLD:
		await trigger_distillation()
	return clean_reply


async def generate_response(user_message: str, user_id: int = 0) -> str:
	return await process_chat_message(user_message, user_id)


@bot.event
async def on_ready():
	await prune_old_unfavorited_images()
	bot_user = bot.user
	if bot_user is None:
		print("Logged in, but bot user is unavailable.")
	else:
		print(f"Logged in as {bot_user} (ID: {bot_user.id})")
	print("------")


@bot.event
async def on_message(message: discord.Message):
	if message.author.bot:
		return

	await bot.process_commands(message)

	if not is_allowed_user(message.author.id):
		return

	update_last_interaction()

	if message.attachments:
		attachment = message.attachments[0]
		if attachment.content_type and attachment.content_type.startswith("image/"):
			await prune_old_unfavorited_images()
			temp_path = await save_incoming_image(attachment)
			async with message.channel.typing():
				vision_result = ask_ollama_vision(temp_path)
			description = vision_result.get("detailed_description") or vision_result.get("description") or f"Saved that picture as {os.path.basename(temp_path)}."
			description += f"\n\nSaved as **{os.path.basename(temp_path)}**."
			await message.channel.send(description)
			return

	bot_user = bot.user
	if bot_user and bot_user in message.mentions:
		cleaned = message.content.replace(f"<@{bot_user.id}>", "").strip() or "hello"
		async with message.channel.typing():
			reply = await process_chat_message(cleaned, message.author.id)
		await message.channel.send(reply)


@bot.command(name="lav")
async def lav_command(ctx: commands.Context, *, message: str):
	if not is_allowed_user(ctx.author.id):
		return
	async with ctx.typing():
		reply = await process_chat_message(message, ctx.author.id)
	await ctx.send(reply)


@bot.command(name="ping")
async def ping_command(ctx: commands.Context):
	if not is_allowed_user(ctx.author.id):
		return
	await ctx.send("pong")


@bot.command(name="note")
async def note_command(ctx: commands.Context, *, note_text: str):
	if not is_allowed_user(ctx.author.id):
		return
	text = note_text.strip()
	if not text:
		await ctx.send("Usage: !note <text>")
		return
	note_number = await add_note(text)
	await ctx.send(f"Saved note #{note_number}.")


@bot.command(name="batch_note")
async def batch_note_command(ctx: commands.Context, limit: int):
	if not is_allowed_user(ctx.author.id):
		return
	if limit <= 0:
		await ctx.send("Usage: !batch_note <positive_number>")
		return

	collected = []
	async for msg in ctx.channel.history(limit=limit + 10):
		if msg.id == ctx.message.id or msg.author.bot:
			continue
		collected.append(msg.content)
		if len(collected) >= limit:
			break

	collected.reverse()
	added = await add_notes_batch(collected)
	await ctx.send(f"Saved {added} notes from recent channel messages.")


@bot.command(name="scan_tag")
async def scan_tag_command(ctx: commands.Context, limit: int, *, tag_name: str):
	if not is_allowed_user(ctx.author.id):
		return
	if limit <= 0:
		await ctx.send("Usage: !scan_tag <positive_number> \"tag name\"")
		return

	normalized_tag = await create_tag(tag_name)
	if not normalized_tag:
		await ctx.send("Usage: !scan_tag <positive_number> \"tag name\"")
		return

	prefix = normalize_tag_name(tag_name)
	matches = []
	async for msg in ctx.channel.history(limit=limit + 10):
		if msg.id == ctx.message.id or msg.author.bot:
			continue
		if normalize_tag_name(msg.content).startswith(prefix):
			matches.append(msg.content)
		if len(matches) >= limit:
			break

	matches.reverse()
	added = await add_notes_batch(matches)
	await ctx.send(f"Saved {added} notes under tag \"{normalized_tag}\".")


@bot.command(name="listnotes")
async def listnotes_command(ctx: commands.Context, page: int = 1):
	if not is_allowed_user(ctx.author.id):
		return

	notes = list(await list_notes())
	if not notes:
		await ctx.send("No notes yet.")
		return

	per_page = 20
	total = len(notes)
	pages = (total + per_page - 1) // per_page
	if page < 1 or page > pages:
		await ctx.send(f"Invalid page. There are {pages} pages.")
		return

	page_rows = notes[(page - 1) * per_page : page * per_page]
	lines = [f"**{note_number}** ({taken_date}) — {note_text.replace(chr(10), ' ')}" for note_number, note_text, taken_date in page_rows]
	for chunk in paginate_lines(f"Notes (page {page}/{pages}, newest first)", lines):
		await ctx.send(chunk)


@bot.command(name="listtag")
async def listtag_command(ctx: commands.Context, *, tag_name: str):
	if not is_allowed_user(ctx.author.id):
		return

	normalized = normalize_tag_name(tag_name)
	rows = await list_notes_by_tag(normalized)
	if not rows:
		await ctx.send(f"No notes found under tag \"{normalized}\".")
		return

	lines = [f"**{note_number}** ({taken_date}) — {note_text.replace(chr(10), ' ')}" for note_number, note_text, taken_date in rows]
	for chunk in paginate_lines(f"Tag: {normalized}", lines):
		await ctx.send(chunk)


@bot.command(name="list_tags")
async def list_tags_command(ctx: commands.Context):
	if not is_allowed_user(ctx.author.id):
		return

	tag_counts, untagged = await list_tags_with_counts()
	lines = [f"- {tag_name} ({count})" for tag_name, count in tag_counts]
	lines.append(f"- untagged ({untagged})")
	await ctx.send("Current tags:\n" + "\n".join(lines))


@bot.command(name="tag")
async def tag_command(ctx: commands.Context, action: str, *, tag_name: str):
	if not is_allowed_user(ctx.author.id):
		return

	normalized = normalize_tag_name(tag_name)
	if not normalized:
		await ctx.send("Usage: !tag create \"tag name\" | !tag delete \"tag name\"")
		return

	action = action.lower().strip()
	if action == "create":
		await create_tag(normalized)
		# Show how many existing notes match this tag
		matching_notes = await list_notes_by_tag(normalized)
		match_count = len(matching_notes)
		if match_count > 0:
			await ctx.send(f"Created tag \"{normalized}\". Found {match_count} matching note(s).")
		else:
			await ctx.send(f"Created tag \"{normalized}\". No existing notes match this tag yet.")
		return
	if action == "delete":
		deleted = await delete_tag(normalized)
		await ctx.send(f"Deleted tag \"{normalized}\"." if deleted else f"Tag \"{normalized}\" does not exist.")
		return

	await ctx.send("Usage: !tag create \"tag name\" | !tag delete \"tag name\"")


@bot.command(name="unnote")
async def unnote_command(ctx: commands.Context, note_number: int):
	if not is_allowed_user(ctx.author.id):
		return
	deleted = await delete_note_by_number(note_number)
	await ctx.send(f"Deleted note #{note_number}." if deleted else "Invalid note number.")


@bot.command(name="searchnote")
async def searchnote_command(ctx: commands.Context, query: str, page: int = 1):
	if not is_allowed_user(ctx.author.id):
		return

	rows = list(await search_notes(query.strip()))
	if not rows:
		await ctx.send("No matching notes found.")
		return

	per_page = 20
	total = len(rows)
	pages = (total + per_page - 1) // per_page
	if page < 1 or page > pages:
		await ctx.send(f"Invalid page. There are {pages} pages.")
		return

	page_rows = rows[(page - 1) * per_page : page * per_page]
	lines = [f"**{note_number}** ({taken_date}) — {note_text.replace(chr(10), ' ')}" for note_number, note_text, taken_date in page_rows]
	for chunk in paginate_lines(f"Notes matching '{query}' (page {page}/{pages})", lines):
		await ctx.send(chunk)


@bot.command(name="analyze_history")
async def analyze_history_command(ctx: commands.Context, limit: int = 50):
	if not is_allowed_user(ctx.author.id):
		return

	messages_data = []
	async for msg in ctx.channel.history(limit=limit):
		if msg.author.bot or not is_allowed_user(msg.author.id):
			continue
		ts = msg.created_at.strftime("%Y-%m-%d %H:%M UTC")
		messages_data.append(f"[{ts}] {sanitize_input(msg.content[:300])}")

	if not messages_data:
		await ctx.send("baa… I couldn't find any messages to analyze.")
		return

	messages_data.reverse()
	await ctx.send(f"baa~ analyzing {len(messages_data)} messages for health patterns… give me a moment ✨")
	joined_messages = "\n".join(messages_data)
	analysis_prompt = (
		"You are a caring personal health pattern analyst. Analyze the following timestamped diary/chat messages and identify:\n"
		"1. Any mentions of illness, discomfort, pain, fatigue, stress, mood dips, or other health concerns\n"
		"2. Patterns over time (recurring symptoms, timing, trends)\n"
		"3. Notable anomalies or clusters of symptoms\n\n"
		"If you find health concerns that warrant further research, add exactly one line at the very end of your response in this exact format:\n"
		"[SEARCH: specific health query to look up potential causes]\n\n"
		"Write in a warm, supportive tone. If no health concerns are found, say so reassuringly.\n\n"
		f"Messages to analyze:\n{joined_messages}\n\nAnalysis:"
	)

	async with ctx.typing():
		analysis = safe_output(await ollama_chat(analysis_prompt))

	search_match = re.search(r"\[SEARCH:\s*(.+?)\]", analysis, re.IGNORECASE)
	display_analysis = re.sub(r"\[SEARCH:\s*.+?\]\s*", "", analysis).strip()
	await add_moment(f"Health analysis report:\n{display_analysis}")

	full_text = f"**📊 Health Pattern Analysis** (last {len(messages_data)} messages)\n\n{display_analysis}"
	for i in range(0, len(full_text), 1900):
		await ctx.send(full_text[i:i + 1900])

	if not search_match:
		return

	query = search_match.group(1).strip()
	await ctx.send("baa~ I spotted some health patterns — let me search for potential causes… 🌐")

	def _duckduckgo_health(q: str) -> str:
		import urllib.parse

		encoded = urllib.parse.quote(q)
		url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_redirect=1&no_html=1"
		try:
			data = requests.get(url, timeout=8).json()
		except Exception:
			return ""
		result_parts = []
		if data.get("AbstractText"):
			result_parts.append(data["AbstractText"])
		for topic in data.get("RelatedTopics", [])[:3]:
			if isinstance(topic, dict) and topic.get("Text"):
				result_parts.append(topic["Text"])
		return "\n\n".join(result_parts)

	async with ctx.typing():
		raw_search = await asyncio.to_thread(_duckduckgo_health, query)

	if not raw_search:
		await ctx.send(f"baa… I searched for *{query}* but couldn't find specific results. You might want to look into this with a doctor! 🌸")
		return

	summarize_prompt = (
		f"I searched the internet for: '{query}'\n\n"
		f"Search results (external reference only):\n{sanitize_input(raw_search[:1500])}\n\n"
		"Based on these results, briefly explain in 3-5 sentences the potential causes in a warm, supportive tone. "
		"This is general wellness information only, not medical advice or a diagnosis. Be concise and helpful."
	)
	async with ctx.typing():
		cause_summary = safe_output(await ollama_chat(summarize_prompt))
	await ctx.send(f"**💡 Potential causes for:** *{query}*\n\n{cause_summary}")


@bot.command(name="listmoments")
async def listmoments_command(ctx: commands.Context, page: int = 1):
	if not is_allowed_user(ctx.author.id):
		return

	rows = list(await list_moments())
	if not rows:
		await ctx.send("No moments yet.")
		return

	lines = []
	for i, (_, moment_text, created_at) in enumerate(rows, start=1):
		preview = moment_text.replace("\n", " ")[:160]
		lines.append(f"**{i}** ({created_at}) — {preview}")

	per_page = 20
	total = len(lines)
	pages = (total + per_page - 1) // per_page
	if page < 1 or page > pages:
		await ctx.send(f"Invalid page. There are {pages} pages.")
		return

	page_lines = lines[(page - 1) * per_page : page * per_page]
	for chunk in paginate_lines(f"Moments (page {page}/{pages}, newest first)", page_lines):
		await ctx.send(chunk)


@bot.command(name="del_moment")
async def del_moment_command(ctx: commands.Context, moment_number: int):
	if not is_allowed_user(ctx.author.id):
		return
	deleted = await delete_moment_by_number(moment_number)
	if deleted:
		await ctx.send(f"baa~ moment {moment_number} deleted.")
	else:
		await ctx.send(f"baa… I couldn't find moment {moment_number}.")


@bot.command(name="distillation")
async def distillation_command(ctx: commands.Context):
	if not is_allowed_user(ctx.author.id):
		return
	content = await get_distillation()
	if content:
		await ctx.send(f"**Distillation:**\n{content}")
	else:
		await ctx.send("baa… no distillation yet. It forms once 50 moments have accumulated.")


@bot.command(name="prune")
async def prune_command(ctx: commands.Context):
	if not is_allowed_user(ctx.author.id):
		return
	deleted_images = await prune_old_unfavorited_images(delete_all_unfavorited=True)
	await ctx.send(
		"baa… pruning complete!\n"
		f"- Deleted unfavourited pictures: {deleted_images}"
	)


@bot.command(name="fav")
async def fav_command(ctx: commands.Context, *, image_name: str):
	path = os.path.join(IMAGE_FOLDER, image_name)
	if not os.path.exists(path):
		await ctx.send("I couldn't find that image…")
		return
	data = load_favorites()
	if path not in data["images"]:
		data["images"].append(path)
		save_favorites(data)
		await ctx.send(f"okie! I'll keep {image_name} safe forever.")
	else:
		await ctx.send("That one is already a favorite!")


@bot.command(name="unfav")
async def unfav_command(ctx: commands.Context, *, image_name: str):
	path = os.path.join(IMAGE_FOLDER, image_name)
	data = load_favorites()
	if path in data["images"]:
		data["images"].remove(path)
		save_favorites(data)
		await ctx.send(f"okie… I won't treat {image_name} as a favorite anymore.")
	else:
		await ctx.send("That image wasn't in favorites.")


@bot.command(name="listfav")
async def listfav_command(ctx: commands.Context):
	data = load_favorites()
	favs = data.get("images", [])
	if not favs:
		await ctx.send("You don't have any favorite images yet…")
		return
	await ctx.send("**Favorite images:**\n" + "\n".join(f"- {os.path.basename(path)}" for path in favs))


@bot.command(name="album")
async def album_command(ctx: commands.Context):
	if not os.path.exists(IMAGE_FOLDER):
		await ctx.send("I don't have any pictures saved yet…")
		return
	files = sorted(os.listdir(IMAGE_FOLDER))
	if not files:
		await ctx.send("I don't have any pictures saved yet…")
		return
	await ctx.send("Here are the pictures I've saved:\n" + "\n".join(f"- {name}" for name in files))


@bot.command(name="listpics")
async def listpics_command(ctx: commands.Context, page: int = 1):
	if not os.path.exists(IMAGE_FOLDER):
		await ctx.send("No pictures saved.")
		return
	files = sorted(os.listdir(IMAGE_FOLDER), reverse=True)
	if not files:
		await ctx.send("No pictures saved.")
		return

	per_page = 20
	total = len(files)
	pages = (total + per_page - 1) // per_page
	if page < 1 or page > pages:
		await ctx.send(f"Invalid page. There are {pages} pages.")
		return
	page_files = files[(page - 1) * per_page : page * per_page]
	lines = [f"**{index}** — {name}" for index, name in enumerate(page_files, start=(page - 1) * per_page)]
	await ctx.send(f"Pictures (page {page}/{pages}, newest first)\n" + "\n".join(lines))


@bot.command(name="favnum")
async def favnum_command(ctx: commands.Context, index: int):
	files = sorted(os.listdir(IMAGE_FOLDER), reverse=True)
	if index < 0 or index >= len(files):
		await ctx.send("Invalid picture number.")
		return
	await fav_command(ctx, image_name=files[index])


@bot.command(name="unfavnum")
async def unfavnum_command(ctx: commands.Context, index: int):
	files = sorted(os.listdir(IMAGE_FOLDER), reverse=True)
	if index < 0 or index >= len(files):
		await ctx.send("Invalid picture number.")
		return
	await unfav_command(ctx, image_name=files[index])


@bot.command(name="guji")
async def guji_command(ctx: commands.Context):
	if not is_allowed_user(ctx.author.id):
		return
	help_text = (
		"baa~ here's what I can do:\n\n"
		"💬 **Talking**\n"
		"- `!lav <message>` — talk to me\n"
		"- `@Lavender <message>` — mention me to chat\n"
		"- Tell me `please remember ...`, `change what you remember about ... to ...`, or `forget ...` to manage persona memory\n\n"
		"📝 **Notes & Tags**\n"
		"- `!note <text>` — save a note exactly as written\n"
		"- `!batch_note <count>` — save recent channel messages as notes\n"
		"- `!listnotes <page>` — list notes\n"
		"- `!unnote <number>` — delete note by number\n"
		"- `!searchnote <keyword> <page>` — search notes\n"
		"- `!tag create \"name\"` — create a note tag\n"
		"- `!tag delete \"name\"` — delete a note tag\n"
		"- `!listtag \"name\"` — list notes under a tag\n"
		"- `!scan_tag <count> \"name\"` — scan recent messages into a tag\n"
		"- `!list_tags` — list tags and note counts\n\n"
		"🧠 **Short-Term Memory**\n"
		"- `!analyze_history <limit>` — analyze recent messages and save the report as a moment\n"
		"- `!listmoments <page>` — list moments (newest first, numbered)\n"
		"- `!del_moment <number>` — delete a moment by its number\n"
		"- `!distillation` — show the distillation paragraph (formed after every 50 moments)\n"
		"📷 **Images**\n"
		"- send an image — I'll save it with an ordered filename and describe it\n"
		"- `!listpics <page>` — list saved images\n"
		"- `!favnum <number>` — favourite an image\n"
		"- `!unfavnum <number>` — unfavourite an image\n"
		"- `!listfav` — list favourite images\n\n"
		"- `!prune` — delete all unfavourited pictures immediately\n\n"
		"🌐 **Internet Search**\n"
		"- `!weather [location]` — get weather\n"
		"- `!news [query]` — search the news\n\n"
		"⚙️ **System**\n"
		"- `!ping` — check if I'm awake\n"
		"- `!guji` — show this help menu\n"
		"- `!ver` — show version changes\n"
		"- `!security_audit full` — run full security audit (pip-audit, bandit, safety)\n"
		"- `!security_audit <type>` — run specific audit (pip-audit, bandit, safety)\n"
		"- `dry_run_commands.py` — verify note/tag and memory parsing from the terminal\n"
	)
	await send_chunked_message(ctx, help_text)


@bot.command(name="weather")
async def weather_command(ctx: commands.Context, *, location: str = DEFAULT_LOCATION):
	if not is_allowed_user(ctx.author.id):
		return
	async with ctx.typing():
		result = await asyncio.to_thread(search_weather, location)
	await ctx.send(result)


@bot.command(name="news")
async def news_command(ctx: commands.Context, *, query: str = ""):
	if not is_allowed_user(ctx.author.id):
		return
	async with ctx.typing():
		result = await asyncio.to_thread(search_news, query)
	await ctx.send(result)


@bot.command(name="ver")
async def ver_command(ctx: commands.Context):
	if not is_allowed_user(ctx.author.id):
		return
	ver_text = (
		"CURRENT VERSION\n\n"
		"3/31/2026 Lavbot v4.1 — MOMENTS DISTILLATION\n"
		"- Moments are no longer auto-deleted; they persist until manually removed\n"
		"- Added !del_moment <number> to delete a specific moment by its numbered position\n"
		"- Added !distillation to view the distillation paragraph\n"
		"- Every 50 moments are automatically summarised into the distillation paragraph\n"
		"- !prune now only deletes unfavourited pictures (no longer clears moments or untagged notes)\n\n"
		"3/26/2026 Lavbot v4.0 — MEMORY REBUILD, QUICKSTART, AND DRY-RUN\n"
		"- Rebuilt notes, tags, persona memory, and short-term moments around a clean storage layer\n"
		"- Added deterministic handling for explicit persona-memory edits like remember/change/forget\n"
		"- Added a quickstart guide in the TUI for Python, Ollama, model pull, and first-run setup\n"
		"- Added a dry-run verification script for notes, tags, persona memories, and moments\n"
		"- Updated help text and version history for the new Lavbot 4.0 workflow\n"
	)
	await send_chunked_message(ctx, ver_text)


@bot.command(name="security_audit")
async def security_audit_command(ctx: commands.Context, audit_type: str = "full"):
	"""Run security audits on the bot dependencies and code."""
	if not is_allowed_user(ctx.author.id):
		return
	
	audit_type = audit_type.lower().strip()
	
	if audit_type == "full":
		await ctx.send("🛡️ Running full security audit... (this may take a minute)")
		async with ctx.typing():
			report = await run_full_security_audit()
		
		# Split report into chunks to avoid Discord's 2000 char limit
		for chunk in paginate_lines("Security Audit Report", report.split('\n')):
			await ctx.send(chunk)
		return
	
	elif audit_type == "pip-audit":
		await ctx.send("🔍 Running pip-audit (checking for vulnerable dependencies)...")
		async with ctx.typing():
			result = await run_pip_audit()
		
		for chunk in paginate_lines("Pip Audit Results", result.split('\n')):
			await ctx.send(chunk)
		return
	
	elif audit_type == "bandit":
		await ctx.send("🔍 Running bandit (checking for security issues in code)...")
		async with ctx.typing():
			result = await run_bandit()
		
		for chunk in paginate_lines("Bandit Results", result.split('\n')):
			await ctx.send(chunk)
		return
	
	elif audit_type == "safety":
		await ctx.send("🔍 Running safety check (checking pip packages for vulnerabilities)...")
		async with ctx.typing():
			result = await run_safety_check()
		
		for chunk in paginate_lines("Safety Check Results", result.split('\n')):
			await ctx.send(chunk)
		return
	
	await ctx.send("Usage: `!security_audit full | !security_audit pip-audit | !security_audit bandit | !security_audit safety`")


if __name__ == "__main__":
	TOKEN = get_discord_token()
	if not TOKEN:
		raise RuntimeError("Discord token is not configured. Set it via user.db (TUI) or DISCORD_TOKEN env var.")
	bot.run(TOKEN)
