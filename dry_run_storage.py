import asyncio
import importlib
import os
import tempfile


async def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_db = os.path.join(temp_dir, "lavbot_dry_run.db")
        os.environ["LAVENDER_MEMORY_DB"] = temp_db

        memory = importlib.import_module("memory")
        memory = importlib.reload(memory)

        await memory.init_db()

        created_tag = await memory.create_tag("Life Hack")
        assert created_tag == "life hack"

        first_note = await memory.add_note("Life hack keep your keys in the same tray every night")
        second_note = await memory.add_note("A normal untagged note for testing")
        assert first_note == 1
        assert second_note == 2

        tagged_notes = await memory.list_notes_by_tag("Life Hack")
        tag_counts, untagged_count = await memory.list_tags_with_counts()
        assert len(tagged_notes) == 1
        assert tagged_notes[0][0] == 1
        assert ("life hack", 1) in tag_counts
        assert untagged_count == 1

        memory_number = await memory.add_persona_memory("ally", "birthday is in september")
        assert memory_number == 1
        replaced = await memory.replace_persona_memory_by_text(
            "ally",
            "birthday is in september",
            "birthday is in late september",
        )
        assert replaced is True
        persona_memories = await memory.list_persona_memories("ally")
        assert len(persona_memories) == 1
        assert persona_memories[0][1] == "birthday is in late september"

        moment_id = await memory.add_moment("Dry-run health analysis report")
        moment_id2 = await memory.add_moment("Second moment for testing")
        moments = await memory.list_moments()
        assert moment_id is not None
        assert moment_id2 is not None
        assert len(moments) == 2
        assert moments[0][1] == "Second moment for testing"
        assert moments[1][1] == "Dry-run health analysis report"

        count = await memory.count_moments()
        assert count == 2

        deleted_moment = await memory.delete_moment_by_number(1)
        assert deleted_moment is True
        remaining_moments = await memory.list_moments()
        assert len(remaining_moments) == 1
        assert remaining_moments[0][1] == "Dry-run health analysis report"

        await memory.set_distillation("A calm day of coding and reflection.")
        distillation = await memory.get_distillation()
        assert distillation == "A calm day of coding and reflection."

        deleted_note = await memory.delete_note_by_number(1)
        remaining_notes = await memory.list_notes()
        assert deleted_note is True
        assert len(remaining_notes) == 1
        assert remaining_notes[0][0] == 1
        assert remaining_notes[0][1] == "A normal untagged note for testing"

        deleted_memory = await memory.delete_persona_memory("ally", 1)
        assert deleted_memory is True
        assert await memory.list_persona_memories("ally") == []

        deleted_tag = await memory.delete_tag("Life Hack")
        assert deleted_tag is True

        print("Dry run passed:")
        print("- tags can be created, counted, and deleted")
        print("- notes are numbered, tagged by prefix, and renumber after deletion")
        print("- persona memories can be added, replaced, listed, and deleted")
        print("- moments can be stored, listed, counted, and deleted by number")
        print("- distillation can be set and retrieved")
        print(f"- temp database used: {temp_db}")


if __name__ == "__main__":
    asyncio.run(main())
