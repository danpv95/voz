# Changelog

Todos los cambios relevantes de este proyecto se documentan aquí.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa [versionado semántico](https://semver.org/lang/es/).

## Unreleased / 0.1.3

Pendiente, en orden de prioridad. Es una lista de trabajo, no una promesa de fechas.

### Correcciones prioritarias
1. Glosario insensible a tildes y con normalización Unicode NFC (hoy `nequi` y `néqui` son reglas distintas).
2. Portapapeles: forzar entorno UTF-8 en `pbcopy` y `pbpaste` para que las tildes sobrevivan si `voz` se lanza desde un lanzador o un servicio sin entorno UTF-8.
3. Validar `--lang` en el parser (aceptar `ES`, `spanish`) en vez de fallar después con un error engañoso.
4. Pie de la pantalla duplicado al cambiar el tamaño de la ventana.
5. El resumen puede indicar `es+en` por un tramo en inglés que se descartó.

### Funciones nuevas
6. Tecla `e` en la revisión: corregir una palabra y elegir entre «solo esta grabación» o recordarla en el glosario.
7. Tecla `i` en la revisión: cambiar el idioma (auto, español, inglés) y reprocesar la grabación.
8. Idioma principal aprendido del uso: que el sesgo de `--lang auto` siga el idioma que de verdad dictas, además del config y del sistema (por decidir).
9. Pista de vocabulario opcional para nombres y términos (por decidir).
10. Señal sonora opcional al terminar.
11. La rotación conserva solo las últimas 5 sesiones: poder conservar una.

### A evaluar
12. Umbrales de confianza del filtro, que hoy descartan más que el propio Whisper en habla técnica o con mezcla de idiomas.
13. En vivo, aplicar las reglas del glosario de varias palabras aunque una pausa las parta en dos tramos.
14. El colapso por frases todavía recorta repeticiones literales como `A. A. A. A.` (el de palabras ya no).
15. Ocultar el aviso técnico `resource_tracker: leaked semaphore` que Python imprime tras un cierre forzado del micrófono.
16. Audio de fondo (videos o música por los parlantes) produce frases inventadas en inglés («I'm going to go to the top»). Evaluar un aviso o perfil para entornos con audio de fondo.
17. Filtro de bucles: una frase de 3+ palabras repetida idéntica en el tramo siguiente se descarta (el audio se conserva). Compromiso conocido: una repetición dictada a propósito se pierde del texto.
18. Modo archivo: aplicar el mismo idioma pegajoso en `detect_languages`.
19. Voces remotas por parlante casi no se detectan como voz (evaluar captura del audio del sistema).

### Infraestructura
20. CI en GitHub Actions (macOS Apple Silicon) con los tests unitarios.

## 0.1.2 - 2026-11-06

Menos voz perdida y nada que se pise: la puerta de silencio se adapta a tu sala y descartar o salir no destruye lo que ya tenías.

### Añadido
- `s copiar de nuevo` siempre visible en la revisión cuando hay texto (`s copiar` si aún no se copió). Con `--no-copy` no se ofrece.
- Tecla `h` para abrir el historial desde Listo, la revisión y Sin voz: `↑↓` elegir, `1-9` saltar, `↵` copiar, `esc` o `h` vuelve a donde estabas. Sin grabaciones guardadas, un aviso de una línea.
- Las grabaciones descartadas ya no desaparecen: salen al final del historial y de `voz copiar --lista`, marcadas «descartada», y se copian por su número (`voz copiar 4`) o con `↵` en el historial. `voz copiar` sin número sigue copiando la última válida.
- La revisión muestra una línea de respaldo propia según lo que de verdad se guardó: `Respaldo: ~/.local/state/voz/sesion-…-g1 · texto y audio`, o en ámbar si falló el texto o el audio.
- El resumen indica `N tramos omitidos por nivel bajo` cuando la puerta descartó tramos (con `VOZ_DIAG=1` apunta a `diag.log`).
- «(reemplazó tu portapapeles)» se muestra solo en el momento de copiar, no en cada redibujo.
- En terminales angostas el historial quita la fecha y la duración antes de recortar las primeras palabras de cada grabación.

### Corregido
- Puerta de silencio relativa al ruido de la sala: la voz baja ya no se pierde. Un tramo debe sobresalir del piso de ruido medido en tus pausas (8 veces si es corto, 5 si es largo) en vez de pasar un umbral fijo. En 9 sesiones reales conserva 859 de 920 tramos (antes 782), sin perder ninguno de los que antes pasaban.
- Descartar no pisa una copia externa: si copiaste otra cosa mientras tanto, el portapapeles queda como estaba, también al salir de `voz`. Si sigue siendo texto de `voz`, se restaura lo anterior.
- Un tramo que falla ya no deja la sesión como limpia: queda marcada y con el comando para recuperar el audio.
- Nada tardío entra en una grabación cerrada: si el modelo responde después de abandonar un tramo atascado, ese resultado se descarta. `c` tras un abandono sigue con un motor nuevo y conserva el texto ya transcrito.
- El respaldo no suelta su protección mientras haya audio por escribir: el siguiente `voz` avisa de la sesión pendiente.
- Nombres de sesión: una colisión ya no genera `…-2-2`, un nombre que dejaba la grabación fuera de las listas.
- Sin voz y descartada ya no dicen que el audio quedó guardado cuando el disco lo rechazó.
- Filtros: una «y» suelta (y su bucle «y y y…») y «suscríbete al canal» ya no entran como texto.

## 0.1.1 - 2026-11-04

Menos texto inventado en reuniones y una forma de recuperar lo que dictaste.

### Añadido
- `voz copiar [N]` vuelve a copiar una grabación guardada en `~/.local/state/voz` (la última no descartada; `--todas`, `--lista`, `--stdout`).

### Corregido
- En vivo, los tramos sin voz ya no llegan a Whisper (#7): un tramo cuyo pico (RMS de 30 ms) queda bajo 0,015 se descarta, y el idioma solo sale del español con un tramo de al menos 3 s y si el texto se acepta. Diagnóstico opcional con `VOZ_DIAG=1` (sin texto).

## 0.1.0 - 2026-10-29

Primera versión pública.

### Añadido
- Dictado por micrófono en vivo con [mlx-whisper](https://pypi.org/project/mlx-whisper/), 100 % local. Un detector de pausas corta el audio en tramos (pausa de unos 0,7 s, máximo de 12 s con corte suave en el punto más silencioso) y cada tramo se transcribe mientras sigues hablando. El tramo en curso muestra un texto provisional en gris.
- Interfaz de terminal con estados (Listo, Escuchando, En pausa, Revisión, Sin voz, Elegir) y teclas de una sola pulsación: hablar, pausar, terminar, continuar, grabar otra, descartar y deshacer. Descartar nunca borra el audio del disco.
- Medidor que distingue silencio de voz: puntos tenues (`·`) cuando no hay voz y barras de color (`▁▂▃▅▇`) cuando `voz` te está detectando.
- Micrófono en la cabecera (`voz · turbo · auto · 🎙 MacBook Pro Microphone`), con el modelo abreviado (`turbo` o `large-v3`).
- Contador de carga del modelo («Cargando modelo… 3 s») y contador al terminar («Terminando… 2 s · 1 tramo pendiente»).
- Pista de primer uso bajo «Listo», solo la primera vez: habla con pausas cortas y `voz --help` para más.
- Copia automática al portapapeles por grabación, configurable (`auto`, `preguntar`, `nunca`) en `~/.config/voz/config.toml`. Al salir con varias grabaciones se elige entre dejar la última o copiar todas.
- Transcripción de archivos (m4a, ogg, opus, mp3, wav) con `--srt` y `--json` que nunca sobrescriben, y detección de idioma por frase para archivos que mezclan español e inglés.
- Idioma automático restringido a español e inglés: en vivo mantiene el idioma de la sesión, empieza por el idioma principal (por defecto el del sistema, y si no, español) y solo cambia con evidencia fuerte. Mezclar idiomas en una sesión sigue funcionando cuando el cambio es claro; en llamadas o con varias voces conviene `--lang es`. `--lang` fija el idioma.
- `[idioma] principal = "es"` o `"en"` en `~/.config/voz/config.toml` elige el idioma al que se inclina `--lang auto`. Sin config se deduce del sistema (ver «Cambiado») y, si nada aplica, español.
- Modelo turbo por defecto, `--accurate` para el modelo grande y `--model` para cualquier otro de mlx-whisper.
- Glosario de correcciones de nombres propios en `~/.config/voz/corrections.toml`, de una sola pasada, con ejemplo en `config/corrections.example.toml`.
- Filtros contra alucinaciones de Whisper: frases basura típicas del silencio, segmentos que parecen no ser voz y bucles de repetición.
- Recorte de silencio de los extremos en el modo `--no-live`, con margen de 300 ms.
- Grabación a salvo: el audio y el texto confirmado se guardan en `~/.local/state/voz/` mientras hablas, se conservan las últimas 5 sesiones y el siguiente arranque avisa de las que no terminaron bien, con el comando para re-transcribirlas.
- `voz doctor` (diagnóstico sin red, con `--mic` para medir el micrófono), `voz devices` y `voz --version`.
- Modo `--loop`, flujo simple para terminales no interactivas (`VOZ_UI=simple`), respeto de `NO_COLOR` y volcado de la pila de hilos con `Ctrl-\` para diagnosticar congelamientos.
- Salida limpia: stdout lleva solo la transcripción y todo lo demás va a stderr.
- `bin/voz` sin rutas fijas: resuelve su propia ubicación aunque lo invoques por un enlace simbólico.
- `voz --help` en español: títulos, teclas del modo en vivo, rutas de configuración y estado, y `VOZ_UI=simple`.
- Instalación reproducible: `requirements.lock.txt` es la forma recomendada de instalar; `requirements.txt` toma las últimas versiones.
- `scripts/build_guide.py` regenera la guía (`docs/guia-voz.html` y `.pdf`) desde `docs/guia-voz.src.html`, con el índice numerado estable.
- Licencia MIT.
- Pruebas unitarias sin modelo ni micrófono y pruebas de integración opcionales (`-m integration`) con el modelo real.

### Cambiado
- Idioma principal: ya no se queda en inglés por tener los menús del Mac en inglés. Orden: `[idioma] principal`; `LC_ALL`/`LANG` solo si dicen `es_*` (un `en_US` heredado de la terminal no prueba nada); la región de `AppleLocale` (un Mac `en_CO` es de alguien que habla español: AR BO CL CO CR CU DO EC ES GT GQ HN MX NI PA PE PR PY SV UY VE; EE. UU. no cuenta) o, si no, su idioma cuando es `es`/`en`; y español.
- Revisión: `↵ listo` pasa a `↵ salir · Cmd-V para pegar` (la segunda parte solo cuando el texto de verdad se copió).
- Símbolos y tono unificados en la interfaz y en `voz doctor`: `✓` éxito, `✖` error, `⚠` advertencia, `●` estado, `·` separador; estados con inicial mayúscula y acciones en minúscula. Desaparecen `✅`, `❌` y `✗`.
- `voz doctor`: la falta de ffmpeg es una advertencia (`⚠ ffmpeg no instalado: el dictado funciona; para transcribir archivos: brew install ffmpeg`), no un problema crítico; distingue el modelo disponible, incompleto (faltan los pesos) y ausente, y un modelo `--accurate` sin descargar se muestra como opcional. El cierre dice «Sin problemas críticos · N advertencia(s)» cuando las hay.
- README (es/en) y guía: instalación completa (`brew install python@3.12`, comprobar `arm64`, `source ~/.zshrc`, no borrar la carpeta clonada), orden del idioma principal y comportamiento real del «gracias» aislado.

### Corregido
- Primera descarga del modelo con progreso: la interfaz muestra `Descargando modelo · 420 MB / ~1,6 GB · esc salir` y después `Cargando modelo… N s`, en vez de un aviso suelto que chocaba con el redibujado y minutos de aparente bloqueo. Sin cifras fiables, `Descargando modelo (~1,6 GB, solo la primera vez)… N s`, sin porcentajes inventados. Un modelo a medias (config sin pesos) reanuda la descarga.
- Micrófono denegado, desconectado o mudo: `✖ No llega audio del micrófono`, la ruta de Ajustes del Sistema y `↵ reintentar · esc salir`, en lugar de un traceback o de «Silencio total» ambiguo. El reintento vuelve a consultar el dispositivo por defecto. Los flujos sin interfaz también muestran el mensaje.
- Terminal estrecha: si la línea de estado no cabe, las teclas pasan a su propia línea (se recorta antes el medidor y los detalles, nunca las teclas), las pistas de revisión se reparten en varias líneas y los avisos de pérdida de audio tienen línea propia.
- `Ctrl-C` informa de lo que pasó: `Interrumpido · texto copiado`, `Interrumpido · audio guardado en <ruta>` o `Cancelado` (no había grabación), y nunca promete un respaldo que falló. Sigue saliendo con código 130.
- Transcripción incompleta: si hubo tramos fallidos o un cierre abandonado, la revisión muestra `⚠ Texto incompleto · N tramos sin transcribir · copiado` y cómo re-transcribir; si todo falló dice `✖ No se pudo transcribir · el audio quedó guardado` en vez de «No se detectó voz».
- El colapso de repeticiones ya no borra dígitos ni letras deletreados: `1 1 1 1` y `A A A A` se conservan.
- Cuando el colapso de repeticiones sí actúa, conserva la puntuación final de la última repetición.
- En vivo, una frase inventada en bucle que llega repetida tramo tras tramo (por ejemplo con música de fondo) se queda en una sola copia. Las respuestas cortas dichas dos veces («Sí.» / «Sí.») y el dictado literal se conservan.
- El glosario de ejemplo ya no trae la regla `cloud` → `Claude`, que dañaba frases en inglés.
- El aviso del permiso de micrófono es seguro: el comando `tccutil reset` sugerido ahora lleva el identificador de la terminal (Terminal o iTerm) y solo reinicia el permiso de esa aplicación, en lugar del de todas.
- Las grabaciones son privadas: la carpeta de estado queda en `0700` y los `.wav`, `.txt` y `.lock` en `0600`, sin importar el `umask`.
- Dos `voz` a la vez no se pisan: el nombre de sesión se reserva de forma atómica (`O_EXCL`) y, si ya existe o el bloqueo falla, se usa el siguiente sufijo en vez de escribir sobre archivos ajenos.
- Un `config.toml` o `corrections.toml` con tipos incorrectos (por ejemplo `portapapeles = 1`) o que no es UTF-8 avisa por stderr y usa los valores por defecto, sin traceback.
- `--out` sin permiso de escritura en la interfaz y en el flujo simple: mensaje claro, código de salida 1, y el texto sigue en el portapapeles y en la carpeta de estado. Salir sin grabar ya no añade una línea en blanco al archivo.
- Terminal con fondo claro: el texto confirmado usa el color por defecto de la terminal y el secundario va en tenue (`NO_COLOR` se respeta).
- Teclas con el mismo vocabulario en todas partes: `p reanudar`, `c añadir`, `⌫ descartar` y `esc descartar` cuando cabe (en terminales estrechas, la forma corta).
- `↵` mientras carga el modelo: se ignora con el aviso «↵ ignorado: el modelo todavía está cargando» y, si la carga falla, se ve el error y su causa sin pulsar nada. La ayuda de `-y` aclara que solo aplica al flujo simple.
- El glosario de ejemplo trae solo reglas neutras (Claude Code, Supabase, Nequi, voz CLI).
- Idioma pegajoso con sesgo al idioma principal: un tramo débil, ruidoso o de varias voces ya no inventa frases en el otro idioma. El primer tramo siembra el principal salvo evidencia clara, volver a él cuesta menos que irse, los parciales antes del primer tramo usan el principal y un error o un tramo filtrado a vacío no cambian el idioma. La frontera se compara con tolerancia de coma flotante.
- Las reglas del glosario que solo cambian mayúsculas (`nit` a `NIT`) funcionan.
- Cierre estable: terminar espera como máximo 20 s a que se transcriba lo pendiente, y un segundo `Ctrl-C` fuerza la salida. En ambos casos el WAV queda guardado, la sesión queda marcada como incompleta (el siguiente `voz` ofrece re-transcribirla) y el mensaje dice cómo. `Ctrl-C` sale siempre con código 130, haya texto o no; el texto se conserva en el portapapeles según la configuración y en `--out`.
- Autosave estable: un fallo al escribir el `.txt` ya no detiene el respaldo del WAV (ni al revés); el aviso se conserva hasta el cierre. El `.lock` solo se borra si el hilo escritor terminó bien, así una sesión a medias aparece como recuperable. Si `chmod` falla, avisa una vez. Los `.srt` y `.json` junto al audio se crean de forma exclusiva y un `voz` concurrente toma el siguiente sufijo, y la pista de primer uso se registra con creación atómica (`0600`).
- Medidor sin carrera: el hilo de la interfaz lee una copia de los niveles bajo el candado del segmentador, así que ya no puede fallar con «deque mutated during iteration» mientras graba.
