"""Cancionero: junta las letras de una carpeta de álbumes (.md / .txt, con subcarpetas) en un solo libro PDF.

Cada álbum es un archivo con canciones bajo encabezados Markdown y la letra con etiquetas
de sección estilo Suno ([Verse], [Chorus]…). Se ignoran las notas de producción (líneas en
negrita, prompts entre comillas invertidas, tablas) y las canciones repetidas entre carpetas.

Uso:
    python cancionero.py "C:\\carpeta\\de\\letras" --titulo "Cancionero" --autor "Resucitado 2025"
Genera <carpeta>\\Cancionero.html y Cancionero.pdf (PDF con Microsoft Edge, que trae las
fuentes para hindi, coreano, árabe, etc.).
"""
import argparse, datetime, html, os, re, shutil, subprocess, tempfile, time, unicodedata

# Si una canción aparece en varias carpetas se usa la versión de la más "terminada"
PRIORIDAD = ["publicados", "procesados suno", "", "detrabajo", "base discos", "archivados"]
EXCLUIR = {"audacity_macros", "suno_bot", "portadas"}
# Textos opcionales del libro: archivos con estos nombres (.md o .txt) en la carpeta principal
EXTRAS = ["dedicatoria", "agradecimientos", "prologo", "epilogo"]
TITULO_EXTRA = {"dedicatoria": "", "agradecimientos": "Agradecimientos", "prologo": "Prólogo", "epilogo": "Epílogo"}
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
    t = re.split(r"\s*(?:[★☆✓✔✅⭐]|\(?demo lista)", t, flags=re.I)[0]  # marcas de trabajo, no del título
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
        archivos += [os.path.join(d, f) for f in fs if f.lower().endswith((".md", ".txt"))
                     and os.path.splitext(f)[0].lower() not in EXTRAS]
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
            if norm(titulo) in ("single", "sencillo", "letra", "lyrics", ""):  # sencillos: el título es el del archivo
                titulo = album
            albumes.setdefault(album, []).append((titulo, estrofas))
    return albumes


FUENTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
ACENTO = "#8a5a2b"

CSS = """
@font-face { font-family: 'Cormorant'; src: url('%(f)s/CormorantGaramond-Medium.ttf'); font-weight: 400 700; }
@font-face { font-family: 'Cormorant'; src: url('%(f)s/CormorantGaramond-MediumItalic.ttf'); font-style: italic; }
@page { size: A5; margin: 17mm 16mm 19mm;
        @bottom-center { content: counter(page); font: 9pt 'Cormorant', Georgia, serif; color: %(a)s; } }
@page blanca { @bottom-center { content: none; } }
body { font-family: 'Cormorant', Georgia, 'Nirmala UI', 'Malgun Gothic', 'Microsoft YaHei', 'Segoe UI', serif;
       font-size: 11.5pt; line-height: 1.38; color: #1d1712; margin: 0; }
.sc { font-variant: small-caps; letter-spacing: .12em; }
.regla { width: 14mm; height: 0; border-top: .6pt solid %(a)s; margin: 4mm auto; }
.portada { page: blanca; height: 99vh; box-sizing: border-box; break-after: page; }
.portada .marco { height: 100%%; box-sizing: border-box; border: .8pt solid %(a)s; outline: .4pt solid %(a)s;
                  outline-offset: -2.2mm; display: flex; flex-direction: column; align-items: center;
                  justify-content: center; text-align: center; padding: 12mm; }
.portada .etq { color: %(a)s; font-size: 10pt; }
.portada h1 { font-size: 34pt; font-weight: 500; margin: 3mm 0 0; line-height: 1.05; }
.portada .orn { color: %(a)s; font-size: 13pt; margin: 7mm 0; letter-spacing: .6em; }
.portada .autor { font-size: 14pt; } .portada .seud { font-style: italic; font-size: 11pt; color: #5a4a3c; }
.portada .pie { margin-top: 22mm; font-size: 9.5pt; color: #5a4a3c; }
.legal { page: blanca; break-after: page; height: 97vh; display: flex; flex-direction: column; justify-content: flex-end;
         font-size: 9pt; line-height: 1.55; color: #3d332a; }
.legal b { font-weight: 600; color: #1d1712; }
.indice { break-after: page; font-size: 9.5pt; line-height: 1.3; }
.indice h2 { text-align: center; font-weight: 500; font-size: 18pt; margin: 4mm 0 0; }
.indice .cols { column-count: 2; column-gap: 7mm; margin-top: 6mm; }
.indice .alb { color: %(a)s; font-size: 9pt; margin: 3.5mm 0 1mm; break-after: avoid; }
.indice ol { margin: 0; padding-left: 6.5mm; } .indice li { margin: 0; break-inside: avoid; }
.indice li::marker { color: #9c8a78; font-size: 8pt; }
.album { break-before: page; page: blanca; height: 90vh; display: flex; flex-direction: column;
         justify-content: center; text-align: center; }
.album .etq { color: %(a)s; font-size: 9.5pt; }
.album h2 { font-weight: 500; font-size: 21pt; margin: 2mm 8mm 0; line-height: 1.15; }
.album .cuenta { font-style: italic; color: #5a4a3c; font-size: 10.5pt; }
.cancion { break-before: page; }
.cancion .etq { color: %(a)s; font-size: 8.5pt; }
.cancion h3 { font-size: 17pt; font-weight: 600; margin: 1mm 0 0; line-height: 1.15; }
.cancion .regla { margin: 3.5mm 0 6mm; }
.estrofa { margin: 0 0 3.6mm; break-inside: avoid; }
.estrofa .sec { font-variant: small-caps; letter-spacing: .1em; font-size: 8.5pt; color: %(a)s; }
.dedicatoria { break-after: page; page: blanca; height: 80vh; display: flex; flex-direction: column;
               justify-content: center; text-align: right; font-style: italic; padding-left: 30mm; }
.texto { break-before: page; break-after: page; }
.texto h2 { text-align: center; font-weight: 500; font-size: 18pt; margin: 6mm 0 0; }
.texto p { text-align: justify; text-indent: 6mm; margin: 0 0 2.5mm; hyphens: auto; }
.texto p:first-of-type { text-indent: 0; }
.coro { border-left: .8pt solid %(a)s; padding-left: 4mm; font-style: italic; }
"""


def leer_extras(carpeta):
    """{nombre: [párrafos]} de dedicatoria, prólogo, etc., si existen en la carpeta."""
    out = {}
    for n in EXTRAS:
        for ext in (".md", ".txt"):
            p = os.path.join(carpeta, n + ext)
            if os.path.exists(p):
                texto = open(p, encoding="utf-8-sig").read()
                out[n] = [re.sub(r"^#+\s*", "", b).strip() for b in re.split(r"\n\s*\n", texto) if b.strip()]
                break
    return out


def html_extra(nombre, parrafos):
    e = html.escape
    if nombre == "dedicatoria":
        return "<section class='dedicatoria'>" + "".join(f"<p>{e(x)}</p>" for x in parrafos) + "</section>"
    return (f"<section class='texto'><h2>{TITULO_EXTRA[nombre]}</h2><div class='regla'></div>"
            + "".join(f"<p>{'<br>'.join(e(l) for l in x.splitlines())}</p>" for x in parrafos) + "</section>")


def generar_html(albumes, titulo, autor, nota, seudonimo="", rut="", extras=None):
    e = html.escape
    total = sum(len(c) for c in albumes.values())
    hoy = datetime.date.today()
    css = CSS % {"f": "file:///" + FUENTES.replace("\\", "/"), "a": ACENTO}
    partes = [f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>{e(titulo)}</title><style>{css}</style></head><body>",
              f"<section class='portada'><div class='marco'><div class='etq sc'>Cancionero</div><h1>{e(titulo)}</h1>"
              f"<div class='orn'>✦ ✦ ✦</div><div class='autor'>{e(autor)}</div>"
              + (f"<div class='seud'>{e(seudonimo)}</div>" if seudonimo else "")
              + f"<div class='pie sc'>{total} canciones · {len(albumes)} álbumes · {hoy.year}</div></div></section>",
              "<section class='legal'>"
              f"<div><b>{e(titulo)}</b></div><div>Letras originales de {e(autor)}"
              + (f", bajo el seudónimo «{e(seudonimo)}»" if seudonimo else "") + ".</div>"
              + (f"<div>RUT {e(rut)}</div>" if rut else "")
              + f"<div>{total} canciones en {len(albumes)} álbumes.</div><div>Edición: {hoy.strftime('%d-%m-%Y')}</div><br>"
              f"<div>© {hoy.year} {e(autor)}. {e(nota)}</div></section>",
              *[html_extra(n, x) for n, x in (extras or {}).items() if n != "epilogo"],
              "<section class='indice'><h2>Índice</h2><div class='regla'></div><div class='cols'>"]
    n = 0
    for album, canciones in albumes.items():
        partes.append(f"<div class='alb sc'>{e(album)}</div><ol start='{n + 1}'>")
        partes += [f"<li>{e(t)}</li>" for t, _ in canciones]
        partes.append("</ol>"); n += len(canciones)
    partes.append("</div></section>")
    n = 0
    for k, (album, canciones) in enumerate(albumes.items(), 1):
        partes.append(f"<section class='album'><div class='etq sc'>Álbum {k}</div><h2>{e(album)}</h2>"
                      f"<div class='regla'></div><div class='cuenta'>{len(canciones)} canciones</div></section>")
        for t, estrofas in canciones:
            n += 1
            partes.append(f"<article class='cancion'><div class='etq sc'>{n} · {e(album)}</div><h3>{e(t)}</h3><div class='regla'></div>")
            for sec, ls in estrofas:
                cls = "estrofa coro" if sec == "Coro" else "estrofa"
                cab = f"<div class='sec'>{e(sec)}</div>" if sec else ""
                partes.append(f"<div class='{cls}'>{cab}{'<br>'.join(e(l) for l in ls)}</div>")
            partes.append("</article>")
    if extras and "epilogo" in extras:
        partes.append(html_extra("epilogo", extras["epilogo"]))
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
    # Edge a veces vuelve antes de terminar: se espera a que el PDF exista y deje de crecer
    tam, fin = -1, time.time() + 900
    while time.time() < fin:
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) == tam > 0:
            break
        tam = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else -1
        time.sleep(2)
    shutil.rmtree(perfil, ignore_errors=True)
    if not os.path.exists(pdf_path):
        raise RuntimeError("Edge no generó el PDF.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("carpeta")
    ap.add_argument("--titulo", default="Cancionero")
    ap.add_argument("--autor", default="Resucitado 2025", help="nombre legal del autor")
    ap.add_argument("--seudonimo", default="", help="nombre artístico (opcional)")
    ap.add_argument("--rut", default="", help="RUT o documento del autor (opcional, va en la página legal)")
    ap.add_argument("--nota", default="Letras originales. Todos los derechos reservados.")
    ap.add_argument("--salida", help="ruta del PDF (por defecto, dentro de la carpeta)")
    a = ap.parse_args()
    albumes = recolectar(a.carpeta)
    doc, total = generar_html(albumes, a.titulo, a.autor, a.nota, a.seudonimo, a.rut, leer_extras(a.carpeta))
    pdf = a.salida or os.path.join(a.carpeta, a.titulo + ".pdf")
    html_path = os.path.splitext(pdf)[0] + ".html"
    open(html_path, "w", encoding="utf-8").write(doc)
    print(f"{total} canciones en {len(albumes)} álbumes -> {html_path}")
    a_pdf(html_path, pdf)
    print(f"PDF: {pdf}")


if __name__ == "__main__":
    main()
