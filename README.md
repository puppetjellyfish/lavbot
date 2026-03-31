# lavbot
A simple discord diary &amp; notetaking companion.

This is a simple discord bot I made to help me manage diary. start_lavbot.bat will start the TUI where you can hook it up with your discord bot and then use !guji in discord to see her capabilities.

Lavender is named after a plush sheep I have at home.

Yes this is vibe coded by Github Copilot, with the help of Muggy8.

Yes I'm Muggy8's gf.

## Data Storage

All runtime user data is stored in `lavuserdata/` inside the main `lavbot` folder.

This includes:
- `lavuserdata/user.db`
- `lavuserdata/.env`
- `lavuserdata/lavender_memory/`
- `lavuserdata/lavender_images/`
- `lavuserdata/self_image/`
- `lavuserdata/favorites.json`

The `lavuserdata/` folder is ignored by git so local user data is not pushed to GitHub.
