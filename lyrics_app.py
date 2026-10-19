"""Video Lyrics: fondo (imagen o video) + audio + letra -> MP4 con letra sincronizada.

La letra se sincroniza con Whisper y se dibuja con ffmpeg (subtítulos ASS).
La sincronización se guarda junto al audio (<audio>.lineas.json) para no repetirla,
y se puede corregir a mano con el editor de tiempos.
"""
import json, os, re, subprocess, tempfile, threading, unicodedata, difflib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import imageio_ffmpeg

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    BaseTk = TkinterDnD.Tk
except ImportError:  # sin arrastrar y soltar, la app sigue funcionando con los botones
    TkinterDnD, DND_FILES, BaseTk = None, None, tk.Tk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(APP_DIR, "fonts")
CONFIG = os.path.join(APP_DIR, "config.json")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FORMATOS = {  # clave: (ancho, alto, descripción, sufijo de archivo)
    "16:9": (1920, 1080, "Horizontal · YouTube", "16x9"),
    "9:16": (1080, 1920, "Vertical · Reels / TikTok / Shorts", "9x16"),
    "1:1": (1080, 1080, "Cuadrado · Instagram", "1x1"),
}
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".avi")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac")
TEXT_EXT = (".md", ".txt")

POSICIONES = ["Abajo (subtítulos)", "Centro", "Derecha", "Izquierda"]
COLORES = ["Automático", "Letra clara (fondo oscuro)", "Letra oscura (fondo claro)"]
ESQUINAS = ["Arriba centro", "Arriba derecha","Arriba izquierda", "Abajo derecha", "Abajo izquierda"]
EXPORTAR = ["Canción completa", "Fragmento corto (TikTok / Reels)", "Ambos", "Solo audio del corto (MP3)"]
FRAGMENTOS = ["Mejor parte (automático)", "Primer coro", "Último coro", "Desde un minuto exacto"]
DURACIONES = ["Coro completo", "15 s", "30 s", "45 s", "60 s"]
EFECTOS = ["Fundido (una línea)", "Karaoke palabra por palabra", "Dos líneas (actual + siguiente)",
           "Karaoke + dos líneas", "Palabra por palabra (aparecen)", "Máquina de escribir"]
FUENTES = ["Cormorant Garamond", "Georgia", "Arial", "Segoe UI", "Times New Roman",
           "Trebuchet MS", "Verdana", "Impact"]
PALETAS = {  # estilo: color de letra
    "clara": {"Verso": (240, 230, 212), "Coro": (255, 205, 135), "Puente": (255, 228, 180),
              "Outro": (245, 238, 225), "Titulo": (250, 242, 228)},
    "oscura": {"Verso": (62, 38, 22), "Coro": (120, 66, 24), "Puente": (92, 58, 28),
               "Outro": (74, 50, 34), "Titulo": (70, 42, 24)},
}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, **kw)


def tipo_de(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT or ext in VIDEO_EXT:
        return "fondo"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in TEXT_EXT:
        return "letra"
    return None


def dimensiones(path):
    """(ancho, alto) de una imagen o video, o None."""
    err = run([FFMPEG, "-hide_banner", "-i", path]).stderr.decode("utf8", "ignore")
    m = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", err)
    return (int(m.group(1)), int(m.group(2))) if m else None


def formato_por_aspecto(path):
    d = dimensiones(path)
    if not d:
        return "16:9"
    r = d[0] / d[1]
    return "16:9" if r > 1.3 else "9:16" if r < 0.8 else "1:1"


# ---------- Letra y sincronización ----------

def norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-zñ]+", t)


def leer_letra(path):
    """Devuelve [{text, section}]; ignora el título y las etiquetas [Sección]."""
    lines, section = [], ""
    raw_lines = [l.strip() for l in open(path, encoding="utf8")]
    first = next((l for l in raw_lines if l), "")
    for raw in raw_lines:
        if not raw or (raw == first and not raw.startswith("[") and raw.isupper()):
            continue
        if raw.startswith("[") and raw.endswith("]"):
            section = raw.strip("[]")
            continue
        lines.append({"text": raw.lstrip("#").strip(), "section": section})
    return lines


def leer_titulo(path):
    """Título de la canción si la letra empieza con una línea en mayúsculas."""
    try:
        for l in open(path, encoding="utf8"):
            l = l.strip().lstrip("#").strip()
            if l:
                return l.title() if l.isupper() and not l.startswith("[") else ""
    except OSError:
        pass
    return ""


def audio_duracion_y_pcm(path):
    """Decodifica cualquier audio a mono 16 kHz float32 con ffmpeg."""
    p = run([FFMPEG, "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"], check=True)
    a = np.frombuffer(p.stdout, np.float32)
    return len(a) / 16000, a


def demucs_disponible():
    try:
        import demucs  # noqa: F401
        return True
    except ImportError:
        return False


def separar_voz(audio_path, log):
    """Voz aislada con Demucs, mono 16 kHz (lo que usa Whisper). Se guarda junto al audio."""
    cache = os.path.splitext(audio_path)[0] + ".voz.wav"
    if os.path.exists(cache):
        log(f"Usando voz ya separada: {os.path.basename(cache)}")
        return audio_duracion_y_pcm(cache)[1]
    import wave
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    log("Separando la voz de la música con Demucs (unos 3 minutos; solo la primera vez)…")
    raw = run([FFMPEG, "-v", "error", "-i", audio_path, "-f", "f32le", "-ac", "2", "-ar", "44100", "-"],
              check=True).stdout
    wav = torch.from_numpy(np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy())
    model = get_model("htdemucs"); model.eval()
    ref = wav.mean(0)
    with torch.no_grad():
        src = apply_model(model, ((wav - ref.mean()) / ref.std())[None], device="cpu",
                          split=True, overlap=0.25, progress=False)[0]
    voz = (src[model.sources.index("vocals")] * ref.std() + ref.mean()).mean(0).numpy()
    t16 = np.arange(0, len(voz) / 44100, 1 / 16000)
    voz16 = np.interp(t16, np.arange(len(voz)) / 44100, voz).astype(np.float32)
    with wave.open(cache, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        wf.writeframes((np.clip(voz16, -1, 1) * 32767).astype(np.int16).tobytes())
    log("Voz separada.")
    return voz16


def sincronizar(audio_path, letra_path, modelo, log, voz=False):
    lines = leer_letra(letra_path)
    log(f"Letra: {len(lines)} líneas. Cargando audio…")
    dur, pcm = audio_duracion_y_pcm(audio_path)
    if voz and demucs_disponible():
        pcm = separar_voz(audio_path, log)
    elif voz:
        log("Demucs no está instalado: se sincroniza con la mezcla completa.")
    log(f"Audio: {dur/60:.1f} min. Transcribiendo con Whisper '{modelo}' (puede tardar varios minutos)…")
    from faster_whisper import WhisperModel
    model = WhisperModel(modelo, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(pcm, language="es", word_timestamps=True, beam_size=5,
                               condition_on_previous_text=False)
    wtok, wtime = [], []
    for s in segs:
        log(f"  {s.start:6.1f}s  {s.text.strip()[:60]}")
        for w in s.words:
            for t in norm(w.word):
                wtok.append(t); wtime.append((w.start, w.end))

    ltok, lidx, lpos = [], [], []
    for i, l in enumerate(lines):
        for j, t in enumerate(norm(l["text"])):
            ltok.append(t); lidx.append(i); lpos.append(j)
    hits, por_palabra = {}, {}
    sm = difflib.SequenceMatcher(None, ltok, wtok, autojunk=False)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            hits.setdefault(lidx[a + k], []).append(wtime[b + k])
            por_palabra.setdefault(lidx[a + k], {})[lpos[a + k]] = wtime[b + k]
    for i, l in enumerate(lines):  # tiempo de cada palabra (None si Whisper no la reconoció)
        pp = por_palabra.get(i, {})
        l["palabras"] = [[round(pp[j][0], 2), round(pp[j][1], 2)] if j in pp else None
                         for j in range(len(norm(l["text"])))]
    for i, l in enumerate(lines):
        h = hits.get(i)
        if h and len(h) >= max(1, len(norm(l["text"])) // 3):
            l["start"], l["end"] = round(h[0][0], 2), round(h[-1][1], 2)

    known = [i for i, l in enumerate(lines) if "start" in l]
    for i, l in enumerate(lines):
        if "start" in l:
            continue
        prev = max([k for k in known if k < i], default=None)
        nxt = min([k for k in known if k > i], default=None)
        t0 = lines[prev]["end"] if prev is not None else 0
        t1 = lines[nxt]["start"] if nxt is not None else dur
        lo = prev if prev is not None else -1
        span = (nxt if nxt is not None else len(lines)) - lo
        l["start"] = round(t0 + (t1 - t0) * (i - lo - 1) / span + 0.1, 2)
        l["end"] = round(t0 + (t1 - t0) * (i - lo) / span, 2)
        l["estimado"] = True
    est = sum(1 for l in lines if l.get("estimado"))
    log(f"Sincronización lista ({len(lines) - est} exactas, {est} estimadas).")
    return {"duracion": dur, "lineas": lines}


def ruta_cache(audio):
    return os.path.splitext(audio)[0] + ".lineas.json"


def obtener_sincronizacion(audio, letra, modelo, forzar, log, voz=False):
    cache = ruta_cache(audio)
    if os.path.exists(cache) and not forzar:
        log(f"Usando sincronización guardada: {os.path.basename(cache)}")
        return json.load(open(cache, encoding="utf8"))
    data = sincronizar(audio, letra, modelo, log, voz)
    guardar_sincronizacion(audio, data)
    return data


def guardar_sincronizacion(audio, data):
    json.dump(data, open(ruta_cache(audio), "w", encoding="utf8"), ensure_ascii=False, indent=1)


# ---------- Fragmento corto ----------

def bloques_coro(lines):
    """Lista de (inicio, fin) de cada coro (líneas seguidas con estilo Coro)."""
    bloques, actual = [], None
    for l in lines:
        if estilo_de(l["section"]) == "Coro":
            actual = [l["start"], l["end"]] if actual is None else [actual[0], l["end"]]
        elif actual:
            bloques.append(tuple(actual)); actual = None
    if actual:
        bloques.append(tuple(actual))
    return bloques


def parse_tiempo(txt):
    txt = txt.strip().replace(",", ".")
    if ":" in txt:
        m, s = txt.split(":", 1)
        return int(m) * 60 + float(s)
    return float(txt)


def energia_audio(audio, paso=0.5):
    """Volumen (dB RMS) del audio cada `paso` segundos, leído en mono a baja frecuencia."""
    sr = 8000
    r = run([FFMPEG, "-v", "error", "-i", audio, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"])
    x = np.frombuffer(r.stdout, dtype=np.float32)
    n = int(sr * paso)
    if len(x) < n:
        return np.zeros(1)
    x = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((x ** 2).mean(axis=1)) + 1e-6)


def mejor_parte(audio, data, d, paso=0.5):
    """Inicio de la ventana de `d` s más fuerte de la canción, premiando el coro y
    arrancando al comienzo de un verso para que no empiece a mitad de frase."""
    db = energia_audio(audio, paso)
    total = data["duracion"]
    if total <= d + 1:
        return 0.0
    rel = np.clip((db - np.percentile(db, 10)) / max(1e-6, np.percentile(db, 95) - np.percentile(db, 10)), 0, 1)
    coro = np.zeros(len(db))
    for a, b in bloques_coro(data["lineas"]):
        coro[int(a / paso):int(b / paso) + 1] = 1
    k = int(d / paso)
    acum = lambda v: np.concatenate([[0], np.cumsum(v)])
    cr, cc = acum(rel), acum(coro)
    # candidatos: un poco antes de cada línea (las frases bien cortadas suenan mejor) y, si no hay letra, cada 0,5 s
    inicios = sorted({max(0.0, l["start"] - 0.6) for l in data["lineas"]}) or list(np.arange(0, total - d, paso))
    mejor, puntaje = 0.0, -1.0
    for t in inicios:
        i = int(t / paso)
        if t + d > total or i + k >= len(cr):
            continue
        # energía media + coro presente + subida al arrancar (el "drop") − arrancar muy al inicio
        sube = rel[i:i + int(4 / paso)].mean() - rel[max(0, i - int(4 / paso)):i].mean() if i else 0
        p = (cr[i + k] - cr[i]) / k + 0.6 * (cc[i + k] - cc[i]) / k + 0.3 * max(0, sube) - (0.3 if t < 15 else 0)
        if p > puntaje:
            mejor, puntaje = t, p
    return mejor


def calcular_fragmento(data, cfg, audio=None):
    """(inicio, duración) del corto según la configuración."""
    total = data["duracion"]
    dur_txt = cfg.get("frag_dur", DURACIONES[0])
    modo = cfg.get("frag_modo", FRAGMENTOS[0])
    if modo.startswith("Desde"):
        ini = max(0.0, min(parse_tiempo(cfg.get("frag_inicio") or "0"), total - 5))
        fin_coro = None
    elif modo.startswith("Mejor"):
        d = 30.0 if dur_txt == "Coro completo" else float(dur_txt.split()[0])
        d = max(5.0, min(d, total))
        return round(mejor_parte(audio, data, d), 2), round(d, 2)
    else:
        coros = bloques_coro(data["lineas"])
        if not coros:
            raise RuntimeError("La letra no tiene secciones [Chorus]/[Coro]; usa «Desde un minuto exacto».")
        c = coros[0] if modo == "Primer coro" else coros[-1]
        ini, fin_coro = max(0.0, c[0] - 1.0), c[1] + 1.5
    if dur_txt == "Coro completo":
        d = (fin_coro - ini) if fin_coro else 30.0
    else:
        d = float(dur_txt.split()[0])
    d = max(5.0, min(d, 90.0, total - ini))
    return round(ini, 2), round(d, 2)


def recortar_lineas(lines, ini, d):
    """Líneas visibles dentro del fragmento, con tiempos relativos al inicio."""
    out = []
    for l in lines:
        if l["end"] > ini and l["start"] < ini + d:
            out.append(dict(l, start=max(0.35, l["start"] - ini), end=min(d, l["end"] - ini)))
    return out


# ---------- Tiempos por palabra (karaoke) ----------

def tiempos_palabras(l):
    """[(palabra, inicio, fin)] para cada palabra visible de la línea.

    Usa los tiempos de Whisper cuando existen; el resto se reparte según la longitud
    de las palabras (también sirve para sincronizaciones antiguas sin tiempos por palabra).
    """
    words = l["text"].split()
    if not words:
        return []
    toks = l.get("palabras") or []
    conocidos, fines_w, k = [], [], 0
    for w in words:
        n = len(norm(w))
        rango = [toks[j] for j in range(k, min(k + n, len(toks))) if toks[j]]
        t = rango[0][0] if rango else None
        ok = t is not None and l["start"] - 0.5 <= t <= l["end"] + 0.5
        conocidos.append(float(t) if ok else None)
        fines_w.append(float(rango[-1][1]) if ok else None)  # cuándo termina de cantarse según Whisper
        k += n
    pesos = [len(w) + 1 for w in words]
    acum = [sum(pesos[:i]) for i in range(len(words))]
    total = sum(pesos)
    anclas = [(0, l["start"])] + [(acum[i], t) for i, t in enumerate(conocidos) if t is not None] + [(total, l["end"])]
    inicios = []
    for i in range(len(words)):
        if conocidos[i] is not None:
            t = conocidos[i]
        else:
            izq = max((a for a in anclas if a[0] <= acum[i]), key=lambda a: a[0])
            der = min((a for a in anclas if a[0] > acum[i]), key=lambda a: a[0])
            t = izq[1] + (der[1] - izq[1]) * (acum[i] - izq[0]) / max(1, der[0] - izq[0])
        inicios.append(float(max(t, inicios[-1] + 0.05) if inicios else max(t, l["start"])))
    siguientes = inicios[1:] + [max(l["end"], inicios[-1] + 0.2)]
    # Termina donde Whisper dice que acaba la palabra (si hay pausa después), nunca después de la siguiente
    fines = [min(sig, max(ini + 0.12, f)) if f is not None else sig
             for ini, sig, f in zip(inicios, siguientes, fines_w)]
    return list(zip(words, inicios, fines))


def texto_karaoke(l, a, ajuste=0.0):
    """Texto con etiquetas \\kf: cada palabra se ilumina cuando se canta."""
    partes = []
    pal = [(w, t0 + ajuste, t1 + ajuste) for w, t0, t1 in tiempos_palabras(l)]
    espera = int(round((pal[0][1] - a) * 100)) if pal else 0
    if espera > 0:
        partes.append(f"{{\\k{espera}}}")
    for i, (w, t0, t1) in enumerate(pal):
        cs = max(1, int(round((t1 - t0) * 100)))
        partes.append(f"{{\\kf{cs}}}{limpiar(w)}")
        if i < len(pal) - 1:
            pausa = int(round((pal[i + 1][1] - t1) * 100))  # silencio hasta la siguiente palabra
            partes.append(f"{{\\k{pausa}}} " if pausa > 0 else " ")
    return "".join(partes)


def texto_aparicion(l, a, ajuste=0.0):
    """Cada palabra aparece (fundido rápido) cuando se canta."""
    pal = [(w, t0 + ajuste, t1 + ajuste) for w, t0, t1 in tiempos_palabras(l)]
    partes = []
    for i, (w, t0, _) in enumerate(pal):
        ms = max(0, int((t0 - a) * 1000))
        partes.append(f"{{\\alpha&HFF&\\t({ms},{ms + 180},\\alpha&H00&)}}{limpiar(w)}"
                      + (" " if i < len(pal) - 1 else ""))
    return "".join(partes)


def texto_maquina(l, a):
    """Las letras se escriben una a una desde que empieza la voz."""
    txt = l["text"]
    letras = max(1, len(txt.replace(" ", "")))
    dur = min(max(0.4, l["end"] - l["start"]), letras * 0.07)
    t0 = (l["start"] - a) * 1000
    partes, k = [], 0
    for ch in txt:
        if ch == " ":
            partes.append(" "); continue
        ms = int(t0 + dur * 1000 * k / letras); k += 1
        partes.append(f"{{\\alpha&HFF&\\t({ms},{ms + 1},\\alpha&H00&)}}{limpiar(ch)}")
    return "".join(partes)


# ---------- Subtítulos ASS ----------

def ass_time(t):
    t = max(0, t)
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_color(rgb):  # (r,g,b) -> &HBBGGRR
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}"


def estilo_de(section):
    s = section.lower()
    if s.startswith("chorus") or s.startswith("coro"):
        return "Coro"
    if s.startswith("bridge") or s.startswith("puente"):
        return "Puente"
    if s.startswith("outro") or s.startswith("intro"):
        return "Outro"
    return "Verso"


def zona_texto(pos, w, h):
    """Rectángulo (x0, y0, x1, y1) donde va la letra según la posición."""
    if pos.startswith("Abajo"):
        if h > w:  # vertical: por encima de la descripción y lejos de los botones de TikTok/Reels
            return 0.08 * w, 0.58 * h, 0.84 * w, 0.76 * h
        return 0.08 * w, 0.72 * h, 0.92 * w, 0.95 * h
    if pos == "Derecha":
        return 0.52 * w, 0.30 * h, 0.95 * w, 0.70 * h
    if pos == "Izquierda":
        return 0.05 * w, 0.30 * h, 0.48 * w, 0.70 * h
    return 0.10 * w, 0.35 * h, 0.90 * w, 0.65 * h


def brillo_rect(fondo, w, h, rect):
    """Brillo medio (0-1) del fondo, ya recortado al formato w x h, dentro de rect=(x0, y0, x1, y1)."""
    sw, sh = 96, int(96 * h / w)
    p = run([FFMPEG, "-v", "error", "-i", fondo, "-frames:v", "1", "-vf",
             f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},scale={sw}:{sh},format=gray",
             "-f", "rawvideo", "-"], check=True)
    img = np.frombuffer(p.stdout, np.uint8).reshape(sh, sw)
    x0, y0, x1, y1 = rect
    reg = img[int(y0 / h * sh):max(int(y0 / h * sh) + 1, int(y1 / h * sh)),
              int(x0 / w * sw):max(int(x0 / w * sw) + 1, int(x1 / w * sw))]
    return float(reg.mean()) / 255


def brillo_zona(fondo, w, h, pos):
    """Brillo medio (0-1) del fondo en la zona donde irá la letra."""
    return brillo_rect(fondo, w, h, zona_texto(pos, w, h))


def zona_logo(logo, w, h, cfg):
    """Rectángulo que ocupará el logo (mismos cálculos que grafo_filtros)."""
    d = dimensiones(logo) or (16, 9)
    lw = int(w * cfg.get("logo_tamano", 14) / 100)
    lh = int(lw * d[1] / d[0])
    m = int(min(w, h) * 0.035)
    esquina = cfg.get("logo_esquina", ESQUINAS[0])
    x = m if "izquierda" in esquina else (w - lw) // 2 if "centro" in esquina else w - lw - m
    y = m if esquina.startswith("Arriba") else h - lh - m
    return x, y, x + lw, y + lh


def elegir_logo(fondo, w, h, cfg, claro, oscuro):
    """Con dos versiones, usa la oscura si el fondo detrás del logo es claro, y la clara si es oscuro."""
    if not (claro and oscuro):
        return claro or oscuro, None
    b = brillo_rect(fondo, w, h, zona_logo(claro, w, h, cfg))
    return (oscuro, "oscuro") if b > 0.55 else (claro, "claro")


def elegir_paleta(color, fondo, w, h, pos):
    if color.startswith("Letra clara"):
        return "clara"
    if color.startswith("Letra oscura"):
        return "oscura"
    return "oscura" if brillo_zona(fondo, w, h, pos) > 0.62 else "clara"


def limpiar(t):
    return t.replace("{", "(").replace("}", ")")


def crear_ass(lines, w, h, cfg, paleta, path, muestra=None, paleta_titulo=None):
    """Genera los subtítulos. `muestra` = una sola línea de ejemplo (vista previa)."""
    base = min(w, h) * cfg["tamano"] / 100
    escala = {"Verso": (1.0, 0), "Coro": (1.15, 0), "Puente": (1.08, 0), "Outro": (0.92, 1)}
    if paleta == "oscura":  # letra oscura con halo claro suave
        borde, sombra = "&H28F0F4F8", "&HA0FFFFFF"
    else:                   # letra clara con borde y sombra oscuros
        borde, sombra = "&H60000000", "&H90000000"
    x0, y0, x1, y1 = zona_texto(cfg["pos"], w, h)
    ml, mr = int(x0), int(w - x1)
    abajo = cfg["pos"].startswith("Abajo")
    fmt = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
           "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
           "Alignment, MarginL, MarginR, MarginV, Encoding")
    out = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
           "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", fmt]
    sizes = {}
    for name, (k, it) in escala.items():
        size = int(base * 0.075 * k)
        sizes[name] = size
        color = ass_color(PALETAS[paleta][name])
        r_, g_, b_ = PALETAS[paleta][name]  # tono apagado (mezcla con gris) y semitransparente
        sin_cantar = "&HA0" + ass_color(((r_ + 150) // 2, (g_ + 150) // 2, (b_ + 150) // 2))[4:]
        out.append(f"Style: {name},{cfg['fuente']},{size},{color},{sin_cantar},"
                   f"{borde},{sombra},0,{it},0,0,100,100,0,0,1,{max(2, size // 14)},{max(1, size // 25)},"
                   f"{2 if abajo else 5},{ml},{mr},{int(h - y1)},1")
    tsize = int(min(w, h) * 0.11)
    pt = paleta_titulo or paleta
    tborde, tsombra = ("&H28F0F4F8", "&HA0FFFFFF") if pt == "oscura" else ("&H60000000", "&H90000000")
    out.append(f"Style: Titulo,{cfg['fuente']},{tsize},{ass_color(PALETAS[pt]['Titulo'])},&H000000FF,"
               f"{tborde},{tsombra},0,0,0,0,100,100,0,0,1,{max(2, tsize // 16)},{max(1, tsize // 30)},"
               f"5,{int(w * 0.06)},{int(w * 0.06)},0,1")
    out += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    if muestra:
        muestras = [muestra] if isinstance(muestra, str) else list(muestra)
        lines = [{"text": m, "section": "Chorus", "start": 0.4 + 9 * i, "end": 9 + 9 * i}
                 for i, m in enumerate(muestras)]

    # Título de apertura
    if cfg.get("titulo_on") and cfg.get("titulo", "").strip():
        primera = lines[0]["start"] if lines else 6
        fin = 9 if muestra else max(2.5, min(6.0, primera - 0.4))
        sub = cfg.get("subtitulo", "").strip()
        ssize = int(tsize * 0.36)
        txt = (f"{{\\fs{ssize}\\fsp{int(ssize * 0.35)}}}{limpiar(sub.upper())}\\N{{\\fs{tsize}\\fsp0}}" if sub else "")
        txt += limpiar(cfg["titulo"].strip())
        cy = int(h * 0.42) if abajo else int(h * 0.5)
        out.append(f"Dialogue: 1,{ass_time(0.3)},{ass_time(fin)},Titulo,,0,0,0,,"
                   f"{{\\fad(700,700)\\pos({w // 2},{cy})}}{txt}")

    efecto = cfg.get("efecto", EFECTOS[0])
    karaoke = efecto.startswith("Karaoke")
    dos = "dos líneas" in efecto.lower()
    cx = int((x0 + x1) / 2)
    cy = int(y1) if abajo else int((y0 + y1) / 2)
    rise = int(base * 0.008)
    ancho = x1 - x0
    NEXT_ESC = 0.78  # tamaño relativo de la línea siguiente

    def alto(texto, size):
        por_fila = max(8, int(ancho / (size * 0.46)))
        filas = max(1, -(-len(texto) // por_fila))
        return filas * size * 1.22

    for i, l in enumerate(lines):
        sig = lines[i + 1] if i + 1 < len(lines) else None
        nxt = sig["start"] if sig else l["end"] + 4
        a = l["start"] - 0.35
        b = min(l["end"] + 0.6, nxt - 0.05)
        estilo = estilo_de(l["section"])
        if estilo == "Outro":
            b = min(l["end"] + 1.5, nxt) if sig else l["end"] + 2.5
        dur_ms = int((b - a) * 1000)
        if karaoke:
            texto = texto_karaoke(l, a, cfg.get("karaoke_ajuste", 0.0))
        elif efecto.startswith("Palabra"):
            texto = texto_aparicion(l, a, cfg.get("karaoke_ajuste", 0.0))
        elif efecto.startswith("Máquina"):
            texto = texto_maquina(l, a)
        else:
            texto = limpiar(l["text"])
        # El halo del brillo aparece junto con cada palabra/letra (solo el borde difuso, nunca la letra)
        texto_halo = (texto.replace("\\alpha&H00&", "\\3a&H70&")
                      if efecto.startswith(("Palabra", "Máquina")) else limpiar(l["text"]))

        mostrar_sig = dos and sig and sig["start"] - l["end"] < 5
        y = cy
        if mostrar_sig:
            est_sig = estilo_de(sig["section"])
            h_sig = alto(sig["text"], sizes[est_sig] * NEXT_ESC)
            h_act = alto(l["text"], sizes[estilo])
            sep = sizes[estilo] * 0.35
            if abajo:  # la siguiente abajo del todo, la actual encima
                y_sig, y = cy, int(cy - h_sig - sep)
            else:
                total = h_act + sep + h_sig
                y = int(cy - total / 2 + h_act / 2)
                y_sig = int(cy + total / 2 - h_sig / 2)
            an = "\\an2" if abajo else "\\an5"
            out.append(f"Dialogue: 0,{ass_time(a + 0.15)},{ass_time(b)},{est_sig},,0,0,0,,"
                       f"{{{an}\\pos({cx},{y_sig})\\fad(250,200)\\fscx{int(NEXT_ESC * 100)}"
                       f"\\fscy{int(NEXT_ESC * 100)}\\alpha&H55&}}{limpiar(sig['text'])}")
        if muestra:
            fx = "\\fad(350,400)" + (f"\\pos({cx},{y})" if mostrar_sig else "")
        else:
            fx = f"\\fad(350,400)\\move({cx},{y + rise},{cx},{y - rise},0,{dur_ms})"
        if cfg.get("pop_on"):  # entra un poco más chica y crece rápido a su tamaño
            fx += "\\fscx84\\fscy84\\t(0,260,0.6,\\fscx100\\fscy100)"
        if cfg.get("brillo_on") and estilo == "Coro":  # halo difuso detrás de la letra del coro
            halo = "&HB4E2FF&" if paleta == "clara" else "&HF0F8FF&"
            gs = sizes[estilo]
            out.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},{estilo},,0,0,0,,"
                       f"{{{fx}\\1a&HFF&\\3c{halo}\\3a&H70&\\4a&HFF&\\bord{max(4, gs // 5)}\\blur{max(4, gs // 4)}}}"
                       f"{texto_halo}")
        out.append(f"Dialogue: 1,{ass_time(a)},{ass_time(b)},{estilo},,0,0,0,,{{{fx}}}{texto}")
    open(path, "w", encoding="utf8").write("\n".join(out))


# ---------- Tono de color del fondo ----------

def virado(tinte, conserva):
    """Matriz colorchannelmixer: mezcla el color original (`conserva`) con la luminancia teñida de `tinte`."""
    s, (tr, tg, tb) = conserva, tinte
    filas = []
    for i, t in enumerate((tr, tg, tb)):
        coef = [(1 - s) * 0.299 * t, (1 - s) * 0.587 * t, (1 - s) * 0.114 * t]
        coef[i] += s
        filas.append(":".join(f"{c:.3f}" for c in coef) + ":0")
    return "colorchannelmixer=" + ":".join(filas)


TONOS = {  # nombre: filtros de ffmpeg (virado de color + contraste)
    "Dorado cálido": virado((1.25, 1.0, 0.62), 0.45) + ",eq=saturation=1.1",
    "Verde sereno": virado((0.88, 1.1, 0.95), 0.55),
    "Nocturno azul": virado((0.68, 0.85, 1.35), 0.35) + ",eq=brightness=-0.08:gamma=0.9",
    "Rojo rock": virado((1.45, 0.72, 0.62), 0.3) + ",eq=contrast=1.2:brightness=-0.05",
    "Cine (azul y naranja)": "colorbalance=rs=-.22:gs=-.02:bs=.25:rh=.25:gh=.06:bh=-.22,eq=contrast=1.1",
    "Sepia nostálgico": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,eq=contrast=0.95",
    "Pastel lavado": "eq=saturation=0.65:contrast=0.88:brightness=0.05," + virado((1.08, 1.0, 1.08), 0.7),
    "Tropical vibrante": "eq=saturation=1.6:contrast=1.06,colorbalance=rs=.08:bs=.12:rh=.12:bh=-.05",
    "Magenta y oro": virado((1.3, 0.8, 1.08), 0.4) + ",colorbalance=rh=.15:gh=.05:bh=-.15,eq=saturation=1.2",
    "Blanco y negro": "hue=s=0,eq=contrast=1.1",
}
TONO_GENERO = {
    "Proyecto Resucitado (sereno, luminoso)": "Verde sereno",
    "Balada rock": "Dorado cálido",
    "Rock": "Rojo rock",
    "Rock de estadios": "Cine (azul y naranja)",
    "Rock argentino": "Nocturno azul",
    "Bolero": "Sepia nostálgico",
    "Indie": "Pastel lavado",
    "Brasileña (bossa nova / MPB)": "Tropical vibrante",
    "Bollywood": "Magenta y oro",
}
TONO_OPCIONES = ["Original", "Automático según género", *TONOS]


def tono_efectivo(cfg):
    """Nombre del tono a aplicar, o None para dejar el fondo como está."""
    t = cfg.get("tono", "Original")
    if t == "Automático según género":
        t = TONO_GENERO.get(cfg.get("genero", ""), "Original")
    return t if t in TONOS and cfg.get("tono_intensidad", 70) > 0 else None


def cadena_tono(cfg, etiqueta="t"):
    """Trozo de filter_complex que mezcla el original con su versión entonada según la intensidad."""
    t = tono_efectivo(cfg)
    if not t:
        return ""
    i = cfg.get("tono_intensidad", 70) / 100
    return (f",format=gbrp,split[{etiqueta}a][{etiqueta}b];[{etiqueta}b]{TONOS[t]},format=gbrp[{etiqueta}g];"
            f"[{etiqueta}g][{etiqueta}a]blend=all_mode=normal:all_opacity={i:.2f}")  # arriba va el entonado


def aplicar_tono(fondo, w, h, cfg, carpeta):
    """Imagen de fondo ya recortada al formato y con el tono aplicado (los videos se entonan al exportar)."""
    if not tono_efectivo(cfg) or fondo.lower().endswith(VIDEO_EXT):
        return fondo
    out = os.path.join(carpeta, f"fondo_tono_{w}x{h}.png")
    r = run([FFMPEG, "-y", "-v", "error", "-i", fondo, "-frames:v", "1", "-filter_complex",
             f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}{cadena_tono(cfg)}",
             "-compression_level", "1", out])
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf8", "ignore")[-400:])
    return out


# ---------- Logo sin fondo ----------

def leer_rgba(path):
    d = dimensiones(path)
    if not d:
        raise RuntimeError(f"No se pudo leer la imagen {os.path.basename(path)}")
    raw = run([FFMPEG, "-v", "error", "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
              check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(d[1], d[0], 4).astype(np.float32)


def logo_limpio(path):
    """(ruta, aviso). Si el logo no es transparente y tiene fondo negro o blanco liso, lo vuelve transparente.

    Fondo negro: la opacidad sale del brillo (ideal para logos claros o plateados, conserva el resplandor).
    Fondo blanco: la opacidad sale de lo oscuro (logos oscuros). Se guarda en caché junto a la app.
    """
    img = leer_rgba(path)
    if img[..., 3].min() < 250:
        return path, ""  # ya trae transparencia real
    borde = np.concatenate([img[:8].reshape(-1, 4), img[-8:].reshape(-1, 4),
                            img[:, :8].reshape(-1, 4), img[:, -8:].reshape(-1, 4)])[:, :3]
    media, desv = borde.mean(), borde.std()
    if desv > 18:
        return path, ("El logo no es transparente y su fondo no es liso (¿cuadriculado pintado?). "
                      "Pídelo sobre fondo negro sólido.")
    if media > 90 and media < 200:
        return path, "El logo no es transparente y su fondo es gris. Pídelo sobre fondo negro sólido."
    cache_dir = os.path.join(APP_DIR, "cache"); os.makedirs(cache_dir, exist_ok=True)
    import hashlib
    clave = hashlib.md5(f"{os.path.abspath(path)}|{os.path.getmtime(path)}".encode()).hexdigest()[:12]
    out = os.path.join(cache_dir, f"logo_{clave}.png")
    if not os.path.exists(out):
        rgb = img[..., :3]
        if media <= 90:  # fondo negro
            a = rgb.max(axis=2)
            color = np.where(a[..., None] > 0, rgb * 255 / np.maximum(a[..., None], 1), 0)
        else:            # fondo blanco
            a = 255 - rgb.min(axis=2)
            af = np.maximum(a[..., None], 1) / 255
            color = np.where(a[..., None] > 0, (rgb - 255 * (1 - af)) / af, 0)
        a = np.where(a < 10, 0, a)  # limpia el ruido del fondo
        rgba = np.dstack([np.clip(color, 0, 255), a]).astype(np.uint8)
        filas, cols = np.where(a > 10)
        if len(filas):  # recorta el margen vacío para que el tamaño elegido sea el del logo
            m = 6
            rgba = rgba[max(0, filas.min() - m):filas.max() + m + 1, max(0, cols.min() - m):cols.max() + m + 1]
            rgba = np.ascontiguousarray(rgba)
        h, w = rgba.shape[:2]
        p = subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                            "-i", "-", out], input=rgba.tobytes(), capture_output=True, creationflags=NO_WINDOW)
        if p.returncode:
            raise RuntimeError(p.stderr.decode("utf8", "ignore")[-300:])
    return out, ""


# ---------- Video ----------

SUPER = 4  # el zoom trabaja sobre la imagen a 4x: cada paso es 1/4 de píxel y el movimiento se ve continuo


def preparar_zoom(fondo, w, h, carpeta):
    """Copia del fondo ya recortada al formato y ampliada SUPER veces (se hace una sola vez)."""
    out = os.path.join(carpeta, "fondo_zoom.png")
    W, H = w * SUPER, h * SUPER
    r = run([FFMPEG, "-y", "-v", "error", "-i", fondo, "-frames:v", "1", "-vf",
             f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}",
             "-compression_level", "1", out])
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf8", "ignore")[-400:])
    return out


def grafo_filtros(w, h, paleta, cfg, es_video, hay_logo, dur=10.0):
    """filter_complex: fondo (con zoom lento opcional) + logo opcional + letra."""
    if cfg.get("zoom_on") and not es_video:
        # Acercamiento lento y constante (Ken Burns) sobre la copia ampliada de preparar_zoom()
        frames = max(1, int(dur * 30))
        z = min(0.08, max(0.03, 0.004 * dur))
        # la imagen ampliada se decodifica una sola vez y se repite en memoria (loop), no desde disco
        fondo = (f"loop=loop={frames}:size=1:start=0,setpts=N/30/TB,"
                 f"zoompan=z='1+{z:.3f}*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
                 f":d=1:s={w}x{h}:fps=30")
    else:
        fondo = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    # Las imágenes llegan ya entonadas (aplicar_tono); los videos se entonan aquí, cuadro a cuadro
    g = f"[0:v]{fondo}{cadena_tono(cfg) if es_video else ''},setsar=1[bg]"
    actual = "bg"
    if hay_logo:
        lw = int(w * cfg.get("logo_tamano", 14) / 100) // 2 * 2
        m = int(min(w, h) * 0.035)
        esquina = cfg.get("logo_esquina", ESQUINAS[0])
        x = m if "izquierda" in esquina else "(W-w)/2" if "centro" in esquina else f"W-w-{m}"
        y = m if esquina.startswith("Arriba") else f"H-h-{m}"
        op = cfg.get("logo_opacidad", 85) / 100
        g += (f";[2:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={op:.2f}[lg]"
              f";[bg][lg]overlay=x={x}:y={y}:format=auto[bgl]")
        actual = "bgl"
    ajuste = "eq=brightness=-0.06," if paleta == "clara" else ""
    fonts = FONTS_DIR.replace("\\", "/").replace(":", "\\:")
    g += f";[{actual}]{ajuste}format=yuv420p,ass=subs.ass:fontsdir='{fonts}'[v]"
    return g


def entradas(fondo, audio, logo, inicio=0.0, zoom=False):
    es_video = fondo.lower().endswith(VIDEO_EXT)
    if es_video:
        ent = ["-stream_loop", "-1", "-i", fondo]
    elif zoom:  # una sola imagen; el filtro loop la repite en memoria
        ent = ["-i", fondo]
    else:
        ent = ["-loop", "1", "-framerate", "30", "-i", fondo]
    if audio:
        ent += (["-ss", f"{inicio:.2f}"] if inicio else []) + ["-i", audio]
    else:
        ent += ["-f", "lavfi", "-i", "anullsrc"]
    if logo:
        ent += ["-i", logo]
    return es_video, ent


def vista_previa(fondo, logo, ass_path, w, h, paleta, cfg, png):
    """Un fotograma con la letra de muestra, reducido para mostrar en la ventana."""
    # Un solo fotograma: el movimiento del fondo no se nota y prepararlo es lento, así que se omite aquí
    cfg = dict(cfg, zoom_on=False)
    es_video, ent = entradas(fondo, None, logo)
    g = grafo_filtros(w, h, paleta, cfg, es_video, bool(logo))
    g = g[:-3] + f",scale=-2:{360 if w >= h else 480}[v]"
    r = run([FFMPEG, "-y", "-v", "error", *ent, "-filter_complex", g, "-map", "[v]",
             "-ss", "1.5", "-frames:v", "1", png], cwd=os.path.dirname(ass_path))
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf8", "ignore")[-400:])


def render(fondo, audio, logo, ass_path, w, h, dur, salida, paleta, cfg, log, progreso, inicio=None):
    """inicio=None: canción completa; inicio=segundos: fragmento de `dur` segundos con fundidos."""
    corto = inicio is not None
    if cfg.get("zoom_on") and not fondo.lower().endswith(VIDEO_EXT):
        log("Preparando el fondo para el movimiento suave…")
        fondo = preparar_zoom(fondo, w, h, os.path.dirname(ass_path))
    es_video, ent = entradas(fondo, audio, logo, inicio or 0.0, zoom=bool(cfg.get("zoom_on")))
    g = grafo_filtros(w, h, paleta, cfg, es_video, bool(logo), dur)
    fundido = []
    if corto:
        g = g[:-3] + f",fade=t=in:d=0.4,fade=t=out:st={dur - 1.2:.2f}:d=1.2[v]"
        fundido = ["-af", f"afade=t=in:d=0.6,afade=t=out:st={dur - 1.8:.2f}:d=1.8"]
    quieta = not es_video and not cfg.get("zoom_on")
    cmd = [FFMPEG, "-y", "-v", "error", "-progress", "pipe:1", *ent,
           "-filter_complex", g, "-map", "[v]", "-map", "1:a", "-r", "30",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           *(["-tune", "stillimage"] if quieta else []),
           *fundido, "-c:a", "aac", "-b:a", "320k", "-t", f"{dur if corto else dur + 0.5:.2f}", "-shortest",
           "-movflags", "+faststart", salida]
    p = subprocess.Popen(cmd, cwd=os.path.dirname(ass_path), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, creationflags=NO_WINDOW)
    for line in p.stdout:
        if line.startswith("out_time_us="):
            try:
                progreso(min(1.0, int(line.split("=")[1]) / 1e6 / dur))
            except ValueError:
                pass
    err = p.stderr.read()
    if p.wait() != 0:
        raise RuntimeError(err[-1500:])
    progreso(1.0)


def exportar_audio(audio, ini, dur, salida):
    """Fragmento de audio con fundidos, en MP3 320 kbps (para subir como sonido a TikTok o CapCut)."""
    r = run([FFMPEG, "-y", "-v", "error", "-ss", f"{ini:.2f}", "-t", f"{dur:.2f}", "-i", audio,
             "-af", f"afade=t=in:d=0.6,afade=t=out:st={dur - 1.8:.2f}:d=1.8",
             "-c:a", "libmp3lame", "-b:a", "320k", salida])
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf8", "ignore")[-800:])


# ---------- Editor de tiempos ----------

class EditorTiempos(tk.Toplevel):
    """Tabla con inicio/fin/texto de cada línea; doble clic para editar."""

    def __init__(self, app, audio, data):
        super().__init__(app)
        self.app, self.audio, self.data = app, audio, json.loads(json.dumps(data))
        self.title("Editor de tiempos — " + os.path.basename(audio))
        self.geometry("880x560")
        top = ttk.Frame(self, padding=8); top.pack(fill="x")
        ttk.Label(top, text="Doble clic en una línea para corregirla. Las marcadas en naranja fueron estimadas.").pack(side="left")
        cols = ("n", "inicio", "fin", "seccion", "texto")
        self.tv = ttk.Treeview(self, columns=cols, show="headings")
        for c, t, wd in zip(cols, ("#", "Inicio", "Fin", "Sección", "Letra"), (40, 80, 80, 150, 500)):
            self.tv.heading(c, text=t); self.tv.column(c, width=wd, anchor="w" if c == "texto" else "center")
        self.tv.tag_configure("est", background="#ffe2bf")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True, padx=(8, 0))
        sb.pack(side="left", fill="y")
        self.tv.bind("<Double-1>", self.editar)
        bot = ttk.Frame(self, padding=8); bot.pack(side="bottom", fill="x", before=self.tv)
        ttk.Label(bot, text="Mover todo:").pack(side="left")
        for d in (-0.5, -0.1, 0.1, 0.5):
            ttk.Button(bot, text=f"{d:+.1f} s", width=7, command=lambda d=d: self.desplazar(d)).pack(side="left", padx=2)
        ttk.Button(bot, text="Guardar", command=self.guardar).pack(side="right", padx=4)
        ttk.Button(bot, text="Cancelar", command=self.destroy).pack(side="right")
        self.refrescar()

    @staticmethod
    def fmt(t):
        m, s = divmod(t, 60)
        return f"{int(m)}:{s:05.2f}"

    @staticmethod
    def parse(txt):
        txt = txt.strip().replace(",", ".")
        if ":" in txt:
            m, s = txt.split(":", 1)
            return int(m) * 60 + float(s)
        return float(txt)

    def refrescar(self):
        self.tv.delete(*self.tv.get_children())
        for i, l in enumerate(self.data["lineas"]):
            self.tv.insert("", "end", iid=str(i), tags=("est",) if l.get("estimado") else (),
                           values=(i + 1, self.fmt(l["start"]), self.fmt(l["end"]), l["section"], l["text"]))

    def desplazar(self, d):
        for l in self.data["lineas"]:
            l["start"] = round(max(0, l["start"] + d), 2); l["end"] = round(max(0, l["end"] + d), 2)
        self.refrescar()

    def editar(self, ev):
        sel = self.tv.focus()
        if not sel:
            return
        i = int(sel); l = self.data["lineas"][i]
        d = tk.Toplevel(self); d.title(f"Línea {i + 1}"); d.transient(self); d.grab_set()
        vs = {"Inicio (m:ss.cc)": tk.StringVar(value=self.fmt(l["start"])),
              "Fin (m:ss.cc)": tk.StringVar(value=self.fmt(l["end"])),
              "Letra": tk.StringVar(value=l["text"])}
        for r, (k, v) in enumerate(vs.items()):
            ttk.Label(d, text=k).grid(row=r, column=0, sticky="w", padx=8, pady=4)
            ttk.Entry(d, textvariable=v, width=60 if k == "Letra" else 12).grid(row=r, column=1, sticky="w", padx=8)

        def ok():
            try:
                a, b = self.parse(vs["Inicio (m:ss.cc)"].get()), self.parse(vs["Fin (m:ss.cc)"].get())
            except ValueError:
                messagebox.showwarning("Tiempo inválido", "Usa el formato m:ss.cc, por ejemplo 1:02.50", parent=d)
                return
            if b <= a:
                messagebox.showwarning("Tiempo inválido", "El fin debe ser después del inicio.", parent=d)
                return
            l.update(start=round(a, 2), end=round(b, 2), text=vs["Letra"].get().strip())
            l.pop("estimado", None)
            d.destroy(); self.refrescar(); self.tv.see(str(i)); self.tv.selection_set(str(i))
        ttk.Button(d, text="Aceptar", command=ok).grid(row=3, column=1, sticky="e", padx=8, pady=8)
        d.bind("<Return>", lambda e: ok())

    def guardar(self):
        guardar_sincronizacion(self.audio, self.data)
        self.app.log(f"Tiempos guardados en {os.path.basename(ruta_cache(self.audio))}")
        self.destroy()


# ---------- Prompts para fondos con IA ----------

GENEROS = {
    "Proyecto Resucitado (sereno, luminoso)":
        "Paisaje sereno al amanecer: niebla, agua calma, tierra agrietada de la que brota vida, luz dorada "
        "difusa que atraviesa nubes. Pictórico y luminoso. Paleta: verde salvia, dorado suave, crema.",
    "Balada rock":
        "Escenario vacío al final de un concierto: un solo foco cálido sobre un micrófono con pie y una guitarra "
        "eléctrica apoyada en un amplificador, humo suave en el haz de luz, el resto en penumbra, luces de fondo "
        "desenfocadas como estrellas. Íntimo, emotivo, cinematográfico, grano de película y leve viñeta. "
        "Paleta: ámbar cálido, azul medianoche, negro profundo, destellos dorados.",
    "Rock":
        "Fotografía nocturna de escenario vacío, humo denso, luces cenitales rojas y ámbar, amplificadores y "
        "cables en penumbra, grano de película, alto contraste. Paleta: negro, rojo sangre, ámbar.",
    "Rock de estadios":
        "Estadio gigante visto desde el escenario, multitud desenfocada como mar de luces, haces de luz cruzando "
        "el humo, confeti dorado suspendido, sensación épica. Paleta: azul profundo, blanco frío, destellos dorados.",
    "Rock argentino":
        "Calle de Buenos Aires de noche después de la lluvia: adoquines mojados, faroles amarillos, fachadas "
        "antiguas, persianas cerradas, cables cruzando, reflejos en el piso. Foto analógica de los 80, grano y "
        "leve viñeta. Paleta: amarillo sodio, verde botella, gris piedra.",
    "Bolero":
        "Salón antiguo iluminado con velas, cortinas de terciopelo, una rosa roja sobre un piano, luz cálida muy "
        "suave y desenfocada (bokeh), ambiente íntimo y nostálgico. Paleta: borgoña, dorado viejo, sepia.",
    "Indie":
        "Habitación con luz de tarde entrando por la ventana, plantas, polvo flotando en el aire, textura de "
        "película 35 mm, colores lavados, sensación melancólica y tranquila. Paleta: melocotón, verde salvia, crema.",
    "Brasileña (bossa nova / MPB)":
        "Atardecer en una playa tropical con palmeras recortadas contra el cielo, mar calmo, luz dorada rasante, "
        "ilustración con texturas de papel y formas suaves estilo tropicalia. "
        "Paleta: turquesa, naranja mango, verde selva, amarillo sol.",
    "Bollywood":
        "Palacio indio al atardecer con arcos tallados, telas de seda flotando, pétalos de caléndula en el aire, "
        "faroles encendidos, ornamentos dorados, ambiente festivo y cinematográfico. "
        "Paleta: magenta, azafrán, dorado, turquesa.",
}

# raíz de palabra (sin tildes) -> imagen que sugiere para el fondo
MOTIVOS = [
    (("barro", "lodo", "fango", "tierra", "polvo"), "barro y tierra agrietada"),
    (("rio", "agua", "mar", "ola", "lluvi", "llor", "lagrim"), "agua (río, lluvia o mar)"),
    (("noche", "oscur", "sombra", "luna"), "la noche y la penumbra"),
    (("sol", "luz", "amanec", "manana", "dia", "brill"), "luz de amanecer"),
    (("mano", "abraz", "contigo", "juntos", "lado"), "compañía (algo que acompaña, una mano tendida)"),
    (("peso", "carg", "cansan", "fuerza"), "el peso y el cansancio"),
    (("camin", "senda", "andar", "paso", "ruta"), "un camino"),
    (("cielo", "estrell", "nube", "volar", "alas"), "el cielo abierto"),
    (("fuego", "arde", "llama", "quema"), "fuego y brasas"),
    (("flor", "semilla", "brot", "raiz", "jardin", "renac"), "algo que brota y renace"),
    (("ciudad", "calle", "esquina", "barrio"), "la ciudad"),
    (("corazon", "amor", "beso", "piel"), "el amor"),
    (("silencio", "solo", "soledad", "vacio"), "la soledad y el silencio"),
]
TRISTE = ("triste", "cansan", "peso", "dolor", "llor", "solo", "vacio", "rendir", "nada", "oscur", "perd")
ESPERANZA = ("esperanz", "luz", "renac", "contigo", "mano", "vida", "sigue", "fuerza", "salir", "amanec")


def detectar_tema(letra_path):
    """Frase con las imágenes y el ánimo dominantes de la letra (para el prompt)."""
    try:
        texto = " ".join(l["text"] for l in leer_letra(letra_path))
    except OSError:
        return ""
    plano = " ".join(norm(texto))
    palabras = plano.split()

    def cuenta(raices):
        return sum(1 for w in palabras for r in raices if w.startswith(r))

    puntajes = sorted(((cuenta(r), m) for r, m in MOTIVOS), reverse=True)
    motivos = [m for n, m in puntajes if n >= 2][:3]
    triste, esper = cuenta(TRISTE), cuenta(ESPERANZA)
    if triste and esper and min(triste, esper) / max(triste, esper) > 0.35:
        animo = "La canción va de la tristeza y el agotamiento hacia la esperanza."
    elif triste > esper:
        animo = "Canción melancólica e introspectiva."
    elif esper:
        animo = "Canción luminosa y esperanzadora."
    else:
        animo = "Canción emotiva."
    return animo + (f" Imágenes de la letra: {', '.join(motivos)}." if motivos else "")


ZONAS_PROMPT = {
    "16:9": ("horizontal 16:9", "el 25% inferior de la imagen"),
    "9:16": ("vertical 9:16 (recompón la escena para vertical, no la recortes)",
             "la franja entre el 55% y el 78% de la altura (el 20% inferior lo tapa la interfaz de TikTok)"),
    "1:1": ("cuadrado 1:1", "el 25% inferior de la imagen"),
}


DESTINOS_IA = ["ChatGPT", "Canva"]


def armar_prompt_canva(genero, tema, fmt, notas=""):
    """Versión corta y en positivo: Canva entiende mejor la escena que las reglas."""
    partes = [GENEROS[genero]]
    m = re.search(r"Imágenes de la letra: (.+?)\.?$", tema or "")
    if m:
        partes.append(f"Inspirado en: {m.group(1)}.")
    if notas.strip():
        partes.append(notas.strip().rstrip(".") + ".")
    abajo = "en la zona media-baja" if fmt == "9:16" else "en la parte inferior"
    partes.append(f"Fondo limpio, solo paisaje, con espacio vacío y suave {abajo} y arriba al centro.")
    return " ".join(partes)


def armar_prompt(titulo, genero, tema, fmt, notas="", destino="ChatGPT"):
    if destino == "Canva":
        return armar_prompt_canva(genero, tema, fmt, notas)
    nombre, zona = ZONAS_PROMPT[fmt]
    partes = [
        f"Crea una imagen de fondo para un video con la letra de la canción"
        f"{' «' + titulo + '»' if titulo else ''} (género: {genero.split(' (')[0].lower()}).",
    ]
    if tema:
        partes.append(f"Tema de la letra: {tema} Que la escena lo sugiera de forma sutil, sin ser literal.")
    partes += [
        f"Estilo: {GENEROS[genero]}",
        f"Formato: {nombre}.",
        "Reglas obligatorias:",
        "- Sin ningún texto, letras, números, logos, firmas ni marcas de agua.",
        "- Centro superior despejado (ahí irá un logo).",
        f"- Tonos parejos y poca textura en la zona de la letra para que se lea bien: {zona}.",
        "- Sin personas reconocibles ni marcas comerciales.",
    ]
    if notas.strip():
        partes.append(f"Además: {notas.strip()}")
    return "\n".join(partes)


# ---------- Interfaz ----------

class App(BaseTk):
    CAMPOS = {  # clave: (etiqueta, tipos de archivo)
        "fondo_16:9": ("Fondo 16:9", "img"), "fondo_9:16": ("Fondo 9:16", "img"),
        "fondo_1:1": ("Fondo 1:1", "img"), "audio": ("Audio", "audio"), "letra": ("Letra (.md / .txt)", "txt"),
    }
    TIPOS = {"img": [("Imagen o video", "*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.mkv *.webm")],
             "audio": [("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac")],
             "txt": [("Texto", "*.md *.txt")], "png": [("Imagen PNG", "*.png *.webp")]}

    def __init__(self):
        super().__init__()
        self.title("Video Lyrics")
        self.geometry("1260x700")
        self.cfg_guardada = self.cargar_config()
        c = self.cfg_guardada
        self.v = {k: tk.StringVar() for k in (*self.CAMPOS, "salida", "logo", "logo_oscuro")}
        self.v["logo"].set(c.get("logo", ""))
        self.v["logo_oscuro"].set(c.get("logo_oscuro", ""))
        self.fmt_on = {k: tk.BooleanVar(value=k in c.get("formatos", ["16:9"])) for k in FORMATOS}
        self.prev_fmt = tk.StringVar(value=c.get("prev_fmt", "16:9"))
        self.modelo = tk.StringVar(value=c.get("modelo", "medium"))
        self.fuente = tk.StringVar(value=c.get("fuente", "Cormorant Garamond"))
        self.pos = tk.StringVar(value=c.get("pos", POSICIONES[0]))
        self.color = tk.StringVar(value=c.get("color", COLORES[0]))
        self.tamano = tk.IntVar(value=c.get("tamano", 100))
        self.efecto = tk.StringVar(value=c.get("efecto", EFECTOS[0]))
        self.pop_on = tk.BooleanVar(value=c.get("pop_on", False))
        self.brillo_on = tk.BooleanVar(value=c.get("brillo_on", False))
        self.karaoke_ajuste = tk.DoubleVar(value=c.get("karaoke_ajuste", 0.0))
        self.tono = tk.StringVar(value=c.get("tono", "Original"))
        self.tono_intensidad = tk.IntVar(value=c.get("tono_intensidad", 70))
        self.separar_voz = tk.BooleanVar(value=c.get("separar_voz", False))
        self.resinc = tk.BooleanVar(value=False)
        self.titulo_on = tk.BooleanVar(value=c.get("titulo_on", False))
        self.titulo = tk.StringVar()
        self.subtitulo = tk.StringVar(value=c.get("subtitulo", "Sencillo"))
        self.zoom_on = tk.BooleanVar(value=c.get("zoom_on", False))
        self.logo_on = tk.BooleanVar(value=c.get("logo_on", False))
        self.logo_esquina = tk.StringVar(value=c.get("logo_esquina", ESQUINAS[0]))
        self.logo_tamano = tk.IntVar(value=c.get("logo_tamano", 14))
        self.logo_opacidad = tk.IntVar(value=c.get("logo_opacidad", 85))
        self.exportar = tk.StringVar(value=c.get("exportar", EXPORTAR[0]))
        self.frag_modo = tk.StringVar(value=c.get("frag_modo", FRAGMENTOS[0]))
        self.frag_dur = tk.StringVar(value=c.get("frag_dur", DURACIONES[0]))
        self.frag_inicio = tk.StringVar(value=c.get("frag_inicio", "1:00"))
        self.ultima = c.get("ultima_carpeta", os.path.expanduser("~"))
        self._prev_job = None
        self._titulo_auto = ""

        self.zona = tk.Label(self, text="Arrastra aquí fondos, audio y letra (puedes soltarlos todos juntos).\n"
                                        "Cada imagen va sola al formato que le corresponde según su forma.",
                             relief="groove", bd=2, height=3, bg="#f3ede4", fg="#5a4630", font=("Segoe UI", 10))
        self.zona.pack(fill="x", padx=12, pady=(12, 0))
        cuerpo = ttk.Frame(self); cuerpo.pack(fill="both", expand=True)
        izq = ttk.Frame(cuerpo, padding=12); izq.pack(side="left", fill="both", expand=True)
        self.construir_previa(cuerpo)

        nb = ttk.Notebook(izq); nb.pack(fill="x")
        self.entradas = {}
        self.genero = tk.StringVar(value=c.get("genero", list(GENEROS)[0]))
        self.tema, self.notas = tk.StringVar(), tk.StringVar()
        self.prompt_fmt = tk.StringVar(value="16:9")
        self.destino_ia = tk.StringVar(value=c.get("destino_ia", DESTINOS_IA[0]))
        self._tema_auto = ""
        self.tab_archivos(nb); self.tab_fondo_ia(nb); self.tab_estilo(nb); self.tab_extras(nb); self.tab_exportar(nb)

        acc = ttk.Frame(izq); acc.pack(fill="x", pady=(10, 4))
        self.btn = ttk.Button(acc, text="Crear video", command=self.iniciar); self.btn.pack(side="left")
        self.btn_t = ttk.Button(acc, text="Editar tiempos…", command=self.abrir_editor); self.btn_t.pack(side="left", padx=8)
        self.bar = ttk.Progressbar(izq, maximum=100); self.bar.pack(fill="x")
        self.txt = tk.Text(izq, height=8, state="disabled", wrap="word"); self.txt.pack(fill="both", expand=True, pady=8)

        for var in (*[self.v[k] for k in ("fondo_16:9", "fondo_9:16", "fondo_1:1", "letra", "logo", "logo_oscuro")],
                    self.prev_fmt, self.fuente, self.pos, self.color, self.tamano, self.efecto, self.pop_on, self.brillo_on, self.tono, self.tono_intensidad, self.genero, self.titulo_on, self.titulo,
                    self.subtitulo, self.zoom_on, self.logo_on, self.logo_esquina, self.logo_tamano, self.logo_opacidad):
            var.trace_add("write", lambda *a: self.programar_previa())
        self.activar_dnd()
        self.protocol("WM_DELETE_WINDOW", self.cerrar)

    # --- construcción de pestañas ---
    def fila_archivo(self, f, r, key, etiqueta, tipo):
        ttk.Label(f, text=etiqueta).grid(row=r, column=0, sticky="w", pady=3)
        e = ttk.Entry(f, textvariable=self.v[key]); e.grid(row=r, column=1, sticky="ew", padx=6)
        self.entradas[key] = e
        ttk.Button(f, text="Elegir…", command=lambda: self.elegir(key, self.TIPOS[tipo])).grid(row=r, column=2)
        ttk.Button(f, text="✕", width=3, command=lambda: self.v[key].set("")).grid(row=r, column=3, padx=(4, 0))

    def tab_archivos(self, nb):
        f = ttk.Frame(nb, padding=10); f.columnconfigure(1, weight=1); nb.add(f, text="Archivos")
        for r, (k, (lbl, tipo)) in enumerate(self.CAMPOS.items()):
            self.fila_archivo(f, r, k, lbl, tipo)
        ttk.Label(f, text="Si un formato no tiene fondo propio, usa el de otro formato (recortado).",
                  foreground="#7a6046").grid(row=len(self.CAMPOS), column=1, sticky="w", pady=(4, 0))

    def tab_fondo_ia(self, nb):
        f = ttk.Frame(nb, padding=10); f.columnconfigure(1, weight=1); nb.add(f, text="Fondo con IA")
        ttk.Label(f, text="Género").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self.genero, values=list(GENEROS), state="readonly").grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="Tema de la letra").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.tema).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Detectar", command=lambda: self.detectar_tema(forzar=True)).grid(row=1, column=2)
        ttk.Label(f, text="Notas extra").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.notas).grid(row=2, column=1, sticky="ew", padx=6)
        fr = ttk.Frame(f); fr.grid(row=3, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(fr, text="Para:").pack(side="left")
        for k in DESTINOS_IA:
            ttk.Radiobutton(fr, text=k, value=k, variable=self.destino_ia).pack(side="left", padx=6)
        ttk.Label(fr, text="     Formato:").pack(side="left")
        for k in FORMATOS:
            ttk.Radiobutton(fr, text=k, value=k, variable=self.prompt_fmt).pack(side="left", padx=6)
        self.prompt_txt = tk.Text(f, height=9, wrap="word", font=("Segoe UI", 9))
        self.prompt_txt.grid(row=4, column=0, columnspan=3, sticky="ew", pady=6)
        fb = ttk.Frame(f); fb.grid(row=5, column=0, columnspan=3, sticky="ew")
        ttk.Button(fb, text="Copiar prompt", command=self.copiar_prompt).pack(side="left")
        self.prompt_ok = ttk.Label(fb, text="", foreground="#2e7d32"); self.prompt_ok.pack(side="left", padx=8)
        self.prompt_ayuda = ttk.Label(fb, text="", foreground="#7a6046"); self.prompt_ayuda.pack(side="right")
        for var in (self.genero, self.tema, self.notas, self.prompt_fmt, self.titulo, self.destino_ia):
            var.trace_add("write", lambda *a: self.actualizar_prompt())
        self.actualizar_prompt()

    def detectar_tema(self, forzar=False):
        letra = self.v["letra"].get().strip()
        if not letra:
            if forzar:
                messagebox.showinfo("Falta la letra", "Elige o arrastra la letra para detectar su tema.")
            return
        t = detectar_tema(letra)
        if forzar or self.tema.get() in ("", self._tema_auto):
            self.tema.set(t); self._tema_auto = t

    def actualizar_prompt(self):
        p = armar_prompt(self.titulo.get().strip(), self.genero.get(), self.tema.get().strip(),
                         self.prompt_fmt.get(), self.notas.get(), self.destino_ia.get())
        self.prompt_txt.delete("1.0", "end"); self.prompt_txt.insert("1.0", p)
        self.prompt_ok.configure(text="")
        if self.destino_ia.get() == "Canva":
            ayuda = f"{len(p)} caracteres · En Canva elige la proporción {self.prompt_fmt.get()} en el generador."
        else:
            ayuda = "Pégalo en ChatGPT, descarga la imagen y arrástrala aquí."
        self.prompt_ayuda.configure(text=ayuda)

    def copiar_prompt(self):
        self.clipboard_clear()
        self.clipboard_append(self.prompt_txt.get("1.0", "end").strip())
        self.prompt_ok.configure(text="✔ Copiado")

    def tab_estilo(self, nb):
        f = ttk.Frame(nb, padding=10); f.columnconfigure(1, weight=1); nb.add(f, text="Estilo de letra")
        ttk.Label(f, text="Fuente").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.fuente, values=FUENTES).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="Posición").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.pos, values=POSICIONES, state="readonly").grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="Tamaño").grid(row=2, column=0, sticky="w", pady=4)
        self.deslizador(f, 2, self.tamano, 50, 180, "%")
        ttk.Label(f, text="Color").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.color, values=COLORES, state="readonly").grid(row=3, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="Efecto").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.efecto, values=EFECTOS, state="readonly").grid(row=4, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="Karaoke: las palabras se iluminan al cantarse. Dos líneas: muestra también la que viene.",
                  foreground="#7a6046").grid(row=5, column=1, sticky="w", padx=6)
        ttk.Checkbutton(f, text="Entrada con «pop» (la línea crece suavemente al aparecer)",
                        variable=self.pop_on).grid(row=6, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Checkbutton(f, text="Brillo suave en el coro", variable=self.brillo_on).grid(row=7, column=1, sticky="w", padx=6)
        ttk.Label(f, text="Ajuste del karaoke").grid(row=8, column=0, sticky="w", pady=(8, 0))
        fila = ttk.Frame(f); fila.grid(row=8, column=1, sticky="ew", padx=6, pady=(8, 0))
        lbl = ttk.Label(fila, width=24)

        def mostrar(v=None):
            x = round(float(self.karaoke_ajuste.get()) / 0.05) * 0.05
            self.karaoke_ajuste.set(round(x, 2))
            lbl.configure(text="sin ajuste" if abs(x) < 0.01 else
                          f"{x:+.2f} s ({'más tarde' if x > 0 else 'más temprano'})")
        ttk.Scale(fila, from_=-0.6, to=0.6, variable=self.karaoke_ajuste, command=mostrar).pack(side="left", fill="x", expand=True)
        lbl.pack(side="left", padx=6)
        mostrar()
        ttk.Label(f, text="Si el iluminado va antes que la voz, muévelo a la derecha; si va después, a la izquierda.",
                  foreground="#7a6046").grid(row=9, column=1, sticky="w", padx=6)
        ttk.Separator(f).grid(row=10, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(f, text="Tono del fondo").grid(row=11, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self.tono, values=TONO_OPCIONES, state="readonly").grid(row=11, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="Intensidad del tono").grid(row=12, column=0, sticky="w", pady=3)
        self.deslizador(f, 12, self.tono_intensidad, 0, 100, "%")
        self.tono_info = ttk.Label(f, text="", foreground="#7a6046")
        self.tono_info.grid(row=13, column=1, sticky="w", padx=6)

        def info_tono(*a):
            t = self.tono.get()
            if t == "Automático según género":
                self.tono_info.configure(text=f"Género «{self.genero.get().split(' (')[0]}» → "
                                              f"{TONO_GENERO.get(self.genero.get(), 'Original')} "
                                              "(el género se elige en la pestaña Fondo con IA)")
            else:
                self.tono_info.configure(text="")
        for var in (self.tono, self.genero):
            var.trace_add("write", info_tono)
        info_tono()

    def tab_extras(self, nb):
        f = ttk.Frame(nb, padding=10); f.columnconfigure(1, weight=1); nb.add(f, text="Extras")
        ttk.Checkbutton(f, text="Mostrar título al inicio", variable=self.titulo_on).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="   Antetítulo").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.subtitulo).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="   Título").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.titulo).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Separator(f).grid(row=3, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Checkbutton(f, text="Movimiento lento del fondo (solo imágenes)", variable=self.zoom_on).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Separator(f).grid(row=5, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Checkbutton(f, text="Logo / sello de agua", variable=self.logo_on).grid(row=6, column=0, columnspan=2, sticky="w")
        self.fila_archivo(f, 7, "logo", "   Logo claro", "png")
        self.fila_archivo(f, 11, "logo_oscuro", "   Logo oscuro", "png")
        ttk.Label(f, text="Con los dos, la app usa el oscuro sobre fondos claros y el claro sobre oscuros. "
                         "Fondo negro o blanco liso: se quita solo.",
                  foreground="#7a6046").grid(row=12, column=1, sticky="w", padx=6)
        ttk.Label(f, text="   Esquina").grid(row=8, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self.logo_esquina, values=ESQUINAS, state="readonly").grid(row=8, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="   Tamaño").grid(row=9, column=0, sticky="w", pady=3)
        self.deslizador(f, 9, self.logo_tamano, 5, 35, "%")
        ttk.Label(f, text="   Opacidad").grid(row=10, column=0, sticky="w", pady=3)
        self.deslizador(f, 10, self.logo_opacidad, 20, 100, "%")

    def tab_exportar(self, nb):
        f = ttk.Frame(nb, padding=10); f.columnconfigure(1, weight=1); nb.add(f, text="Exportar")
        ttk.Label(f, text="Formatos").grid(row=0, column=0, sticky="nw", pady=3)
        fc = ttk.Frame(f); fc.grid(row=0, column=1, sticky="w", padx=6)
        for k, (w, h, desc, _) in FORMATOS.items():
            ttk.Checkbutton(fc, text=f"{k}  {desc}  ({w}x{h})", variable=self.fmt_on[k]).pack(anchor="w")
        ttk.Label(f, text="Guardar como").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(f, textvariable=self.v["salida"]).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Elegir…", command=self.elegir_salida).grid(row=1, column=2)
        ttk.Label(f, text="Se agrega el formato al nombre, p. ej. «… (9x16).mp4».",
                  foreground="#7a6046").grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(f, text="Precisión Whisper").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Combobox(f, textvariable=self.modelo, values=["small", "medium", "large-v3"], state="readonly",
                     width=12).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Checkbutton(f, text="Volver a sincronizar aunque ya exista sincronización guardada",
                        variable=self.resinc).grid(row=4, column=1, sticky="w", padx=6)
        ttk.Checkbutton(f, text="Separar la voz antes de sincronizar (karaoke más preciso, ~3 min la primera vez)",
                        variable=self.separar_voz,
                        state="normal" if demucs_disponible() else "disabled").grid(row=9, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Separator(f).grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(f, text="Qué exportar").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self.exportar, values=EXPORTAR, state="readonly").grid(row=6, column=1, sticky="ew", padx=6)
        ttk.Label(f, text="Fragmento").grid(row=7, column=0, sticky="w", pady=3)
        fr = ttk.Frame(f); fr.grid(row=7, column=1, sticky="ew", padx=6)
        self.cb_modo = ttk.Combobox(fr, textvariable=self.frag_modo, values=FRAGMENTOS, state="readonly", width=24)
        self.cb_modo.pack(side="left")
        ttk.Label(fr, text="  inicio (m:ss)").pack(side="left")
        self.ent_ini = ttk.Entry(fr, textvariable=self.frag_inicio, width=7); self.ent_ini.pack(side="left", padx=4)
        ttk.Label(fr, text="  duración").pack(side="left")
        self.cb_dur = ttk.Combobox(fr, textvariable=self.frag_dur, values=DURACIONES, state="readonly", width=14)
        self.cb_dur.pack(side="left", padx=4)
        ttk.Label(f, text="El corto sale con fundido de entrada y salida, listo para TikTok, Reels o Shorts.",
                  foreground="#7a6046").grid(row=8, column=1, sticky="w", padx=6)
        for var in (self.exportar, self.frag_modo):
            var.trace_add("write", lambda *a: self.estado_fragmento())
        self.estado_fragmento()

    def estado_fragmento(self):
        corto = not self.exportar.get().startswith("Canción")
        self.cb_modo.configure(state="readonly" if corto else "disabled")
        self.cb_dur.configure(state="readonly" if corto else "disabled")
        self.ent_ini.configure(state="normal" if corto and self.frag_modo.get().startswith("Desde") else "disabled")

    def deslizador(self, f, r, var, a, b, unidad):
        fila = ttk.Frame(f); fila.grid(row=r, column=1, sticky="ew", padx=6)
        lbl = ttk.Label(fila, text=f"{var.get()} {unidad}", width=6)
        ttk.Scale(fila, from_=a, to=b, variable=var,
                  command=lambda v: (var.set(int(float(v))), lbl.configure(text=f"{int(float(v))} {unidad}"))
                  ).pack(side="left", fill="x", expand=True)
        lbl.pack(side="left")

    def construir_previa(self, cuerpo):
        pv = ttk.LabelFrame(cuerpo, text="Vista previa", padding=8)
        pv.pack(side="right", fill="y", padx=(0, 12), pady=12)
        fr = ttk.Frame(pv); fr.pack()
        for k in FORMATOS:
            ttk.Radiobutton(fr, text=k, value=k, variable=self.prev_fmt).pack(side="left", padx=6)
        self.prev_img = ttk.Label(pv, text="Elige un fondo para ver\ncómo queda la letra.", anchor="center", width=60)
        self.prev_img.pack(expand=True, pady=6)
        self.prev_info = ttk.Label(pv, text="", foreground="#7a6046"); self.prev_info.pack()
        ttk.Button(pv, text="Actualizar vista previa", command=self.actualizar_previa).pack(pady=6)

    # --- configuración persistente ---
    def cargar_config(self):
        try:
            return json.load(open(CONFIG, encoding="utf8"))
        except (OSError, ValueError):
            return {}

    def ajustes(self):
        return dict(fuente=self.fuente.get(), pos=self.pos.get(), tamano=int(self.tamano.get()), efecto=self.efecto.get(),
                    pop_on=self.pop_on.get(), brillo_on=self.brillo_on.get(), karaoke_ajuste=round(self.karaoke_ajuste.get(), 2),
                    tono=self.tono.get(), tono_intensidad=int(self.tono_intensidad.get()), separar_voz=self.separar_voz.get(),
                    color=self.color.get(), modelo=self.modelo.get(), prev_fmt=self.prev_fmt.get(),
                    formatos=[k for k, v in self.fmt_on.items() if v.get()],
                    titulo_on=self.titulo_on.get(), titulo=self.titulo.get(), subtitulo=self.subtitulo.get(),
                    zoom_on=self.zoom_on.get(), logo_on=self.logo_on.get(), logo=self.v["logo"].get(), logo_oscuro=self.v["logo_oscuro"].get(),
                    logo_esquina=self.logo_esquina.get(), logo_tamano=int(self.logo_tamano.get()),
                    logo_opacidad=int(self.logo_opacidad.get()), ultima_carpeta=self.ultima, genero=self.genero.get(), destino_ia=self.destino_ia.get(),
                    exportar=self.exportar.get(), frag_modo=self.frag_modo.get(),
                    frag_dur=self.frag_dur.get(), frag_inicio=self.frag_inicio.get())

    def guardar_config(self):
        cfg = self.ajustes(); cfg.pop("titulo")  # el título cambia con cada canción
        try:
            json.dump(cfg, open(CONFIG, "w", encoding="utf8"), ensure_ascii=False, indent=1)
        except OSError:
            pass

    def cerrar(self):
        self.guardar_config(); self.destroy()

    # --- archivos ---
    def fondo_para(self, fmt):
        """Fondo propio del formato o, si falta, el de otro formato."""
        orden = [fmt] + [k for k in ("16:9", "1:1", "9:16") if k != fmt]
        for k in orden:
            p = self.v[f"fondo_{k}"].get().strip()
            if p:
                return p
        return ""

    def limpiar_logo(self, key):
        """Ruta del logo `key` listo para usar (sin fondo), o None. Avisa si no se pudo limpiar."""
        p = self.v[key].get().strip()
        if not (self.logo_on.get() and p and os.path.exists(p)):
            return None
        try:
            limpio, aviso = logo_limpio(p)
        except Exception as e:
            limpio, aviso = p, f"No se pudo procesar el logo: {e}"
        avisos = getattr(self, "_avisos_logo", {})
        if aviso and aviso != avisos.get(key):
            self.log("⚠ " + aviso)
        avisos[key] = aviso; self._avisos_logo = avisos
        return limpio

    def logos_activos(self):
        """(logo claro, logo oscuro) ya limpios; cualquiera puede ser None."""
        return self.limpiar_logo("logo"), self.limpiar_logo("logo_oscuro")

    def asignar(self, key, p):
        if key == "fondo":
            key = "fondo_" + formato_por_aspecto(p)
        self.v[key].set(p)
        self.ultima = os.path.dirname(p)
        if key == "audio" and not self.v["salida"].get():
            self.v["salida"].set(os.path.splitext(p)[0] + " - lyrics.mp4")
        if key == "letra":
            t = leer_titulo(p)
            if t and self.titulo.get() in ("", self._titulo_auto):
                self.titulo.set(t); self._titulo_auto = t
            self.detectar_tema()
        if key.startswith("fondo_"):
            self.prev_fmt.set(key[6:])
        return key

    def activar_dnd(self):
        if not DND_FILES:
            self.zona.configure(text="(Arrastrar y soltar no disponible: usa los botones Elegir…)")
            return
        # Soltar en un campo lo asigna a ese campo; en cualquier otro lugar se clasifica solo.
        for key, e in self.entradas.items():
            e.drop_target_register(DND_FILES)
            e.dnd_bind("<<Drop>>", lambda ev, k=key: self.soltar(ev, k))
        for w in (self, self.zona):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", lambda ev: self.soltar(ev, None))
        self.zona.dnd_bind("<<DropEnter>>", lambda ev: self.zona.configure(bg="#e6d6bd") or ev.action)
        self.zona.dnd_bind("<<DropLeave>>", lambda ev: self.zona.configure(bg="#f3ede4") or ev.action)

    def soltar(self, ev, destino):
        self.zona.configure(bg="#f3ede4")
        rutas = self.tk.splitlist(ev.data)
        ignorados = []
        for p in rutas:
            key = destino if destino and len(rutas) == 1 else tipo_de(p)
            if key:
                final = self.asignar(key, p)
                self.log(f"→ {self.CAMPOS.get(final, ('Logo',))[0]}: {os.path.basename(p)}")
            else:
                ignorados.append(os.path.basename(p))
        if ignorados:
            self.log("No reconozco: " + ", ".join(ignorados))
        return ev.action

    def elegir(self, key, types):
        p = filedialog.askopenfilename(initialdir=self.ultima, filetypes=types + [("Todos", "*.*")])
        if p:
            self.asignar(key, p)

    def elegir_salida(self):
        p = filedialog.asksaveasfilename(initialdir=self.ultima, defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
        if p:
            self.v["salida"].set(p)

    # --- vista previa ---
    def texto_muestra(self):
        try:
            lineas = leer_letra(self.v["letra"].get())
            textos = [l["text"] for l in lineas]
            coro = [i for i, l in enumerate(lineas) if estilo_de(l["section"]) == "Coro"]
            i = max(coro or range(len(textos)), key=lambda k: len(textos[k]))
            return textos[i:i + 2] if i + 1 < len(textos) else [textos[i]]
        except Exception:
            return ["Así se verá la letra de tu canción", "y aquí la línea que viene"]

    def programar_previa(self):
        if self._prev_job:
            self.after_cancel(self._prev_job)
        self._prev_job = self.after(600, self.actualizar_previa)

    def actualizar_previa(self):
        fmt = self.prev_fmt.get()
        fondo = self.fondo_para(fmt)
        if not fondo or not os.path.exists(fondo):
            return
        cfg, muestra, logos = self.ajustes(), self.texto_muestra(), self.logos_activos()
        self.prev_info.configure(text="Generando…")
        threading.Thread(target=self._previa, args=(fondo, logos, fmt, cfg, muestra), daemon=True).start()

    def _previa(self, fondo, logos, fmt, cfg, muestra):
        try:
            w, h = FORMATOS[fmt][:2]
            td = tempfile.mkdtemp()
            nombre_fondo = os.path.basename(fondo)
            fondo = aplicar_tono(fondo, w, h, cfg, td)
            logo, version = elegir_logo(fondo, w, h, cfg, *logos)
            paleta = elegir_paleta(cfg["color"], fondo, w, h, cfg["pos"])
            ass, png = os.path.join(td, "subs.ass"), os.path.join(td, "prev.png")
            pt = elegir_paleta(cfg["color"], fondo, w, h, "Centro") if cfg["titulo_on"] else None
            crear_ass([], w, h, cfg, paleta, ass, muestra=muestra, paleta_titulo=pt)
            vista_previa(fondo, logo, ass, w, h, paleta, cfg, png)

            def mostrar():
                self._foto = tk.PhotoImage(file=png)
                self.prev_img.configure(image=self._foto, text="")
                self.prev_info.configure(text=f"{fmt} · letra {paleta} · {cfg['pos']} · {cfg['tamano']} %"
                                              + (f" · logo {version}" if version else "")
                                              + f"\nFondo: {nombre_fondo}"
                                              + (f" · tono {tono_efectivo(cfg)}" if tono_efectivo(cfg) else ""))
            self.after(0, mostrar)
        except Exception as e:
            self.after(0, lambda: self.prev_info.configure(text=f"No se pudo generar: {str(e)[:120]}"))

    # --- trabajo ---
    def log(self, msg):
        def _():
            self.txt.configure(state="normal"); self.txt.insert("end", msg + "\n")
            self.txt.see("end"); self.txt.configure(state="disabled")
        self.after(0, _)

    def progreso(self, frac):
        self.after(0, lambda: self.bar.configure(value=frac * 100))

    def ocupado(self, si):
        estado = "disabled" if si else "normal"
        self.after(0, lambda: (self.btn.configure(state=estado), self.btn_t.configure(state=estado)))

    def abrir_editor(self):
        audio, letra = self.v["audio"].get().strip(), self.v["letra"].get().strip()
        if not audio or not letra:
            messagebox.showwarning("Falta algo", "Elige el audio y la letra primero."); return
        self.ocupado(True)

        def tarea():
            try:
                data = obtener_sincronizacion(audio, letra, self.modelo.get(), self.resinc.get(), self.log,
                                              self.separar_voz.get())
                self.after(0, lambda: EditorTiempos(self, audio, data))
            except Exception as e:
                self.log(f"✖ Error: {e}")
            finally:
                self.ocupado(False)
        threading.Thread(target=tarea, daemon=True).start()

    def iniciar(self):
        formatos = [k for k, v in self.fmt_on.items() if v.get()]
        faltan = [n for k, n in (("audio", "el audio"), ("letra", "la letra"), ("salida", "dónde guardar"))
                  if not self.v[k].get().strip()]
        solo_audio = self.exportar.get() == EXPORTAR[3]
        if not formatos and not solo_audio:
            faltan.insert(0, "al menos un formato (pestaña Exportar)")
        if not solo_audio and not self.fondo_para("16:9"):
            faltan.insert(0, "un fondo")
        if faltan:
            messagebox.showwarning("Falta algo", "Elige " + ", ".join(faltan) + "."); return
        self.guardar_config()
        self.bar.configure(value=0)
        self.ocupado(True)
        threading.Thread(target=self.trabajo, args=(formatos, self.ajustes()), daemon=True).start()

    def trabajo(self, formatos, cfg):
        hechos = []
        try:
            audio, letra = self.v["audio"].get().strip(), self.v["letra"].get().strip()
            data = obtener_sincronizacion(audio, letra, cfg["modelo"], self.resinc.get(), self.log, cfg["separar_voz"])
            self.after(0, lambda: self.resinc.set(False))
            base = os.path.splitext(self.v["salida"].get().strip())[0]
            if cfg["exportar"] == EXPORTAR[3]:
                ini, d = calcular_fragmento(data, cfg, audio)
                salida = f"{base} corto.mp3"
                self.log(f"Audio: desde {int(ini // 60)}:{ini % 60:05.2f}, {d:.0f} s…")
                exportar_audio(audio, ini, d, salida)
                self.progreso(1.0)
                self.log(f"   ✔ {salida}")
                self.after(0, lambda: messagebox.showinfo("Listo", "Audio creado: " + salida))
                return
            logos = self.logos_activos()
            trabajos = []  # (formato, inicio o None, duración)
            if cfg["exportar"] != EXPORTAR[1]:
                trabajos += [(fmt, None, data["duracion"]) for fmt in formatos]
            if cfg["exportar"] != EXPORTAR[0]:
                ini, d = calcular_fragmento(data, cfg, audio)
                self.log(f"Fragmento: desde {int(ini // 60)}:{ini % 60:05.2f}, {d:.0f} s.")
                trabajos += [(fmt, ini, d) for fmt in formatos]
            tono = tono_efectivo(cfg)
            if tono:
                self.log(f"Tono del fondo: {tono} ({cfg['tono_intensidad']} %).")
            tono_dir = tempfile.mkdtemp()
            for n, (fmt, ini, d) in enumerate(trabajos):
                w, h, _, suf = FORMATOS[fmt]
                fondo = aplicar_tono(self.fondo_para(fmt), w, h, cfg, tono_dir)
                paleta = elegir_paleta(cfg["color"], fondo, w, h, cfg["pos"])
                salida = f"{base} ({suf}).mp4" if ini is None else f"{base} ({suf}) corto.mp4"
                lineas = data["lineas"] if ini is None else recortar_lineas(data["lineas"], ini, d)
                tipo = "completo" if ini is None else "corto"
                logo, version = elegir_logo(fondo, w, h, cfg, *logos)
                self.log(f"[{n + 1}/{len(trabajos)}] {fmt} {tipo} · fondo {os.path.basename(fondo)} · letra {paleta}"
                         + (f" · logo {version}" if version else "") + "…")
                with tempfile.TemporaryDirectory() as td:
                    ass = os.path.join(td, "subs.ass")
                    pt = elegir_paleta(cfg["color"], fondo, w, h, "Centro") if cfg["titulo_on"] else None
                    crear_ass(lineas, w, h, cfg, paleta, ass, paleta_titulo=pt)
                    render(fondo, audio, logo, ass, w, h, d, salida, paleta, cfg, self.log,
                           lambda f, n=n: self.progreso((n + f) / len(trabajos)), inicio=ini)
                hechos.append(salida)
                self.log(f"   ✔ {salida}")
            self.after(0, lambda: messagebox.showinfo("Listo", "Videos creados:\n" + "\n".join(hechos)))
        except Exception as e:
            self.log(f"✖ Error: {e}")
            self.after(0, lambda: messagebox.showerror("Error", str(e)[:800]))
        finally:
            self.ocupado(False)


if __name__ == "__main__":
    App().mainloop()
