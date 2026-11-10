# Personal MEGA Renamer Telegram Bot

A single-admin Telegram bot starter for browsing a MEGA account, renaming one selected item, and bulk-renaming video/image files directly in the MEGA cloud.

## Important compatibility note

This project uses the community `mega.py` Python package, which is an unofficial MEGA API client and is old. MEGA can change its private API at any time; login, listing, or rename operations may stop working without notice. Test with a few non-critical files first. This is not an official MEGA product.

## Features included

- Single Telegram admin allowlist (`ADMIN_ID`)
- MEGA email/password login
- MEGA credentials are not persisted; login is memory-only
- Browse a limited listing of drive items
- Select one item and rename it
- Bulk rename video files, image files, or both
- Separate counters for videos and images: `Base (1).mp4`, `Base (1).jpg`
- Preview and explicit confirmation before bulk changes
- Search filenames

Bulk rename currently scans the node listing returned by `mega.py`. The folder scope UI and advanced prefix/suffix/find-replace workflows are not implemented in this starter yet. Bulk selection currently targets the drive listing; recursive folder behavior depends on the library's node metadata and must be tested on your account.

## Security

- Never share your MEGA password, Telegram bot token, or Fernet key.
- The bot attempts to delete the Telegram password message after receiving it, but Telegram deletion can fail; use a private chat and delete it manually if it remains visible.
- Password is stored encrypted in SQLite. MEGA login is memory-only and must be repeated after a restart.
- Use a private Telegram bot and set `ADMIN_ID` to your own numeric Telegram user ID.
- MongoDB stores the account email and can be hosted on MongoDB Atlas; a Koyeb volume is not required for that data.
- Use a strong MEGA password and enable MEGA 2FA if supported by your account. The old `mega.py` client may not support every 2FA flow.

## MongoDB and credential handling

This version uses MongoDB to store the Telegram admin's MEGA account email. It does NOT store the MEGA password or a reusable MEGA session. The live MEGA login remains in memory only. After a Koyeb restart or redeploy, use `/login` again. Do not store the MEGA password as plaintext in MongoDB.

## Koyeb deployment

1. Upload this project to a private GitHub repository.
2. In Koyeb, create an App and deploy from that repository.
3. Choose Dockerfile builder (Dockerfile is included).
4. Add environment variables:
   - `BOT_TOKEN` — token from BotFather
   - `ADMIN_ID` — your numeric Telegram user ID
   - `MONGODB_URI` — your MongoDB Atlas connection string\n   - `MONGODB_DATABASE` — `mega_renamer_bot` (or your preferred database name)\n   - `DATA_DIR` — `/data`
5. Attach a persistent Koyeb Volume and mount it at `/data`.
6. Use one instance/replica to avoid multiple polling workers using the same bot token.
7. Deploy and inspect logs.

The container runs `python main.py`. For buildpack deployment, the included Procfile has `worker: python main.py`.

## Commands

- `/start`
- `/login`
- `/files`
- `/rename` (select an item first from `/files`)
- `/bulk`
- `/search keyword`
- `/prefix`
- `/suffix`
- `/replace`
- `/help`
- `/cancel`

## Bulk rename behavior

Choose Videos, Images, or Videos + Images. Send a base name, e.g. `Archive`. Preview is shown before confirmation.

Example:
- `clip.mp4` -> `Archive (1).mp4`
- `photo.jpg` -> `Archive (1).jpg`
- `movie.mkv` -> `Archive (2).mkv`

Video and image counters are separate. Existing extensions are preserved. Bulk rename is irreversible at the MEGA level; keep backups and test on a small set first.

## MongoDB setup\n\n1. Create a MongoDB Atlas cluster (or use your own MongoDB server).\n2. Create a database user and allow network access from your Koyeb service (for Atlas, configure an appropriate IP access rule).\n3. Copy the MongoDB connection string into the Koyeb secret `MONGODB_URI`.\n4. Set `MONGODB_DATABASE=mega_renamer_bot`.\n5. Keep the connection string private.\n\n## Known limitations / next work

- `/files` currently displays a limited top-level/sample listing rather than a full paginated folder browser.
- Selected-folder-only and whole-drive recursive scope selection need a robust folder traversal implementation for your MEGA account's returned node schema.
- Prefix, suffix, and replace commands are placeholders.
- Rename API compatibility must be verified against the current MEGA API and the specific account.
- Password is not stored. Re-login after a process restart.


## Koyeb HTTP health check

This version starts an aiohttp health server alongside Telegram long polling. Koyeb can check `GET /health` on the service port. The app listens on `0.0.0.0:$PORT` (defaults to `8000`). Configure the Koyeb health check as HTTP on path `/health` and port `8000` (or the service port configured by Koyeb). The Procfile uses `web: python main.py`; the Dockerfile starts the same command.

On SIGTERM/SIGINT, the app stops Telegram polling, closes the HTTP health server, and closes the MongoDB client.
