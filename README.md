# voz

![license MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![python 3.12](https://img.shields.io/badge/python-3.12-3776AB.svg)
![platform macOS Apple Silicon](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-lightgrey.svg)
![version 0.1.0](https://img.shields.io/badge/version-0.1.0-informational.svg)

Web: [voz-cli.vercel.app](https://voz-cli.vercel.app) (cómo se ve, cómo instalarlo y qué cambió en cada versión)

**Español** · [English](README.en.md)

Dictado por voz para la terminal, 100 % local. Hablas, `voz` transcribe en tu Mac con [mlx-whisper](https://pypi.org/project/mlx-whisper/) y deja el texto en el portapapeles, listo para pegarlo en Claude Code, en tu editor o donde quieras. Tu voz no pasa por ningún servidor.

## Por qué existe

Queríamos dictarle a un agente de código sin mandar el audio a la nube y sin pagar otra suscripción. Dictar es mucho más rápido que teclear prompts largos, pero casi todas las opciones cómodas son servicios externos.

`voz` hace una cosa: convierte tu voz en texto y lo pone en el portapapeles. Esa es toda la integración. No hay plugin ni API que mantener: haces `Cmd-V` donde lo necesites. También transcribe archivos de audio (notas de voz de WhatsApp o Telegram, reuniones grabadas).

## Para quién es

Para quien vive en la terminal en un Mac con chip Apple Silicon (M1 o posterior) y quiere dictar sin sacar su voz del equipo. Si además escribes en español, mejor: es el idioma con el que más lo hemos usado. Con `--lang auto` mantiene el idioma de la sesión, empieza por tu idioma principal (español por defecto, ver [Idioma principal](#idioma-principal)) y solo cambia con evidencia fuerte.

**Estado: v0.1.0.** Funciona y lo usamos a diario, pero es una versión temprana con aristas conocidas (ver [Limitaciones](#limitaciones)). Solo macOS en Apple Silicon por ahora.

## Cómo se ve una sesión

Es una ilustración del flujo; los tiempos y el texto cambian con lo que digas.

```
$ voz
voz  ·  turbo  ·  auto  ·  🎙 MacBook Pro Microphone
⠋ Cargando modelo… 3 s   esc salir

● Listo   ↵ hablar · esc salir
  habla con pausas cortas: voz transcribe mientras hablas · voz --help para más

        (pulsas ↵ y empiezas a hablar)

● Escuchando  00:07  ·····▃▅▃··  es   ↵ terminar · p pausa · esc descartar
  Necesito que revises el módulo de facturación antes del viernes
  y separa el texto parcial del confirmado

        (pulsas ↵ para terminar)

⠋ Terminando… 2 s · 1 tramo pendiente

── grabación 1 ─────────────────────────────────────────────
Necesito que revises el módulo de facturación antes del viernes y separa el texto parcial del confirmado.

✓  Copiado (reemplazó tu portapapeles)  ·  7 s  ·  17 palabras  ·  es
↵ salir · Cmd-V para pegar   r grabar otra   c añadir   ⌫ descartar
```

La cabecera muestra el modelo abreviado (`turbo`, o `large-v3` con `--accurate`) y el micrófono de entrada por defecto del sistema. La pista gris bajo «Listo» sale solo la primera vez. En una terminal estrecha (menos de unas 60 columnas) las teclas pasan a una línea propia y se recorta antes el medidor: nunca desaparecen.

El medidor te dice si `voz` te está oyendo: en silencio dibuja puntos tenues (`·`); cuando detecta voz, barras de color (`▁▂▃▅▇`). Si hablas y solo ves puntos, revisa el micrófono con `voz doctor --mic`.

Mientras hablas, el texto confirmado aparece con el color normal de tu terminal (se lee igual con fondo claro u oscuro) y la suposición provisional del tramo en curso en tono tenue. Al terminar, el texto ya está en el portapapeles. El flujo de todos los días es `voz`, `↵`, hablar, `↵`, `↵`, y `Cmd-V`.

## Instalación

Necesitas un Mac con Apple Silicon, `python3.12` y [Homebrew](https://brew.sh) (para instalar Python y, si quieres transcribir archivos, `ffmpeg`; dictar por micrófono no lo usa).

**0. Comprueba tu Mac y Python**

```bash
uname -m                 # debe decir arm64 (Apple Silicon)
python3.12 --version     # si no existe: brew install python@3.12
```

**1. Clona y crea el entorno**

```bash
git clone https://github.com/danpv95/voz voz
cd voz
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.lock.txt
```

`requirements.lock.txt` fija las versiones con las que se probó esta versión, así que la instalación sale igual en cualquier equipo. `requirements.txt` pide solo versiones mínimas y toma las últimas que haya: úsalo si prefieres actualizar.

El entorno pesa cerca de 1,1 GB (mlx-whisper arrastra torch).

**2. Pon el comando en tu PATH**

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/bin/voz" ~/.local/bin/voz
```

Si `which voz` no lo encuentra, añade `~/.local/bin` a tu PATH y recarga la terminal:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**No borres ni muevas la carpeta clonada: `voz` vive ahí.** El comando es un enlace a `bin/voz` y usa el `.venv` de esa carpeta. Si la mueves, repite este paso.

**3. Comprueba que todo está en orden**

```bash
voz doctor          # revisa la instalación, sin red
brew install ffmpeg # solo si vas a transcribir archivos
```

`voz doctor` distingue lo crítico de lo opcional: sin `ffmpeg` muestra `⚠ ffmpeg no instalado: el dictado funciona; para transcribir archivos: brew install ffmpeg`, y del modelo dice si está disponible, incompleto (faltan los pesos) o sin descargar.

La primera vez, `voz` descarga el modelo `whisper-large-v3-turbo` (unos 1,6 GB) a `~/.cache/huggingface/` y lo muestra en la propia interfaz: `Descargando modelo · 420 MB / ~1,6 GB · esc salir` y después `Cargando modelo… 3 s`. Si la descarga no informa bytes, ves `Descargando modelo (~1,6 GB, solo la primera vez)… 12 s`, sin porcentajes inventados. Es el único momento en que `voz` usa la red; si se corta, la próxima vez continúa. Después todo corre sin conexión. (`--accurate` baja otros ~3 GB la primera vez que lo uses.)

**Permiso de micrófono.** macOS se lo da a la terminal (Terminal, iTerm, la que uses), no a `voz`. Si no llega audio, `voz` lo dice sin trazas de error: `✖ No llega audio del micrófono`, con la ruta Ajustes del Sistema → Privacidad y seguridad → Micrófono, y `↵` reintenta (vuelve a consultar el dispositivo por defecto, útil si conectas otro micrófono). El flujo simple (`VOZ_UI=simple`, `--no-live`) avisa «Silencio detectado» con la misma ruta.

## Uso

Corre `voz` en una pestaña aparte de la terminal, no dentro de la sesión del agente: necesita una terminal interactiva para el micrófono.

```bash
voz                         # dicta por el micrófono (↵ empieza, ↵ termina)
voz --lang es               # fija español (por defecto auto: mantiene el idioma de la sesión)
voz --accurate              # modelo grande: más lento, más preciso (1ª vez baja ~3 GB)
voz --out notas.md          # además de copiar, añade el texto a un archivo

voz archivo.m4a             # transcribe un archivo (m4a, ogg, opus, mp3, wav)
voz reunion.m4a --srt       # además escribe reunion.srt con tiempos
voz reunion.m4a --json      # y/o reunion.json con los segmentos
voz nota.ogg --no-copy > nota.txt   # solo stdout, nada al portapapeles

voz doctor                  # diagnóstico sin red (voz doctor --mic graba 1 s)
voz devices                 # lista los dispositivos de audio
voz --version
```

`voz` imprime en stdout solo la transcripción. Todo lo demás (estado, avisos) va a stderr, así que `voz archivo.m4a | pbcopy` o redirigir a un archivo funcionan limpios. Los `--srt` y `--json` nunca sobrescriben: si ya existen, crean `reunion-1.srt`, `reunion-2.srt`, etc.

Banderas principales:

| Bandera | Qué hace |
|---|---|
| `--lang es\|en\|auto` | Idioma. `auto` (default) mantiene el idioma de la sesión: empieza por tu idioma principal (español si no se puede saber) y solo cambia con evidencia fuerte. Mezclar español e inglés en una sesión sigue funcionando cuando el cambio es claro; en llamadas o con varias voces conviene `--lang es` |
| `--accurate` | Usa `whisper-large-v3-mlx` en vez del turbo |
| `--model REPO` | Otro modelo de mlx-whisper (no se combina con `--accurate`) |
| `--out RUTA` | Añade el texto al final de ese archivo |
| `--no-copy` | No toca el portapapeles |
| `--loop` | Para el micrófono sin interfaz interactiva: repite sin recargar el modelo |
| `--no-live` | Graba todo y transcribe al final, sin texto en vivo |
| `--no-trim` | Con `--no-live`, no recorta el silencio de los extremos |
| `-y` | En el flujo simple, copia sin preguntar (la interfaz interactiva se rige por `config.toml`) |

`voz --help` muestra todo esto en español, con las teclas y las rutas de configuración.

La referencia completa está en la [guía](docs/guia-voz.pdf) (fuente: [`docs/guia-voz.html`](docs/guia-voz.html)).

## Teclas del modo en vivo

| Momento | Teclas |
|---|---|
| Listo | `↵` o espacio: hablar · `esc`: salir |
| Escuchando | `↵` o espacio: terminar · `p`: pausa · `esc`: terminar y descartar (se puede deshacer) |
| En pausa | `p`: reanudar · `↵`: terminar · `esc`: terminar y descartar |
| Revisión | `↵`: salir (el texto ya está copiado: `Cmd-V` para pegar) · `r`: grabar otra · `c`: añadir a esta grabación · `⌫`: descartar · `u`: deshacer · `s`: copiar (solo si pediste copiar a mano) · `esc`: salir |
| Sin voz | `r`: reintentar · `↵` o `esc`: salir |
| Sin audio del micrófono | `↵` o `r`: reintentar · `esc`: salir |
| Salir con varias grabaciones | `↵`: dejar copiada la última · `a`: copiar todas juntas · `esc`: salir sin cambiar el portapapeles |

Mientras el modelo carga ves `⠋ Cargando modelo… 3 s` con un contador; un `↵` pulsado en ese momento se ignora con el aviso «↵ ignorado: el modelo todavía está cargando». Si la carga falla, lo ves ahí mismo con la causa. Descartar nunca borra el audio del disco. `Ctrl-C` es una salida segura: termina lo que se está grabando, deja copiado el último texto válido (según tu configuración) y sale siempre con código 130, haya texto o no. Dice lo que de verdad pasó: `Interrumpido · texto copiado`, `Interrumpido · audio guardado en <ruta>` o `Cancelado` (no había grabación), y nunca promete un respaldo que falló. Al terminar, `voz` espera hasta 20 s a que se transcriba lo pendiente; si no llega, o si pulsas `Ctrl-C` otra vez, sale igual: el audio queda guardado, la sesión queda marcada como incompleta y `voz` te dice cómo re-transcribirla (el siguiente `voz` también te la recuerda). Si algún tramo no se pudo transcribir, la revisión no finge éxito: muestra `⚠ Texto incompleto · 2 tramos sin transcribir · copiado` y el comando para transcribir el audio entero; si fallaron todos, dice `✖ No se pudo transcribir · el audio quedó guardado` en lugar de «No se detectó voz». Pegar texto en la terminal de `voz` no dispara teclas.

Si tu terminal dibuja mal la interfaz, usa `VOZ_UI=simple voz`. Si pipeas o corres `voz` sin terminal interactiva, también se usa el flujo simple.

**Cuándo copiar.** Por defecto cada grabación se copia sola. Puedes cambiarlo en `~/.config/voz/config.toml`:

```toml
[portapapeles]
copiar = "auto"        # auto (default) | preguntar | nunca
```

### Idioma principal

Con `--lang auto`, `voz` se inclina hacia un idioma principal: lo usa en el primer tramo si el otro no es claro, y volver a él cuesta menos que irse. Lo elige así, de arriba abajo:

1. `[idioma] principal` en `config.toml` (`"es"` o `"en"`):

   ```toml
   [idioma]
   principal = "en"       # es | en
   ```

2. `LC_ALL` o `LANG`, solo si dicen español (`es_*`). Un `en_US` heredado de la terminal es muy común y no prueba nada, así que no decide por sí solo.
3. `AppleLocale` (macOS): si la **región** es de un país hispanohablante (Argentina, Bolivia, Chile, Colombia, Costa Rica, Cuba, Ecuador, El Salvador, España, Guatemala, Guinea Ecuatorial, Honduras, México, Nicaragua, Panamá, Paraguay, Perú, Puerto Rico, República Dominicana, Uruguay, Venezuela), el principal es español aunque los menús estén en inglés (`en_CO`). Si no, el idioma del propio locale cuando es `es` o `en` (`en_US` da inglés).
4. Un `en` de `LANG` si no hubo nada más, y por último español.

## Glosario de correcciones

Whisper oye mal los nombres propios y los términos técnicos que no ha visto en español. En lugar de empujarlo con un prompt (lo repite cuando hay silencio), `voz` corrige el texto después, con un diccionario tuyo y editable.

Vive en `~/.config/voz/corrections.toml`. Hay un punto de partida:

```bash
mkdir -p ~/.config/voz
cp config/corrections.example.toml ~/.config/voz/corrections.toml
```

Cada línea es `"lo que Whisper escribe" = "lo que querías"`:

```toml
[replacements]
"supa base" = "Supabase"
"cloud code" = "Claude Code"
```

Reglas de la casa: solo nombres propios y términos técnicos, nunca palabras comunes (pares como poner/poder son errores acústicos y romperían frases legítimas). La clave no distingue mayúsculas, pero sí tildes. Mínimo 3 letras. Las claves largas ganan sobre las cortas y las reglas se aplican en una sola pasada. Una regla que solo cambia mayúsculas es válida: normaliza cualquier variante de la palabra (`nit` pasa a `NIT`).

## Privacidad

- El audio y el texto se procesan en tu equipo. No hay cuentas, claves ni telemetría.
- `bin/voz` arranca con `HF_HUB_OFFLINE=1` y desactiva la telemetría de Hugging Face: con el modelo en caché, `voz` no abre conexiones. La única excepción es la primera descarga del modelo, que `voz` avisa antes de hacerla.
- Mientras grabas, el audio y cada tramo de texto se guardan en `~/.local/state/voz/` (las últimas 5 sesiones, una hora de audio pesa unos 115 MB). Es una red de seguridad: si algo corta la sesión, lo grabado sigue ahí y el siguiente `voz` te avisa cómo recuperarlo. La carpeta es solo tuya (permisos `0700` y archivos `0600`, sin importar tu `umask`). Si no quieres esos archivos, borra la carpeta cuando quieras.

## Limitaciones

Lo que sabemos que no funciona bien, sin maquillar:

- **Solo Mac con Apple Silicon.** El motor (MLX), el portapapeles (`pbcopy`) y la captura dependen de macOS.
- **El deletreo es pobre.** Siglas, letras sueltas, rutas y claves salen como Whisper quiera escribirlas. Esta versión ya no borra dígitos ni letras repetidas, pero no las reconoce mejor.
- **No distingue quién habla.** Todo sale como un solo texto.
- Mezclar idiomas dentro de la misma frase, sin pausa, enreda a Whisper. En `auto` solo elige entre español e inglés.
- **Llamadas o varias voces: fija el idioma.** En `auto` el audio lejano o con varias voces puede confundir al detector. La sesión es pegajosa y solo cambia con evidencia fuerte, pero en llamadas o con varias voces conviene `--lang es` (o `--lang en`).
- Con el micrófono, un «gracias» dicho solo se conserva cuando de verdad hablaste: se descarta únicamente si el audio es silencio (alucinación típica de Whisper). En archivos, un «gracias» aislado sí se descarta, por prudencia.
- Con ruido fuerte de fondo (10 dB de relación señal-ruido o peor) el detector de pausas falla.
- El glosario distingue tildes: `nequi` y `néqui` son reglas distintas.
- No captura el audio del sistema (por ejemplo, el de una reunión en línea): solo el micrófono.

## Roadmap

`voz` es el proyecto; hoy tiene una sola cara, el CLI de terminal. Las demás se apoyan en el mismo núcleo (grabar, transcribir, limpiar, glosario).

1. **v0.1.1**: glosario insensible a tildes, portapapeles con entorno UTF-8, validar `--lang`, señal sonora al terminar, tecla `e` para corregir una palabra y recordarla, tecla `i` para cambiar el idioma y reprocesar, idioma principal aprendido del uso, pista de vocabulario opcional y CI en GitHub Actions. Detalle en [CHANGELOG.md](CHANGELOG.md).
2. **Windows y Linux**: un motor alternativo (por ejemplo faster-whisper) detrás de la misma interfaz, más portapapeles y teclado multiplataforma. Si quieres ayudar, empieza por [CONTRIBUTING.md](CONTRIBUTING.md).
3. **App de menú para macOS**, a futuro: dictar en cualquier aplicación con un atajo global.
4. **`voz habla`**, a futuro: el camino inverso, de texto a voz.

Sin fechas. Es un proyecto personal y avanza a ratos.

## Contribuir

Los issues y los PR son bienvenidos, en español o en inglés. Antes de abrir un PR lee [CONTRIBUTING.md](CONTRIBUTING.md): cómo montar el entorno, correr los tests, proponer reglas del glosario y ayudar con otras plataformas. El flujo es GitHub Flow: ramas cortas, PR a `main` y squash. Una regla simple: todo cambio de comportamiento lleva su test.

También puedes hacer fork con tranquilidad: la [guía](docs/guia-voz.pdf) explica la arquitectura y dónde enchufar otro motor.

## Licencia

MIT. Ver [LICENSE](LICENSE).

Creado por Daniel Patiño Vargas, en Colombia.

Lo construimos Daniel y Claude (IA de Anthropic) en colaboración.
