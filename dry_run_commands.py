"""dry_run_commands.py — verify note/tag command logic, memory phrase detection,
and subject-aware memory resolution without a live Discord bot connection.

Run with: python dry_run_commands.py
"""
import os
import sys
import tempfile

# Redirect both databases to a temp dir BEFORE importing any lavbot modules.
# This ensures no test data touches the real databases.
_temp_dir = tempfile.mkdtemp()
os.environ["LAVENDER_MEMORY_DB"] = os.path.join(_temp_dir, "cmds_dry_run_memory.db")
os.environ["LAVENDER_USER_DB"] = os.path.join(_temp_dir, "cmds_dry_run_user.db")

import asyncio  # noqa: E402
import shutil  # noqa: E402

import memory  # noqa: E402  (picks up LAVENDER_MEMORY_DB before DB_PATH is evaluated)
import bot as _bot  # noqa: E402  (imports memory functions after env var is set)

# Patch persona lookup so the test doesn't need a configured user.db.
_bot.get_persona_for_user = lambda uid: "testpersona"


async def main() -> None:
    persona = "testpersona"

    # ── 1. Note & tag pipeline ────────────────────────────────────────────────
    await memory.create_tag("recipe")
    n1 = await memory.add_note("recipe:chocolate cake with 2 cups flour")
    n2 = await memory.add_note("recipe:banana bread with honey and walnuts")
    n3 = await memory.add_note("Call the vet on Thursday")
    assert n1 == 1 and n2 == 2 and n3 == 3, f"Note numbering wrong: {n1}, {n2}, {n3}"

    tagged = await memory.list_notes_by_tag("recipe")
    assert len(tagged) == 2, f"Expected 2 tagged notes, got {len(tagged)}"

    all_notes = await memory.list_notes()
    assert len(all_notes) == 3, f"Expected 3 total notes, got {len(all_notes)}"

    results = await memory.search_notes("chocolate")
    assert any("chocolate" in (t or "").lower() for _, t, _ in results), "Note search for 'chocolate' failed"

    # Delete note #1 and verify renumbering
    await memory.delete_note_by_number(1)
    remaining = await memory.list_notes()
    assert len(remaining) == 2, f"Expected 2 notes after delete, got {len(remaining)}"
    # list_notes returns DESC order, so the lowest note number is the last element
    min_num = min(r[0] for r in remaining)
    assert min_num == 1, f"Renumbering failed; lowest note number after delete is {min_num}"

    await memory.delete_tag("recipe")
    tag_counts, _ = await memory.list_tags_with_counts()
    assert not any(name == "recipe" for name, _ in tag_counts), "Tag 'recipe' should be gone after delete"

    # ── 2. Memory phrase detection (remember / forget / change) ──────────────
    await memory.add_persona_memory(persona, "her birthday is march 5th")
    await memory.add_persona_memory(persona, "her favorite food is ramen")
    await memory.add_persona_memory(persona, "she works as a nurse")

    # remember
    status, reply, handled = await _bot.process_explicit_memory_request(
        "please remember that she has a cat named mochi", 99999
    )
    assert handled and status, f"remember phrase not handled (status={status!r}, reply={reply!r})"
    mems = await memory.list_persona_memories(persona)
    assert any("mochi" in (t or "").lower() for _, t, _ in mems), "remember phrase did not save memory"

    # forget
    status, reply, handled = await _bot.process_explicit_memory_request(
        "forget what you remember about nurse", 99999
    )
    assert handled and status, f"forget phrase not handled (status={status!r}, reply={reply!r})"
    mems = await memory.list_persona_memories(persona)
    assert not any("nurse" in (t or "").lower() for _, t, _ in mems), "forget phrase did not remove memory"

    # change
    status, reply, handled = await _bot.process_explicit_memory_request(
        "change what you remember about ramen to her favorite food is now sushi", 99999
    )
    assert handled and status, f"change phrase not handled (status={status!r}, reply={reply!r})"
    mems = await memory.list_persona_memories(persona)
    assert any("sushi" in (t or "").lower() for _, t, _ in mems), "change phrase did not update memory with sushi"
    assert not any("ramen" in (t or "").lower() for _, t, _ in mems), "old 'ramen' memory still present after change"

    # duplicate remember — should acknowledge without adding a second copy
    status, reply, handled = await _bot.process_explicit_memory_request(
        "please remember that she has a cat named mochi", 99999
    )
    assert handled and status, "duplicate remember phrase not handled"
    mems_after = await memory.list_persona_memories(persona)
    mochi_count = sum(1 for _, t, _ in mems_after if "mochi" in (t or "").lower())
    assert mochi_count == 1, f"Duplicate memory was added; 'mochi' appears {mochi_count} times"

    # ── 3. Subject-aware resolution ───────────────────────────────────────────
    mems_now = await memory.list_persona_memories(persona)

    # "birthday" should match "her birthday is march 5th" via subject alias
    resolved = _bot.resolve_memory_reference(mems_now, "birthday")
    assert resolved is not None, "Subject 'birthday' did not resolve to a memory"
    assert "birthday" in resolved[1].lower(), f"Wrong memory resolved for 'birthday': {resolved[1]}"

    # "pronouns" — no memory with pronoun aliases; should return None cleanly
    resolved_none = _bot.resolve_memory_reference(mems_now, "pronouns")
    assert resolved_none is None, "Expected None when no pronoun memory exists"

    # "favorite food" should resolve to the sushi memory (was ramen, now updated)
    resolved_food = _bot.resolve_memory_reference(mems_now, "favorite food")
    assert resolved_food is not None, "Subject 'favorite food' did not resolve"
    assert "sushi" in resolved_food[1].lower(), f"Expected sushi memory for 'favorite food', got: {resolved_food[1]}"


try:
    asyncio.run(main())
    print()
    print("Command dry run passed:")
    print("  - notes number correctly, tag prefix works, search finds content, delete renumbers")
    print("  - 'recipe' tag creates, prefixes notes, and deletes cleanly")
    print("  - remember / forget / change phrases intercept and apply correctly")
    print("  - duplicate remember detected without adding a second copy")
    print("  - subject-aware resolution: 'birthday' and 'favorite food' resolve by alias")
    print("  - 'pronouns' (absent) returns None cleanly")
    print(f"  - temp databases used: {_temp_dir}")
finally:
    shutil.rmtree(_temp_dir, ignore_errors=True)
