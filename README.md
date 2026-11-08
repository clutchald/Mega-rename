# Personal MEGA Renamer Telegram Bot

A single-admin Telegram bot starter for browsing a MEGA account, renaming one selected item, and bulk-renaming video/image files directly in the MEGA cloud.

## Important compatibility note

This project uses the community `mega.py` Python package, which is an unofficial MEGA API client and is old. MEGA can change its private API at any time; login, listing, or rename operations may stop working without notice. Test with a few non-critical files first. This is not an official MEGA product.

## Features included

- Single Telegram admin allowlist (`ADMIN_ID`)
- MEGA email/password login
- Encrypted-at-rest saved MEGA password (Fernet key)
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
- Password is stored encrypted in SQLite. Losing `FERNET_KEY` makes the saved password unrecoverable.
- Use a private Telegram bot and set `ADMIN_ID` to your own numeric Telegram user ID.
- Use a persistent Koyeb Volume mounted at `/data`; without it, the SQLite database can be lost on redeploy/restart.
- Use a strong MEGA password and enable MEGA 2FA if supported by your account. The old `mega.py` client may not support every 2FA flow.

## Generate Fernet key

Run locally:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Save the output as the `FERNET_KEY` secret in Koyeb. Do not regenerate it after deployment unless you intend to re-enter credentials.

## Koyeb deployment

1. Upload this project to a private GitHub repository.
2. In Koyeb, create an App and deploy from that repository.
3. Choose Dockerfile builder (Dockerfile is included).
4. Add environment variables:
   - `BOT_TOKEN` — token from BotFather
   - `ADMIN_ID` — your numeric Telegram user ID
   - `FERNET_KEY` — generated key above
   - `DATA_DIR` — `/data`
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

## Known limitations / next work

- `/files` currently displays a limited top-level/sample listing rather than a full paginated folder browser.
- Selected-folder-only and whole-drive recursive scope selection need a robust folder traversal implementation for your MEGA account's returned node schema.
- Prefix, suffix, and replace commands are placeholders.
- Rename API compatibility must be verified against the current MEGA API and the specific account.
- Password is re-used for automatic login after process restart; the Fernet key is therefore critical.
