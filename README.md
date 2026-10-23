# Video Lyrics

App de escritorio (Windows) para crear **videos con la letra sincronizada** a partir de una imagen o video de fondo, una canción y su letra. Todo funciona en tu PC, sin internet y sin pagar servicios: la letra se sincroniza con [Whisper](https://github.com/SYSTRAN/faster-whisper) y el video se genera con ffmpeg.

Pensada para músicos que publican en YouTube, TikTok, Reels o Shorts.

## Qué hace

- **Sincroniza la letra sola** con la voz, línea por línea y palabra por palabra. Incluye un editor de tiempos para corregir a mano.
- **Formatos:** 16:9 (YouTube), 9:16 (TikTok / Reels / Shorts) y 1:1 (Instagram), uno o todos de una vez.
- **Fragmentos cortos:** la mejor parte (automático: la zona más fuerte de la canción, priorizando el coro y empezando al inicio de un verso), primer coro, último coro o desde un minuto exacto, con fundidos de entrada y salida.
- **Masterización inicial:** antes de todo crea `<canción> master.wav` (24 bits, para SoundOn). Con una canción de referencia usa [Matchering](https://github.com/sergree/matchering); sin ella, la lleva a −14 LUFS con pico de −1 dB. Botones para escuchar original y master.
- **Archivos para SoundOn:** letra sincronizada en `.lrc` y un `SoundOn.txt` con el minuto de inicio del clip de TikTok.
- **Solo audio:** exporta el fragmento como MP3 de 30 s para subirlo como sonido a TikTok o CapCut.
- **Efectos de letra:** fundido, karaoke palabra por palabra, dos líneas (actual + siguiente), palabra por palabra, máquina de escribir, entrada con "pop" y brillo en el coro.
- **Color automático de la letra:** clara u oscura según el brillo del fondo, en la zona donde va el texto.
- **Tono del fondo:** 10 virados de color (dorado, sepia, nocturno, rojo rock…) o automático según el género.
- **Logo / marca de agua:** con versión clara y oscura; la app elige cuál usar según el fondo. Si el logo viene sobre fondo negro o blanco liso, lo vuelve transparente sola.
- **Título de apertura, movimiento lento del fondo** (zoom suave) y vista previa en vivo.
- **Generador de prompts** para crear fondos con ChatGPT o Canva según el género y el tema de la letra.
- **Arrastrar y soltar:** suelta fondos, audio y letra en la ventana; cada imagen va sola al formato que le corresponde según su forma.
- Recuerda tus ajustes entre sesiones.

## Instalación

Requisitos: Windows 10/11 y [Python 3.11 o superior](https://www.python.org/downloads/).

```bash
git clone https://github.com/rodrigoatenas001/video-lyrics.git
cd video-lyrics
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

ffmpeg se descarga solo al instalar (paquete `imageio-ffmpeg`). La primera sincronización descarga el modelo de Whisper (~1,5 GB para `medium`).

**Opcional — separación de voz** (karaoke más preciso en canciones con mucha instrumentación, ~2 GB):

```bash
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install -r requirements-voz.txt
```

**Opcional — masterización con referencia** (Matchering):

```bash
.venv\Scripts\python -m pip install -r requirements-master.txt
```

## Uso

Abre la app con doble clic en `VideoLyrics.bat`, o:

```bash
.venv\Scripts\python lyrics_app.py
```

1. Arrastra a la ventana el **fondo** (imagen o video), el **audio** (WAV, MP3, M4A, FLAC…) y la **letra** (`.md` o `.txt`).
2. Ajusta estilo, efectos y extras mirando la vista previa.
3. En **Exportar** elige formatos y si quieres la canción completa, un corto o ambos.
4. Pulsa **Crear video**.

### Formato de la letra

Texto plano, una línea por verso. Las etiquetas de sección entre corchetes (como las de Suno) definen el estilo: los coros se destacan y el outro va en cursiva. La primera línea en MAYÚSCULAS se toma como título.

```
EL TÍTULO

[Verse]
Primera línea del verso
Segunda línea del verso

[Chorus]
Línea del coro
```

La sincronización se guarda junto al audio (`<canción>.lineas.json`), así que exportar otros formatos después es inmediato.

### Guías para diseñar fondos

En `plantillas/` hay guías PNG transparentes (16:9, 9:16 y 1:1) que marcan dónde va el logo, dónde va la letra y qué zona tapa la interfaz de TikTok/Reels. Úsalas como capa en Canva o en tu editor.

## Cancionero en PDF

`cancionero.py` junta todas las letras de una carpeta de álbumes (con subcarpetas) en un solo libro PDF: portada, índice por álbum y una canción por página. Quita las notas de producción y los prompts, y descarta las canciones repetidas entre carpetas. Sirve para registrar las letras como un libro en el DDI.

```bash
python cancionero.py "C:\carpeta\de\letras" --titulo "Cancionero" --autor "Tu nombre" --seudonimo "Nombre artístico" --rut "12.345.678-9"
```

El PDF se crea con Microsoft Edge (incluido en Windows), que trae las fuentes para hindi, coreano, árabe y chino.

## Tiempos de referencia

En un PC sin tarjeta gráfica dedicada, para una canción de 4,5 minutos:

| Tarea | Tiempo aprox. |
|---|---|
| Sincronizar con Whisper (`medium`) | 3–5 min (solo la primera vez) |
| Separar la voz con Demucs (opcional) | ~4 min (solo la primera vez) |
| Video completo | ~2 min por formato |
| Video completo con movimiento del fondo | ~12 min por formato |
| Corto de 30 s | ~10 s |

## Licencia

Código bajo [licencia MIT](LICENSE). La fuente Cormorant Garamond incluida en `fonts/` se distribuye bajo la [SIL Open Font License 1.1](fonts/OFL.txt).
