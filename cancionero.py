"""Cancionero: junta las letras de una carpeta de álbumes (.md / .txt, con subcarpetas) en un solo libro PDF.

Cada álbum es un archivo con canciones bajo encabezados Markdown y la letra con etiquetas
de sección estilo Suno ([Verse], [Chorus]…). Se ignoran las notas de producción (líneas en
negrita, prompts entre comillas invertidas, tablas) y las canciones repetidas entre carpetas.

Uso:
    python cancionero.py "C:\\carpeta\\de\\letras" --titulo "Cancionero" --autor "Resucitado 2025"
Genera <carpeta>\\Cancionero.html y Cancionero.pdf (PDF con Microsoft Edge, que trae las
fuentes para hindi, coreano, árabe, etc.).
"""
import argparse, datetime, html, os, re, shutil, subprocess, tempfile, unicodedata

# Si una canción aparece en varias carpetas se usa la versión de la más "terminada"
PRIORIDAD = ["publicados", "procesados suno", "", "detrabajo", "base discos", "archivados"]
EXCLUIR = {"audacity_macros", "suno_bot", "portadas"}
ETIQUETA = re.compile(r"^\[([^\]]+)\]\s*$")
NOTA = re.compile(r"^(\*\*|`|>|\||-{3,}|\*[A-ZÁÉÍÓÚ ]+:|[A-ZÁÉÍÓÚ ]{3,}:\s)")
SECCION_VISIBLE = {"chorus": "Coro", "coro": "Coro", "pre-chorus": "Pre-coro", "bridge": "Puente",
                   "puente": "Puente", "outro": "Final", "intro": None}


def norm(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return re.sub(r"[^a-z0-9\u0900-\u097f\uac00-\ud7af\u0600-\u06ff\u4e00-\u9fff]+", " ",
                  "".join(c for c in t if not unicodedata.combining(c))).strip()


def limpiar_titulo(t):
    t = re.sub(r"^#+\s*", "", t).strip()
    t = re.sub(r"^(\d+|[IVX]+)\s*[.)\-–—:]\s*", "", t)
    t = re.sub(r"^(canci[oó]n|track|tema)\s*\d+\s*[:.\-–—]\s*", "", t, flags=re.I)
    t = t.split(" | ")[0].strip("*_ ").strip()
    if t.isupper():
        t = t.title()
    return t


def prioridad(ruta, raiz):
    rel = os.path.relpath(os.path.dirname(ruta), raiz).replace("\\", "/").lower()
    for i, p in enumerate(PRIORIDAD):
        if p and p in rel:
            return i
    return PRIORIDAD.index("") if rel == "." else len(PRIORIDAD)


def canciones_de(texto):
    """[(título, [estrofas])] del archivo. Cada estrofa: (sección o None, [líneas])."""
    bloques, titulo, actual = [], None, []
    for linea in texto.splitlines() + ["# FIN"]:
        if re.match(r"^#{1,4}\s", linea):
            if titulo is not None and any(ETIQUETA.match(l.strip()) for l in actual):
                bloques.append((titulo, actual))
            titulo, actual = linea, []
        else:
            actual.append(linea)
    out = []
    for titulo, lineas in bloques:
        estrofas, sec, buf, empezo = [], None, [], False
        for l in lineas:
            s = l.strip()
            m = ETIQUETA.match(s)
            if m:
                empezo = True
                if buf:
                    estrofas.append((sec, buf)); buf = []
                nombre = m.group(1).split(":")[0].strip().lower()
                if nombre in ("end", "fin", "instrumental", "instrumental break", "break"):
                    sec = None; continue
                clave = next((k for k in SECCION_VISIBLE if nombre.startswith(k)), None)
                sec = SECCION_VISIBLE.get(clave) if clave else None
                continue
            if not empezo:
                continue
            if not s:
                if buf:
                    estrofas.append((sec, buf)); buf = []
                continue
            if NOTA.match(s) or s.startswith("(") and s.endswith(")") and len(s) < 40:
                continue
            buf.append(s.strip("*_"))
        if buf:
            estrofas.append((sec, buf))
        estrofas = [e for e in estrofas if e[1]]
        if sum(len(e[1]) for e in estrofas) >= 4:
            out.append((limpiar_titulo(titulo), estrofas))
    return out


def titulo_album(texto, archivo):
    for l in texto.splitlines():
        if l.startswith("# "):
            t = re.sub(r"\s*[—–-]\s*VOLUMEN\s*", " · Vol. ", l[2:].strip(), flags=re.I).split(" | ")[0].strip("*_ ")
            return t if any(c.islower() for c in t) else " ".join(w if len(w) <= 3 and w.isupper() and "-" in w else w.capitalize() for w in t.split())
    return re.sub(r"[_]+", " ", os.path.splitext(os.path.basename(archivo))[0]).strip().capitalize()


def recolectar(raiz):
    archivos = []
    for d, sub, fs in os.walk(raiz):
        sub[:] = [s for s in sub if s.lower() not in EXCLUIR]
        archivos += [os.path.join(d, f) for f in fs if f.lower().endswith((".md", ".txt"))]
    archivos.sort(key=lambda p: (prioridad(p, raiz), p.lower()))
    vistos, albumes = set(), {}
    for p in archivos:
        try:
            texto = open(p, encoding="utf-8-sig").read()
        except UnicodeDecodeError:
            texto = open(p, encoding="latin-1").read()
        for titulo, estrofas in canciones_de(texto):
            primeras = [l for _, ls in estrofas for l in ls][:3]
            clave = norm(" ".join(primeras))
            if not clave or clave in vistos:
                continue
            vistos.add(clave)
            album = titulo_album(texto, p)
            albumes.setdefault(album, []).append((titulo, estrofas))
    return albumes


CSS = """
@page { size: A5; margin: 18mm 16mm 20mm; @bottom-center { content: counter(page); } }
body { font-family: 'Cormorant Garamond', Georgia, 'Nirmala UI', 'Malgun Gothic', 'Segoe UI', serif;
       font-size: 11pt; line-height: 1.35; color: #111; }
.portada { height: 170mm; display: flex; flex-direction: column; justify-content: center; text-align: center;
           page-break-after: always; }
.portada h1 { font-size: 30pt; margin: 0 0 6mm; font-weight: 500; }
.portada .autor { font-size: 15pt; } .portada .pie { margin-top: 30mm; font-size: 10pt; color: #444; }
.legal { page-break-after: always; font-size: 9pt; padding-top: 120mm; }
.indice { page-break-after: always; font-size: 9.5pt; }
.indice h2 { font-size: 16pt; font-weight: 500; }
.indice .alb { margin: 4mm 0 1mm; font-weight: 600; break-after: avoid; }
.indice ol { margin: 0; padding-left: 7mm; } .indice li { margin: 0; }
h2.album { page-break-before: always; text-align: center; font-weight: 500; font-size: 17pt; margin-top: 50mm; }
.cancion { page-break-before: always; }
.cancion h3 { font-size: 15pt; font-weight: 600; margin: 0 0 1mm; }
.cancion .num { color: #666; font-size: 9pt; margin-bottom: 5mm; }
.estrofa { margin: 0 0 3.2mm; break-inside: avoid; } .estrofa .sec { font-style: italic; font-size: 9pt; color: #555; }
.coro { padding-left: 5mm; font-style: italic; }
"""


def generar_html(albumes, titulo, autor, nota):
    e = html.escape
    total = sum(len(c) for c in albumes.values())
    fecha = datetime.date.today().strftime("%d-%m-%Y")
    partes = [f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>{e(titulo)}</title><style>{CSS}</style></head><body>",
              f"<section class='portada'><h1>{e(titulo)}</h1><div class='autor'>{e(autor)}</div>"
              f"<div class='pie'>{total} canciones · {len(albumes)} álbumes<br>{fecha}</div></section>",
              f"<section class='legal'>© {datetime.date.today().year} {e(autor)}. {e(nota)}</section>",
              "<section class='indice'><h2>Índice</h2>"]
    n = 0
    for album, canciones in albumes.items():
        partes.append(f"<div class='alb'>{e(album)}</div><ol start='{n + 1}'>")
        partes += [f"<li>{e(t)}</li>" for t, _ in canciones]
        partes.append("</ol>"); n += len(canciones)
    partes.append("</section>")
    n = 0
    for album, canciones in albumes.items():
        partes.append(f"<h2 class='album'>{e(album)}</h2>")
        for t, estrofas in canciones:
            n += 1
            partes.append(f"<article class='cancion'><h3>{e(t)}</h3><div class='num'>{n} · {e(album)}</div>")
            for sec, ls in estrofas:
                cls = "estrofa coro" if sec == "Coro" else "estrofa"
                cab = f"<div class='sec'>{e(sec)}</div>" if sec else ""
                partes.append(f"<div class='{cls}'>{cab}{'<br>'.join(e(l) for l in ls)}</div>")
            partes.append("</article>")
    partes.append("</body></html>")
    return "\n".join(partes), total


def edge():
    for p in (shutil.which("msedge"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", shutil.which("chrome"),
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if p and os.path.exists(p):
            return p
    return None


def a_pdf(html_path, pdf_path):
    nav = edge()
    if not nav:
        raise RuntimeError("No encontré Microsoft Edge ni Chrome para crear el PDF; abre el HTML e imprímelo como PDF.")
    perfil = tempfile.mkdtemp()  # perfil aparte: funciona aunque Edge esté abierto
    pdf_path = os.path.abspath(pdf_path)
    subprocess.run([nav, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={perfil}",
                    f"--print-to-pdf={pdf_path}", "file:///" + os.path.abspath(html_path).replace("\\", "/")],
                   capture_output=True, timeout=900)
    shutil.rmtree(perfil, ignore_errors=True)
    if not os.path.exists(pdf_path):
        raise RuntimeError("Edge no generó el PDF.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("carpeta")
    ap.add_argument("--titulo", default="Cancionero")
    ap.add_argument("--autor", default="Resucitado 2025")
    ap.add_argument("--nota", default="Letras originales. Todos los derechos reservados.")
    ap.add_argument("--salida", help="ruta del PDF (por defecto, dentro de la carpeta)")
    a = ap.parse_args()
    albumes = recolectar(a.carpeta)
    doc, total = generar_html(albumes, a.titulo, a.autor, a.nota)
    pdf = a.salida or os.path.join(a.carpeta, a.titulo + ".pdf")
    html_path = os.path.splitext(pdf)[0] + ".html"
    open(html_path, "w", encoding="utf-8").write(doc)
    print(f"{total} canciones en {len(albumes)} álbumes -> {html_path}")
    a_pdf(html_path, pdf)
    print(f"PDF: {pdf}")


if __name__ == "__main__":
    main()
