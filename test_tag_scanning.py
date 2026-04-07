import memory


def test_scan_messages_for_tags_matches_existing_tags_only():
    messages = [
        "recipe: chocolate cake",
        "just chatting here",
        "TODO buy oat milk",
        "recipe - banana bread",
        "untagged reminder",
    ]

    matched, counts = memory.scan_messages_for_tags(messages, ["recipe", "todo"])

    assert matched == [
        "recipe: chocolate cake",
        "TODO buy oat milk",
        "recipe - banana bread",
    ]
    assert counts == {"recipe": 2, "todo": 1}


def test_scan_messages_for_tags_prefers_longest_matching_tag():
    messages = ["favorite food sushi", "fav quick note"]

    matched, counts = memory.scan_messages_for_tags(messages, ["fav", "favorite food"])

    assert matched == ["favorite food sushi", "fav quick note"]
    assert counts == {"fav": 1, "favorite food": 1}
