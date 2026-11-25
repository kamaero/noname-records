# Noname Records

Noname Records is a self-hosted workspace for full-cast audiobook production. Import a book, review speaker attribution, cast actors, collect recordings, compare takes with the script, and export a chapter project for Adobe Audition.

Your studio runs its own copy. AI services use your own provider keys and may charge for requests. Check provider terms, estimates and prices before starting a job. Book text and audio are sent only to the services you choose for that work. The software is free under the [AGPL-3.0 license](LICENSE).

**English** · [Русский](README.ru.md)

## What you can do

- Import `.txt`, `.docx`, `.fb2` and `.epub` books in English or Russian. Choose the book language when uploading; the interface is in English.
- Find characters, review speaker attribution in the reader, and organize the cast.
- Publish chapters to actors, collect takes and compare recordings with the script using speech recognition.
- Download chapter projects for Adobe Audition. Optional tools include sound and ambient work and Telegram notifications.
- Find scene music, environment audio and effects with Openverse or YouTube. Preview candidates beside the script, confirm rights before downloading, and maintain chapter credits. Files are added to Audition manually.

A narrator take starts a working chapter draft after its intake and transcription finish. Missing character voices, separate titles and script lines remain explicit project gaps; they do not delay the first draft. Automatic revisions keep earlier project files and verified media intact, including manual edits. Without a NAS, the installation stores drafts beside its local audio; configured NAS installations store drafts there. The ASR table reports the current saved draft and narrator-check result. Administrators can explicitly start a full paid narrator check with the circular arrow, or resume an eligible failed check using its saved full transcription. Resume reuses completed text-review verdicts when their inputs still match; remaining text review and short independent confirmation transcriptions may still incur charges. Both actions require payment confirmation.

If strict narrator checking is enabled, assembly queues it under your configured provider keys and spending limit. Lexical substitutions and incorrect word forms are checked alongside omissions; a second transcription must confirm a finding before it becomes a project marker. Short neutral dialogue tags may be wholly or partly omitted. A spoken substitution must agree in both independent transcriptions and pass the text review before an actor receives a notice. Notices show the source filename, approximate source time, expected word, heard word and script context; mandatory script gaps remain in project markers. Automatic assembly audits update the owner report and project markers without broadcasting retrospective notices to actors. An unknown previous payment outcome stops automatic paid retries and needs owner review.

Assigned narrators can review confirmed recording findings on their role script page and download Audacity labels as TXT without audio, or the original WAV with matching labels as ZIP. Label times refer to the named original recording, not the chapter mix.

AI results need human review. Provider availability, model quality and costs depend on your configuration.

## Requirements

- Mac or Windows with Docker Desktop and at least 8 GB of memory, or a Linux server with Docker Compose.
- Internet access for installation and external AI services.
- API keys for the providers you choose; enter them during setup or in **Settings → AI providers**.
- A domain and HTTPS for remote collaborators or the optional Telegram bot.

The budget screen currently records expenses and limits in RUB. An unknown price is not a free request and may not be included in the limit. Review the estimate and provider settings before paid work.

## Install on Mac

1. Install and start [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/kamaero/noname-records/main/get.sh | bash
```

3. Follow the prompts, set administrator credentials and select local mode. Keys can be added later.
4. Open [http://localhost:8080](http://localhost:8080) and sign in.

## Install on Windows

Install and start Docker Desktop with WSL 2 enabled, then run in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/kamaero/noname-records/main/get.ps1 | iex"
```

Follow the prompts and open [http://localhost:8080](http://localhost:8080).

## Install on a server

Point your domain's DNS A record at the Linux server, connect to it, and replace `studio.example.com` with your domain:

```bash
curl -fsSL https://raw.githubusercontent.com/kamaero/noname-records/main/get.sh | bash -s -- --domain studio.example.com
```

The installer configures Docker and HTTPS. Sign in at your domain. The optional Telegram bot requires your own bot token and webhook secret; keep these private.

## Get started

1. Sign in and configure keys and models under **Settings → AI providers**.
2. Add the sample book or upload a book; review the estimate before starting analysis.
3. Review attribution in the reader, correct uncertain passages and organize the cast.
4. Create actor accounts, publish chapters and collect recordings.
5. Review ASR results and download the chapter project for mixing.

## Separate chapter title voice

An administrator can assign a separate actor to a book's headings. Run `docker compose exec web python scripts/configure_chapter_titles.py --book-id BOOK_ID --actor "Full Actor Name"` to preview the change, then repeat with `--apply`. Back up the database before applying. Existing paid transcripts are realigned locally without sending actor notices or paying for transcription again.

The assigned actor sends `BookCode_Главы_Actor.wav` or `.flac` to the Telegram bot. The book code must be unique; the actor must have an active linked account. An unambiguous surname or the full actor name is accepted. Administrators and agents can send the assigned actor's recording on their behalf. There is no website upload form for this role.

All heading segments form each chapter title. Recognized repeats are kept, including a return to an earlier chapter. Repeated subtitles without a chapter number and uncertain pronunciations are retained as review clips; they do not prove full heading coverage. Zero-length punctuation and event tags do not discard other valid takes, while words with unknown duration remain explicit gaps for review. Audition exports put complete title takes consecutively on the separate `Названия глав` track before the chapter body. In chapters with a separate opening title, audio starts with three seconds of silence. The opening ambient clip fades in from 0:03 to 0:10 at a target clip gain of −12 dB. The title starts at 0:10 and the chapter body follows 2.5 seconds after the last title take. Missing or ambiguous titles stay visible for studio review. Source recordings and paid ASR checkpoints are retained in `data/spool/bot_takes`; include this directory in backups. Resending the same file resumes a failed or incomplete upload with its existing source and paid cache when the actor, headings and recognition settings are unchanged. A request with an unknown payment outcome requires manual review before a paid retry.

## Import scene alternatives A/B

You can supply two already-generated PCM16 mono/stereo WAVs for an active scene. No generator, model files or external API key is needed for this import. The regular ElevenLabs BYOK generation remains available.

After updating the installation (database migration `0071_ambient_pairs`), copy your WAVs into a directory mounted in the web container. List scenes with `docker compose exec web python scripts/import_ambient_pair.py --chapter-id CHAPTER_ID --list-scenes`. Validate a pair with `docker compose exec web python scripts/import_ambient_pair.py --chapter-id CHAPTER_ID --marker-id MARKER_ID --a /path/to/A.wav --b /path/to/B.wav`; add `--apply` to save it. Back up the database and audio before applying, as with other administrator imports.

New chapter exports place both variants at their scene start, limited to the scene length: A on **Ambient 1**, B on **Ambient 2** with B muted. Switch mute states in Audition to compare them. Both WAVs travel with the chapter archive. Earlier media and manually edited projects are retained; export a new project to receive the pair. Reimporting the same pair reuses it. Replacing a pair keeps its previous media; editing a scene or changing the book language invalidates its imported pair until you import a matching pair again. Legacy scenes keep their existing generation and assembly behavior; in chapters mixing imported pairs with legacy audio, legacy audio remains audible on Ambient 1.

This portable path imports existing audio. It does not automatically plan or generate alternatives, and it does not connect to a studio's private generation infrastructure.

## Maintenance

Run commands from the installation directory (`~/noname-records` on Mac/Linux or `$HOME\noname-records` in Windows PowerShell):

| Task | Mac / Linux | Windows PowerShell |
|---|---|---|
| Update | `./update.sh` | `powershell -ExecutionPolicy Bypass -File update.ps1` |
| Back up | `./backup.sh` | `powershell -ExecutionPolicy Bypass -File backup.ps1` |
| Stop / start | `docker compose stop` / `docker compose up -d` | same |
| View logs | `docker compose logs --tail 100 web worker` | same |

Updates create a backup first. Keep another copy away from the server. The `data` directory contains the database and recordings; `.env` contains configuration and secrets.

If Docker is unavailable, start Docker Desktop and wait until it is ready. If the server is unreachable, check DNS and HTTPS; after correcting DNS, restart Caddy with `docker compose restart caddy`. Generate an administrator password hash with `docker compose run --rm web python scripts/generate_password_hash.py`. For Telegram webhook status, run `docker compose exec web python -m scripts.telegram_set_webhook --info`.

## Development

Python 3.12, FastAPI, SQLAlchemy, Alembic, SQLite, RQ and Redis; React 18, TypeScript and Vite.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
cd frontend && npm ci && npm run build
```

Before publication, run `scripts/release_audit.py --tree --history --release` with the required read-only people and book databases. Never publish private books, recordings, names or secrets.

## License

[GNU AGPL-3.0](LICENSE).

## Narrator chapter replacements

A narrator chapter recording marked `_fix` is a full replacement by default for new uploads. Its filename must explicitly name the chapter, for example `Sample_Chapter1_Narrator_fix.wav`. On the website, choose **Short correction only** for a few corrected lines; in Telegram, choose **Full chapter replacement** or **Short correction only** before processing. A full replacement excludes every earlier narrator source in that chapter. Lines absent from the replacement remain visible as gaps. Later short corrections supplement that replacement. Original files, paid transcripts and previous chapter projects are retained. Historical uploads preserve their previous short-correction behavior. Other roles and separate title recordings keep their current behavior.
