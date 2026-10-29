# Contribuir a voz

Gracias por el interés. Los issues y los pull requests son bienvenidos, en español o en inglés (*issues and PRs are welcome in English or Spanish*). Es un proyecto personal con poco tiempo, así que una respuesta puede tardar unos días.

Antes de empezar con algo grande (un motor nuevo, una plataforma nueva, un cambio de interfaz), abre un issue y lo hablamos. Evita que trabajes en algo que no encaje.

## Flujo de trabajo

Usamos GitHub Flow.

- `main` está siempre estable y protegida. Nadie empuja directo: todo cambio entra por un pull request desde una rama corta.
- Nombra la rama con un prefijo: `feat/…`, `fix/…`, `docs/…` o `chore/…` (por ejemplo `fix/tildes-glosario`).
- El PR se integra con **squash**: un commit por cambio en el historial de `main`.
- Los commits siguen Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`). Ver [Estilo de commits](#estilo-de-commits).
- Versionado semántico (`MAJOR.MINOR.PATCH`). Cada versión es un tag `vX.Y.Z` y un release en GitHub, con las notas sacadas del [CHANGELOG](CHANGELOG.md).
- El CHANGELOG sigue Keep a Changelog: lo nuevo se anota en *Unreleased* y esa sección se cierra con el número y la fecha al publicar.
- Todo cambio de comportamiento lleva su test (ver la regla de oro más abajo).
- Los issues con la etiqueta `good first issue` son la puerta de entrada.

No usamos ramas `develop` ni `release`: es un proyecto pequeño con un solo mantenedor, así que GitHub Flow basta.

## Montar el entorno

Necesitas un Mac con Apple Silicon y `python3.12`.

```bash
git clone https://github.com/danpv95/voz voz
cd voz
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.lock.txt -r requirements-dev.txt
```

`requirements.lock.txt` fija las versiones exactas con las que se prueba el proyecto. `requirements.txt` solo pide mínimos y toma las últimas versiones: úsalo si quieres comprobar que `voz` sigue funcionando con dependencias nuevas, y si todo va bien actualiza el lock (`pip freeze`).

Para usar tu copia como comando: `ln -sf "$PWD/bin/voz" ~/.local/bin/voz`. Después, `voz doctor` te dice si falta algo.

## Correr los tests

```bash
./.venv/bin/python -m pytest -q                    # unitarios: sin modelo ni micrófono, segundos
./.venv/bin/python -m pytest -q -m integration     # E2E con el modelo real
```

- Los **unitarios** son el default (`pyproject.toml` excluye `integration`). No necesitan red, modelo, micrófono ni portapapeles. Tienen que pasar siempre antes de abrir un PR.
- Los de **integración** usan `say` de macOS para generar voz y el modelo `whisper-large-v3-turbo` ya descargado. Si no lo tienes en caché, se saltan solos.
- `tests/conftest.py` agrega `src/` al path, así que los módulos se importan planos (`import stt`, `import corrections`).

## Regla de oro: cambio de comportamiento, test

Todo cambio que altere lo que `voz` hace (una regla de filtro, una tecla, un mensaje, un formato de salida) lleva un test que lo pruebe. Un arreglo de bug lleva el test que habría fallado antes del arreglo.

Algunas pautas:

- Prueba comportamiento observable, no detalles internos. Las piezas puras (`Segmenter`, `flow.step`, `corrections.apply`, `stt._filter_segments`) se prueban fácil y sin hardware.
- Un test por archivo temático en `tests/`. Si tocas el glosario, mira `tests/test_corrections.py`; si tocas teclas y estados, `tests/test_flow.py` y `tests/test_keys_clipboard.py`.
- No incluyas audios personales ni de terceros en el repo. Para audio, genera voz sintética con `say` o crea señal en numpy.
- No subas datos personales: nombres, números, rutas de tu equipo.

## Estilo de código

- Python 3.12, `from __future__ import annotations` y anotaciones de tipo.
- Sin dependencias nuevas salvo que sea imprescindible (la interfaz de terminal usa solo ANSI y `termios` a propósito). Si propones una, justifícala en el issue.
- Los comentarios y docstrings del código están en inglés. Los mensajes que ve el usuario están en español por ahora.
- Cambios pequeños y enfocados. Un PR, una idea.

## Regenerar la guía (PDF)

La guía se escribe en `docs/guia-voz.src.html` (ahí se edita; el índice lleva el marcador `{{TOC}}`). `docs/guia-voz.html` y `docs/guia-voz.pdf` son generados: no los edites a mano.

```bash
python3.12 scripts/build_guide.py
```

Necesita Google Chrome (modo headless) y `pdftotext` (`brew install poppler`). Si tu Chrome está en otra ruta, define `CHROME`. El script renderiza dos veces porque el índice lleva números de página que solo se conocen con el PDF ya maquetado, y avisa si no se estabilizan. Haz commit de los tres archivos juntos.

## Proponer reglas para el glosario

El archivo de ejemplo `config/corrections.example.toml` solo debe traer reglas que sirvan a cualquiera.

- **Solo nombres propios y términos técnicos** que Whisper oye mal de forma consistente (por ejemplo, un nombre de producto o de librería).
- **Nunca palabras comunes.** Pares como poner/poder o casa/caza son errores acústicos: corregirlos a ciegas rompería frases correctas.
- **Cuidado con las claves que son palabras reales en otro idioma.** Una regla `cloud` → `Claude` daña `backup to the cloud`, y por eso ya no está en el ejemplo. Prefiere claves de varias palabras (`cloud code`) o inventadas por el mal oído (`supavase`).
- Mínimo 3 caracteres por clave. Las claves distinguen tildes (hoy no las pliega).
- En el PR, cuenta qué frase dijiste y qué escribió Whisper exactamente. Sin eso no podemos comprobar la regla.
- Nada de nombres de personas, clientes o empresas privadas.

## Ayudar con Windows y Linux

Es lo que más ayudaría. `voz` hoy depende de macOS en cuatro puntos, y la [guía](docs/guia-voz.pdf) (sección «Guía para forks») los explica a fondo:

1. **Motor de transcripción.** mlx-whisper solo corre en Apple Silicon. La idea es poner detrás de `stt.transcribe_segments` otro motor (faster-whisper, por ejemplo) con la misma firma y los mismos filtros.
2. **Portapapeles.** `output.py` usa `pbcopy` y `pbpaste`. Hacen falta equivalentes (`wl-copy` o `xclip` en Linux, `clip` o PowerShell en Windows).
3. **Teclado de la terminal.** `ui.Keys` usa `termios` y `tty`, que no existen en Windows.
4. **Bloqueos de archivo.** `autosave.py` usa `fcntl.flock`, solo POSIX.

Cómo conviene hacerlo:

- Abre un issue primero para acordar la interfaz.
- Un PR por capacidad (portapapeles, luego teclado, luego motor), no uno gigante.
- El camino de macOS tiene que seguir funcionando y con sus tests en verde.
- Si no tienes Mac para probar, dilo en el PR: indica qué probaste y qué no.

## Estilo de commits

Usamos [Conventional Commits](https://www.conventionalcommits.org/es/):

```
feat: acepta Enter mientras carga el modelo
fix: el colapso de repeticiones conserva la puntuación final
docs: aclara el permiso de micrófono en el README
test: cubre el glosario con tildes
refactor: separa la copia al portapapeles de liveui
chore: actualiza requirements.lock.txt
```

- Primera línea en imperativo o presente, de 72 caracteres o menos, en español o inglés.
- Si el porqué no es obvio, cuéntalo en el cuerpo del commit.
- Un commit por cambio lógico. No mezcles formato con comportamiento. Como el PR se integra con squash, lo que importa es el título del PR.

## Abrir el pull request

1. Haz fork y crea una rama desde `main` (`fix/tildes-glosario`, `feat/portapapeles-linux`).
2. Corre los unitarios.
3. Abre el PR contra `main` y rellena la plantilla: qué cambia para el usuario (no solo qué archivos tocaste), por qué y cómo lo probaste.
4. Si cambia algo visible, actualiza el README, la guía (edita `docs/guia-voz.src.html` y regenera con `scripts/build_guide.py`) y el [CHANGELOG](CHANGELOG.md) (sección *Unreleased*).
5. Se integra con squash. Si el título del PR sigue Conventional Commits, el historial queda limpio.

Al contribuir aceptas que tu aporte se publique bajo la licencia MIT del proyecto.
