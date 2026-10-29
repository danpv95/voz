# voz

![license MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![python 3.12](https://img.shields.io/badge/python-3.12-3776AB.svg)
![platform macOS Apple Silicon](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-lightgrey.svg)
![version 0.1.0](https://img.shields.io/badge/version-0.1.0-informational.svg)

Website: [voz.vercel.app](https://voz.vercel.app) (what it looks like, how to install it and what changed in each version) <!-- URL-WEB -->

[Español](README.md) · **English**

Local voice dictation for the terminal. You talk, `voz` transcribes on your Mac with [mlx-whisper](https://pypi.org/project/mlx-whisper/) and leaves the text on your clipboard, ready to paste into Claude Code, your editor, or anywhere else. Your voice never touches a server.

## Why it exists

We wanted to dictate to a coding agent without sending audio to the cloud and without paying for yet another subscription. Talking is a lot faster than typing long prompts, but most of the comfortable options are hosted services.

`voz` does one thing: it turns your voice into text and puts it on the clipboard. That is the whole integration. There is no plugin or API to maintain, you just hit `Cmd-V` wherever you need the text. It also transcribes audio files (WhatsApp or Telegram voice notes, recorded meetings).

## Who it is for

People who live in the terminal on a Mac with Apple Silicon (M1 or later) and want to dictate without their voice leaving the machine. We use it mostly in Spanish, and with `--lang auto` it keeps the session's language: it starts with your primary language (Spanish by default, see [Primary language](#primary-language)) and only switches on strong evidence. Mixing Spanish and English in one session still works when the change is clear and you pause between them.

**Status: v0.1.0.** It works and we use it every day, but it is an early release with known rough edges (see [Limitations](#limitations)). macOS on Apple Silicon only, for now.

## What a session looks like

This is an illustration of the flow. Timings and text change with what you say. (The interface is in Spanish for now.)

```
$ voz
voz  ·  turbo  ·  auto  ·  🎙 MacBook Pro Microphone
⠋ Cargando modelo… 3 s   esc salir

● Listo   ↵ hablar · esc salir
  habla con pausas cortas: voz transcribe mientras hablas · voz --help para más

        (you press ↵ and start talking)

● Escuchando  00:07  ·····▃▅▃··  es   ↵ terminar · p pausa · esc descartar
  Necesito que revises el módulo de facturación antes del viernes
  y separa el texto parcial del confirmado

        (you press ↵ to finish)

⠋ Terminando… 2 s · 1 tramo pendiente

── grabación 1 ─────────────────────────────────────────────
Necesito que revises el módulo de facturación antes del viernes y separa el texto parcial del confirmado.

✓  Copiado (reemplazó tu portapapeles)  ·  7 s  ·  17 palabras  ·  es
↵ salir · Cmd-V para pegar   r grabar otra   c añadir   ⌫ descartar
```

The header shows the model in short form (`turbo`, or `large-v3` with `--accurate`) and the system's default input microphone. The gray hint under "Listo" ("speak with short pauses: voz transcribes while you talk") appears only the first time. On a narrow terminal (under about 60 columns) the keys move to a line of their own and the meter is cut first: the keys never disappear.

The meter tells you whether `voz` is hearing you: in silence it draws faint dots (`·`); when it detects voice it draws colored bars (`▁▂▃▅▇`). If you talk and only see dots, check the microphone with `voz doctor --mic`. "Cargando modelo… 3 s" is the model loading with a counter, and "Terminando… 2 s · 1 tramo pendiente" is `voz` finishing the last chunk (1 chunk pending).

While you talk, confirmed text shows up in your terminal's normal color (readable on light and dark backgrounds) and the provisional guess for the chunk in progress in a dim tone. When you finish, the text is already on the clipboard. The everyday loop is `voz`, `↵`, talk, `↵`, `↵`, then `Cmd-V`.

## Install

You need a Mac with Apple Silicon, `python3.12`, and [Homebrew](https://brew.sh) (to install Python and, if you want to transcribe files, `ffmpeg`; mic dictation does not use it).

**0. Check your Mac and Python**

```bash
uname -m                 # must say arm64 (Apple Silicon)
python3.12 --version     # if it is missing: brew install python@3.12
```

**1. Clone and create the environment**

```bash
git clone https://github.com/danpv95/voz voz
cd voz
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.lock.txt
```

`requirements.lock.txt` pins the versions this release was tested with, so the install comes out the same on any machine. `requirements.txt` only asks for minimum versions and takes the latest available: use it if you prefer to stay current.

The environment is about 1.1 GB (mlx-whisper pulls in torch).

**2. Put the command on your PATH**

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/bin/voz" ~/.local/bin/voz
```

If `which voz` finds nothing, add `~/.local/bin` to your PATH and reload the shell:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Do not delete or move the cloned folder: `voz` lives there.** The command is a link to `bin/voz` and uses that folder's `.venv`. If you move it, repeat this step.

**3. Check that everything is fine**

```bash
voz doctor          # checks the install, no network
brew install ffmpeg # only if you will transcribe files
```

`voz doctor` tells critical problems from optional ones: without `ffmpeg` it shows `⚠ ffmpeg no instalado: el dictado funciona; para transcribir archivos: brew install ffmpeg` (not installed: dictation works; to transcribe files, `brew install ffmpeg`), and it says whether each model is available, incomplete (weights missing) or not downloaded.

The first time, `voz` downloads the `whisper-large-v3-turbo` model (about 1.6 GB) to `~/.cache/huggingface/` and shows it in the interface itself: `Descargando modelo · 420 MB / ~1,6 GB · esc salir` (downloading model), then `Cargando modelo… 3 s`. If the download reports no byte counts you see `Descargando modelo (~1,6 GB, solo la primera vez)… 12 s` instead, with no invented percentage. That is the only time `voz` uses the network; if it is cut off, the next run continues. After that it runs offline. (`--accurate` downloads another ~3 GB the first time you use it.)

**Microphone permission.** macOS grants it to your terminal app (Terminal, iTerm, whichever you use), not to `voz`. If no audio arrives, `voz` says so without an error trace: `✖ No llega audio del micrófono` (no audio from the microphone), with the path System Settings → Privacy & Security → Microphone, and `↵` retries (it looks the default device up again, handy if you plug in another microphone). The plain flow (`VOZ_UI=simple`, `--no-live`) shows "Silencio detectado" with the same path.

## Usage

Run `voz` in a separate terminal tab, not inside your agent's session: it needs an interactive terminal for the microphone.

```bash
voz                         # dictate from the mic (↵ starts, ↵ stops)
voz --lang es               # force Spanish (default auto: keeps the session's language)
voz --accurate              # larger model: slower, more precise (downloads ~3 GB once)
voz --out notes.md          # also append the text to a file

voz file.m4a                # transcribe a file (m4a, ogg, opus, mp3, wav)
voz meeting.m4a --srt       # also write meeting.srt with timestamps
voz meeting.m4a --json      # and/or meeting.json with the segments
voz note.ogg --no-copy > note.txt   # stdout only, clipboard untouched

voz doctor                  # offline diagnostics (voz doctor --mic records 1 s)
voz devices                 # list audio devices
voz --version
```

`voz` prints only the transcript on stdout. Everything else (status, warnings) goes to stderr, so `voz file.m4a | pbcopy` or a redirect stay clean. `--srt` and `--json` never overwrite: if the file exists they write `meeting-1.srt`, `meeting-2.srt`, and so on.

Main flags:

| Flag | What it does |
|---|---|
| `--lang es\|en\|auto` | Language. `auto` (default) keeps the session's language: it starts with your primary language (Spanish if it cannot tell) and only switches on strong evidence. For calls or several voices, `--lang es` is the safer choice |
| `--accurate` | Uses `whisper-large-v3-mlx` instead of turbo |
| `--model REPO` | Any other mlx-whisper model (cannot be combined with `--accurate`) |
| `--out PATH` | Appends the text to that file |
| `--no-copy` | Leaves the clipboard alone |
| `--loop` | For mic use without the interactive UI: repeats without reloading the model |
| `--no-live` | Records everything and transcribes at the end, no live text |
| `--no-trim` | With `--no-live`, keeps the silence at the edges |
| `-y` | In the plain flow, copies without asking (the interactive UI follows `config.toml`) |

`voz --help` shows all of this in Spanish, with the keys and the config paths.

The full reference lives in the [guide](docs/guia-voz.pdf) (source: [`docs/guia-voz.html`](docs/guia-voz.html)). The guide is in Spanish.

## Live mode keys

| Moment | Keys |
|---|---|
| Ready | `↵` or space: talk · `esc`: quit |
| Listening | `↵` or space: finish · `p`: pause · `esc`: finish and discard (can be undone) |
| Paused | `p`: resume · `↵`: finish · `esc`: finish and discard |
| Review | `↵`: quit (the text is already copied: `Cmd-V` to paste) · `r`: record another · `c`: add to this one · `⌫`: discard · `u`: undo · `s`: copy (only if you chose manual copy) · `esc`: quit |
| No voice | `r`: retry · `↵` or `esc`: quit |
| No microphone audio | `↵` or `r`: retry · `esc`: quit |
| Quitting with several takes | `↵`: keep the last one copied · `a`: copy all together · `esc`: quit without touching the clipboard |

While the model loads you see `⠋ Cargando modelo… 3 s` with a counter, and a `↵` pressed then is ignored with the notice "↵ ignorado: el modelo todavía está cargando" (ignored: the model is still loading). If the load fails, you see the cause right there. Discarding never deletes the audio from disk. `Ctrl-C` is a safe exit: it finishes what is being recorded, leaves the last valid text copied (per your config) and always exits with code 130, with or without text. It says what really happened: `Interrumpido · texto copiado` (interrupted, text copied), `Interrumpido · audio guardado en <path>` (audio saved) or `Cancelado` (nothing had been recorded), and never promises a backup that failed. When finishing, `voz` waits up to 20 s for the pending text; if it does not arrive, or you press `Ctrl-C` again, it leaves anyway: the audio is saved, the session is marked incomplete and `voz` tells you how to re-transcribe it (the next `voz` reminds you too). If some chunk could not be transcribed, the review does not pretend success: it shows `⚠ Texto incompleto · 2 tramos sin transcribir · copiado` (incomplete text, 2 chunks not transcribed, copied) with the command to transcribe the whole audio; if every chunk failed it says `✖ No se pudo transcribir · el audio quedó guardado` (could not transcribe, audio saved) instead of "No se detectó voz". Pasting text into the `voz` terminal does not trigger keys.

If your terminal draws the interface badly, run `VOZ_UI=simple voz`. If you pipe `voz` or run it without an interactive terminal, the plain flow is used too.

**When to copy.** By default each recording is copied automatically. You can change that in `~/.config/voz/config.toml`:

```toml
[portapapeles]
copiar = "auto"        # auto (default) | preguntar | nunca
```

(The keys are in Spanish: `copiar` means copy, and the values are `auto`, `preguntar` = ask, `nunca` = never.)

### Primary language

With `--lang auto`, `voz` leans towards a primary language: it uses it for the first chunk unless the other language is clear, and going back to it takes less evidence than leaving it. It picks it like this, top to bottom:

1. `[idioma] principal` in `config.toml` (`"es"` or `"en"`):

   ```toml
   [idioma]
   principal = "en"       # es | en
   ```

2. `LC_ALL` or `LANG`, only when they say Spanish (`es_*`). An `en_US` inherited from the terminal is very common and proves nothing, so it does not decide on its own.
3. `AppleLocale` (macOS): if the **region** belongs to a Spanish-speaking country (Argentina, Bolivia, Chile, Colombia, Costa Rica, Cuba, Ecuador, El Salvador, Spain, Guatemala, Equatorial Guinea, Honduras, Mexico, Nicaragua, Panama, Paraguay, Peru, Puerto Rico, Dominican Republic, Uruguay, Venezuela), the primary language is Spanish even when the menus are in English (`en_CO`). Otherwise, the locale's own language when it is `es` or `en` (`en_US` gives English).
4. An `en` from `LANG` if nothing else answered, and finally Spanish.

## Corrections glossary

Whisper mishears proper nouns and technical terms it has not met in Spanish. Instead of pushing it with a prompt (which it repeats back on silence), `voz` fixes the text afterwards with a dictionary of your own.

It lives in `~/.config/voz/corrections.toml`. There is a starting point:

```bash
mkdir -p ~/.config/voz
cp config/corrections.example.toml ~/.config/voz/corrections.toml
```

Each line is `"what Whisper writes" = "what you meant"`:

```toml
[replacements]
"supa base" = "Supabase"
"cloud code" = "Claude Code"
```

House rules: proper nouns and technical terms only, never common words (pairs like poner/poder are acoustic mistakes and would break legitimate sentences). Keys ignore case but not accents. Minimum 3 characters. Longer keys win over shorter ones and rules run in a single pass. A rule that only changes capitalization is valid: it normalizes every variant of the word (`nit` becomes `NIT`).

## Privacy

- Audio and text are processed on your machine. No accounts, no keys, no telemetry.
- `bin/voz` starts with `HF_HUB_OFFLINE=1` and turns off Hugging Face telemetry: with the model cached, `voz` opens no connections. The one exception is the very first model download, which `voz` announces.
- While you record, the audio and each chunk of text are saved to `~/.local/state/voz/` (the last 5 sessions; an hour of audio is about 115 MB). It is a safety net: if something interrupts the session, what you recorded is still there and the next `voz` tells you how to recover it. The folder is yours alone (mode `0700`, files `0600`, whatever your `umask`). If you do not want those files, delete the folder whenever you like.

## Limitations

What we know does not work well, no sugarcoating:

- **Apple Silicon Macs only.** The engine (MLX), the clipboard (`pbcopy`) and audio capture depend on macOS.
- **Spelling is poor.** Acronyms, single letters, paths and keys come out however Whisper decides to write them. This version no longer deletes repeated digits or letters, but it does not recognize them any better.
- **It cannot tell speakers apart.** Everything comes out as one text.
- Mixing languages inside one sentence, with no pause, confuses Whisper. In `auto` it only chooses between Spanish and English.
- **Calls or several voices: fix the language.** In `auto`, distant audio or several voices can confuse the detector. The session is sticky and only switches on strong evidence, but for calls or several voices `--lang es` (or `--lang en`) is the safer choice.
- With the microphone, a lone "gracias" is kept when you really spoke: it is dropped only if the audio is silence (a typical Whisper hallucination). In files, an isolated "gracias" is dropped, to be safe.
- With strong background noise (10 dB signal-to-noise or worse) the pause detector fails.
- The glossary is accent-sensitive: `nequi` and `néqui` are different rules.
- It does not capture system audio (for example an online meeting): only the microphone.

## Roadmap

`voz` is the project; today it has a single face, the terminal CLI. The others build on the same core (record, transcribe, clean up, glossary).

1. **v0.1.1**: accent-insensitive glossary, UTF-8 clipboard environment, `--lang` validation, an optional sound when done, an `e` key to fix a word and remember it, an `i` key to change the language and reprocess, a primary language learned from use, an optional vocabulary hint and CI on GitHub Actions. Details in [CHANGELOG.md](CHANGELOG.md).
2. **Windows and Linux**: an alternative engine (for example faster-whisper) behind the same interface, plus cross-platform clipboard and keyboard handling. If you want to help, start with [CONTRIBUTING.md](CONTRIBUTING.md).
3. **macOS menu bar app**, someday: dictate into any app with a global shortcut.
4. **`voz habla`**, someday: the reverse direction, text to speech.

No dates. It is a personal project and moves in bursts.

## Contributing

Issues and pull requests are welcome, in English or Spanish. Before opening a PR read [CONTRIBUTING.md](CONTRIBUTING.md): how to set up the environment, run the tests, propose glossary rules and help with other platforms. The flow is GitHub Flow: short branches, a PR to `main`, squash merge. One simple rule: every behavior change comes with a test.

Forking is fine too: the [guide](docs/guia-voz.pdf) (Spanish) explains the architecture and where to plug in another engine.

## License

MIT. See [LICENSE](LICENSE).

Created by Daniel Patiño Vargas, in Colombia.

We built this, Daniel and Claude (Anthropic's AI), in collaboration.
