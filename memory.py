import asyncio
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiosqlite
from data_paths import MEMORY_DB_PATH, ensure_userdata_dirs

ensure_userdata_dirs()

DB_PATH = os.getenv("LAVENDER_MEMORY_DB", str(MEMORY_DB_PATH))


def _today() -> str:
	return datetime.now().strftime("%Y-%m-%d")


def normalize_tag_name(tag_name: str) -> str:
	text = (tag_name or "").strip()
	if text.startswith('"') and text.endswith('"') and len(text) >= 2:
		text = text[1:-1]
	return " ".join(text.split()).strip().lower()


def note_matches_tag(note_text: str, normalized_tag: str) -> bool:
	if not normalized_tag:
		return False

	normalized_note = " ".join((note_text or "").strip().lower().split())
	pattern = rf"^{re.escape(normalized_tag)}(?=$|[^a-z0-9])"
	return re.match(pattern, normalized_note) is not None


def match_note_to_existing_tag(note_text: str, tags: List[str]) -> Optional[str]:
	matches = [tag for tag in tags if note_matches_tag(note_text, tag)]
	if not matches:
		return None
	return max(matches, key=len)


async def _table_exists(db: aiosqlite.Connection, table_name: str) -> bool:
	cursor = await db.execute(
		"SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
		(table_name,),
	)
	row = await cursor.fetchone()
	return row is not None


async def _migrate_notes_table(db: aiosqlite.Connection):
	if not await _table_exists(db, "notes"):
		await db.execute(
			"""
			CREATE TABLE IF NOT EXISTS notes (
				note_number INTEGER PRIMARY KEY,
				note_text TEXT NOT NULL,
				taken_date TEXT NOT NULL
			)
			"""
		)
		return

	cursor = await db.execute("PRAGMA table_info(notes)")
	columns = [row[1] for row in await cursor.fetchall()]
	if "note_number" in columns:
		return

	cursor = await db.execute("SELECT note_text, taken_date FROM notes ORDER BY id ASC")
	rows = await cursor.fetchall()

	await db.execute("ALTER TABLE notes RENAME TO notes_legacy")
	await db.execute(
		"""
		CREATE TABLE notes (
			note_number INTEGER PRIMARY KEY,
			note_text TEXT NOT NULL,
			taken_date TEXT NOT NULL
		)
		"""
	)

	for idx, (note_text, taken_date) in enumerate(rows, start=1):
		await db.execute(
			"INSERT INTO notes (note_number, note_text, taken_date) VALUES (?, ?, ?)",
			(idx, note_text, taken_date),
		)

	await db.execute("DROP TABLE notes_legacy")


async def init_db():
	async with aiosqlite.connect(DB_PATH) as db:
		await db.execute("PRAGMA foreign_keys = ON")
		await _migrate_notes_table(db)

		await db.execute(
			"""
			CREATE TABLE IF NOT EXISTS note_tags (
				normalized_name TEXT PRIMARY KEY,
				created_at TEXT NOT NULL
			)
			"""
		)
		await db.execute(
			"""
			CREATE TABLE IF NOT EXISTS persona_memories (
				persona TEXT NOT NULL,
				memory_number INTEGER NOT NULL,
				memory_text TEXT NOT NULL,
				created_at TEXT NOT NULL,
				PRIMARY KEY (persona, memory_number)
			)
			"""
		)
		await db.execute(
			"""
			CREATE TABLE IF NOT EXISTS moments (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				moment_text TEXT NOT NULL,
				created_at TEXT NOT NULL
			)
			"""
		)

		await db.execute("DROP TABLE IF EXISTS memories")
		await db.execute("DROP TABLE IF EXISTS image_memories")
		await db.commit()


async def _next_note_number(db: aiosqlite.Connection) -> int:
	cursor = await db.execute("SELECT COALESCE(MAX(note_number), 0) + 1 FROM notes")
	row = await cursor.fetchone()
	return int(row[0])


async def add_note(note_text: str, taken_date: str = None) -> int:
	text = (note_text or "").strip()
	if not text:
		raise ValueError("Note text cannot be empty.")

	async with aiosqlite.connect(DB_PATH) as db:
		note_number = await _next_note_number(db)
		await db.execute(
			"INSERT INTO notes (note_number, note_text, taken_date) VALUES (?, ?, ?)",
			(note_number, text, taken_date or _today()),
		)
		await db.commit()
		return note_number


async def add_notes_batch(note_texts: List[str], taken_date: str = None) -> int:
	cleaned = [text.strip() for text in note_texts if isinstance(text, str) and text.strip()]
	if not cleaned:
		return 0

	async with aiosqlite.connect(DB_PATH) as db:
		note_number = await _next_note_number(db)
		for offset, text in enumerate(cleaned):
			await db.execute(
				"INSERT INTO notes (note_number, note_text, taken_date) VALUES (?, ?, ?)",
				(note_number + offset, text, taken_date or _today()),
			)
		await db.commit()
	return len(cleaned)


async def list_notes() -> List[Tuple[int, str, str]]:
	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"SELECT note_number, note_text, taken_date FROM notes ORDER BY note_number DESC"
		)
		return await cursor.fetchall()


async def delete_note_by_number(note_number: int) -> bool:
	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"DELETE FROM notes WHERE note_number = ?",
			(note_number,),
		)
		if cursor.rowcount <= 0:
			await db.rollback()
			return False

		await db.execute(
			"UPDATE notes SET note_number = note_number - 1 WHERE note_number > ?",
			(note_number,),
		)
		await db.commit()
		return True


async def search_notes(keyword: str) -> List[Tuple[int, str, str]]:
	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"""
			SELECT note_number, note_text, taken_date
			FROM notes
			WHERE LOWER(note_text) LIKE LOWER(?)
			ORDER BY note_number DESC
			""",
			(f"%{keyword}%",),
		)
		return await cursor.fetchall()


async def create_tag(tag_name: str) -> Optional[str]:
	normalized = normalize_tag_name(tag_name)
	if not normalized:
		return None

	async with aiosqlite.connect(DB_PATH) as db:
		await db.execute(
			"INSERT OR IGNORE INTO note_tags (normalized_name, created_at) VALUES (?, ?)",
			(normalized, _today()),
		)
		await db.commit()
	return normalized


async def delete_tag(tag_name: str) -> bool:
	normalized = normalize_tag_name(tag_name)
	if not normalized:
		return False

	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"DELETE FROM note_tags WHERE normalized_name = ?",
			(normalized,),
		)
		await db.commit()
		return cursor.rowcount > 0


async def list_tags() -> List[str]:
	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"SELECT normalized_name FROM note_tags ORDER BY normalized_name ASC"
		)
		rows = await cursor.fetchall()
		return [row[0] for row in rows]


async def list_tags_with_counts() -> Tuple[List[Tuple[str, int]], int]:
	tags = await list_tags()
	notes = await list_notes()

	counts: Dict[str, int] = {tag: 0 for tag in tags}
	untagged = 0
	for _, note_text, _ in notes:
		matched = match_note_to_existing_tag(note_text, tags)
		if matched is None:
			untagged += 1
		else:
			counts[matched] += 1

	return [(tag, counts[tag]) for tag in tags], untagged


async def list_notes_by_tag(tag_name: str) -> List[Tuple[int, str, str]]:
	normalized = normalize_tag_name(tag_name)
	if not normalized:
		return []

	notes = await list_notes()
	return [row for row in notes if note_matches_tag(row[1], normalized)]


async def delete_untagged_notes() -> int:
	"""Delete all notes that don't match any existing tag."""
	tags = await list_tags()
	notes = await list_notes()

	deleted = 0
	for note_number, note_text, _ in notes:
		matched = match_note_to_existing_tag(note_text, tags)
		if matched is None:  # Untagged note
			if await delete_note_by_number(note_number):
				deleted += 1

	return deleted


async def _next_persona_memory_number(db: aiosqlite.Connection, persona: str) -> int:
	cursor = await db.execute(
		"SELECT COALESCE(MAX(memory_number), 0) + 1 FROM persona_memories WHERE persona = ?",
		(persona,),
	)
	row = await cursor.fetchone()
	return int(row[0])


async def add_persona_memory(persona: str, memory_text: str) -> Optional[int]:
	persona_name = (persona or "").strip()
	text = (memory_text or "").strip()
	if not persona_name or not text:
		return None

	async with aiosqlite.connect(DB_PATH) as db:
		memory_number = await _next_persona_memory_number(db, persona_name)
		await db.execute(
			"""
			INSERT INTO persona_memories (persona, memory_number, memory_text, created_at)
			VALUES (?, ?, ?, ?)
			""",
			(persona_name, memory_number, text, _today()),
		)
		await db.commit()
		return memory_number


async def list_persona_memories(persona: str) -> List[Tuple[int, str, str]]:
	persona_name = (persona or "").strip()
	if not persona_name:
		return []

	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"""
			SELECT memory_number, memory_text, created_at
			FROM persona_memories
			WHERE persona = ?
			ORDER BY memory_number ASC
			""",
			(persona_name,),
		)
		return await cursor.fetchall()


async def load_persona_memory_texts(persona: str) -> List[str]:
	rows = await list_persona_memories(persona)
	return [row[1] for row in rows]


async def delete_persona_memory(persona: str, memory_number: int) -> bool:
	persona_name = (persona or "").strip()
	if not persona_name:
		return False

	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"DELETE FROM persona_memories WHERE persona = ? AND memory_number = ?",
			(persona_name, memory_number),
		)
		if cursor.rowcount <= 0:
			await db.rollback()
			return False

		await db.execute(
			"""
			UPDATE persona_memories
			SET memory_number = memory_number - 1
			WHERE persona = ? AND memory_number > ?
			""",
			(persona_name, memory_number),
		)
		await db.commit()
		return True


async def replace_persona_memory_by_text(persona: str, old_text: str, new_text: str) -> bool:
	persona_name = (persona or "").strip()
	old_value = (old_text or "").strip()
	new_value = (new_text or "").strip()
	if not persona_name or not old_value or not new_value:
		return False

	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"""
			UPDATE persona_memories
			SET memory_text = ?
			WHERE persona = ? AND memory_text = ?
			""",
			(new_value, persona_name, old_value),
		)
		await db.commit()
		return cursor.rowcount > 0


async def delete_persona_memory_by_text(persona: str, memory_text: str) -> bool:
	rows = await list_persona_memories(persona)
	target = (memory_text or "").strip()
	for memory_number, existing_text, _ in rows:
		if existing_text == target:
			return await delete_persona_memory(persona, memory_number)
	return False


async def add_moment(moment_text: str, created_at: str = None) -> Optional[int]:
	text = (moment_text or "").strip()
	if not text:
		return None

	await prune_expired_moments()
	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"INSERT INTO moments (moment_text, created_at) VALUES (?, ?)",
			(text, created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
		)
		await db.commit()
		return cursor.lastrowid


async def list_moments() -> List[Tuple[int, str, str]]:
	await prune_expired_moments()
	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"SELECT id, moment_text, created_at FROM moments ORDER BY id DESC"
		)
		return await cursor.fetchall()


async def recent_moments(limit: int = 5) -> List[Tuple[int, str, str]]:
	rows = await list_moments()
	return rows[:limit]


async def prune_expired_moments(max_age_days: int = 30) -> int:
	cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute(
			"DELETE FROM moments WHERE created_at < ?",
			(cutoff,),
		)
		await db.commit()
		return cursor.rowcount


async def clear_moments() -> int:
	async with aiosqlite.connect(DB_PATH) as db:
		cursor = await db.execute("DELETE FROM moments")
		await db.commit()
		return cursor.rowcount


try:
	asyncio.get_running_loop()
except RuntimeError:
	asyncio.run(init_db())

