#!/usr/bin/env python3
"""
Navegador "humano" para QA de cursos Moodle con contenido H5P — versión STEALTH.

Sobre la versión base añade:
- playwright-stealth: parchea navigator.webdriver, plugins, idiomas, etc.
- human_click: mueve el ratón al elemento con trayectoria curvada
  y velocidad variable antes de clickar.

Uso:
    python web_walker_stealth.py <URL_curso> [minutos]
"""
import math
import random
import sys
import threading
import time
import unicodedata
from urllib.parse import urldefrag, urlparse

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# ── Config ──────────────────────────────────────────────────────────────────
# Flag opcional --no-gpu: desactiva la aceleración por GPU. Útil SOLO si el
# navegador crashea con "Aw, Snap! SIGTRAP" (típico en algunas GPUs AMD).
# OJO: en máquinas con GPU sana, desactivarla dispara el uso de RAM (SwiftShader
# renderizando WebGL por software), así que por defecto va ACTIVADA.
DISABLE_GPU = "--no-gpu" in sys.argv
args_clean = [a for a in sys.argv[1:] if a != "--no-gpu"]

if len(args_clean) < 1:
    print("Uso: python web_walker_stealth.py <URL_curso> [minutos] [--no-gpu]")
    print("  URL_curso : página principal del curso a recorrer")
    print("  minutos   : duración máxima (opcional; si se omite, corre hasta Ctrl+C)")
    print("  --no-gpu  : desactiva la GPU (solo si el navegador crashea con SIGTRAP)")
    sys.exit(1)

START_URL = args_clean[0]
# 2º argumento opcional: duración máxima en minutos. Si no se pasa, navega indefinidamente.
MAX_MINUTES = float(args_clean[1]) if len(args_clean) > 1 else 0
TARGET_HOST = urlparse(START_URL).netloc

# Lista de módulos del curso a los que el script entrará.
# Sustituye estos textos por los títulos visibles de los módulos en tu curso.
# La coincidencia es por substring sin acentos y case-insensitive.
MODULE_NAMES = [
    "Introducción a la prevención de riesgos laborales",
    "Riesgos generales y su prevención",
    "Riesgos específicos y su prevención",
    "Elementos básicos de gestión de la prevención de riesgos",
    "Primeros auxilios",
]

DEDICATION_REFRESH = 90  # segundos entre lecturas del bloque de dedicación
STATUS_REFRESH = 30      # segundos entre paneles de estado

# ── ANSI colors ─────────────────────────────────────────────────────────────
C = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "gray":    "\033[90m",
    "red":     "\033[31m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "blue":    "\033[34m",
    "magenta": "\033[35m",
    "cyan":    "\033[36m",
    "white":   "\033[97m",
}
def c(text, *styles):
    return "".join(C[s] for s in styles) + str(text) + C["reset"]

# ── Helpers ─────────────────────────────────────────────────────────────────
def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower().strip()

MODULE_NAMES_NORM = [normalize(n) for n in MODULE_NAMES]

def fmt_secs(sec: float) -> str:
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h:02d}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"

# ── Estado compartido para el hilo de estado ────────────────────────────────
STATE = {
    "session_start": None,
    "iteration": 0,
    "current_module": "—",
    "current_url": START_URL,
    "h5p_clicks": 0,
    "modules_visited": 0,
    "last_pause": None,
    "last_action": "—",
    "dedication": None,
    "dedication_ts": None,
    "lock": threading.Lock(),
}

# ── Pausas humanas: lognormal + inercia + "lectura profunda" ────────────────
class HumanPauser:
    def __init__(self):
        self.last = None
    def pause(self):
        # Lognormal con mediana ~5.5s, p95 ~16s
        sample = random.lognormvariate(1.7, 0.55)
        if self.last is not None:
            # 30% de inercia respecto a la pausa anterior
            sample = 0.7 * sample + 0.3 * self.last
        # 12% de probabilidad de "lectura profunda"
        if random.random() < 0.12:
            sample *= random.uniform(2.0, 3.5)
        sample = max(2.0, min(40.0, sample))
        self.last = sample
        with STATE["lock"]:
            STATE["last_pause"] = sample
        time.sleep(sample)
        return sample

pauser = HumanPauser()

def human_scroll(page):
    try:
        for _ in range(random.randint(2, 6)):
            page.mouse.wheel(0, random.randint(250, 700))
            time.sleep(random.uniform(0.5, 1.4))
        if random.random() < 0.3:
            page.mouse.wheel(0, -random.randint(150, 400))
            time.sleep(random.uniform(0.4, 1.0))
    except Exception:
        pass

# ── Movimiento de ratón "humano" ────────────────────────────────────────────
# Mantenemos la posición lógica del cursor para poder partir de "donde estaba"
CURSOR = {"x": 200.0, "y": 200.0}

def _bezier_path(x0, y0, x1, y1, n_points=None):
    """Genera puntos a lo largo de una curva cuadrática con control aleatorio."""
    dist = math.hypot(x1 - x0, y1 - y0)
    if n_points is None:
        n_points = max(18, min(60, int(dist / 8)))
    # Punto de control desviado perpendicular a la línea (curva natural)
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    dx, dy = x1 - x0, y1 - y0
    perp_x, perp_y = -dy, dx
    norm = math.hypot(perp_x, perp_y) or 1.0
    perp_x, perp_y = perp_x / norm, perp_y / norm
    bow = random.uniform(0.05, 0.25) * dist * random.choice([-1, 1])
    cx, cy = mx + perp_x * bow, my + perp_y * bow
    pts = []
    for i in range(1, n_points + 1):
        t = i / n_points
        # Bézier cuadrática
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1
        pts.append((bx, by))
    return pts

def human_move_to(page, x, y):
    """Mueve el ratón con curva Bézier y velocidad variable hasta (x, y)."""
    x0, y0 = CURSOR["x"], CURSOR["y"]
    pts = _bezier_path(x0, y0, x, y)
    total = len(pts)
    for i, (px, py) in enumerate(pts):
        # Velocidad: lenta al inicio y al final, rápida en medio (ease-in-out)
        progress = i / max(1, total - 1)
        speed_factor = 0.5 + 2.0 * (1.0 - abs(0.5 - progress) * 2.0)
        # Pequeña sacudida natural
        jx = random.gauss(0, 0.6)
        jy = random.gauss(0, 0.6)
        try:
            page.mouse.move(px + jx, py + jy)
        except Exception:
            return
        time.sleep(random.uniform(0.006, 0.018) / speed_factor)
    CURSOR["x"], CURSOR["y"] = x, y

def human_click(page, locator):
    """Mueve el ratón hasta un punto aleatorio dentro del elemento y clicka."""
    try:
        locator.scroll_into_view_if_needed(timeout=2500)
    except Exception:
        pass
    box = locator.bounding_box()
    if not box:
        # Fallback: click sin movimiento
        locator.click(timeout=4000)
        return
    # Punto aleatorio cerca del centro, no en bordes
    pad_x = box["width"] * 0.20
    pad_y = box["height"] * 0.20
    tx = box["x"] + random.uniform(pad_x, box["width"] - pad_x)
    ty = box["y"] + random.uniform(pad_y, box["height"] - pad_y)
    human_move_to(page, tx, ty)
    # Mini pausa de "reacción humana"
    time.sleep(random.uniform(0.08, 0.22))
    page.mouse.down()
    time.sleep(random.uniform(0.04, 0.11))
    page.mouse.up()

# ── Lectura del bloque "Dedicación al curso" ────────────────────────────────
def fetch_dedication(page):
    js = """
    () => {
      const block = document.querySelector('.block_dedication .content');
      if (!block) return null;
      // Buscamos el <p> que tenga al menos un dígito (el del tiempo)
      const ps = block.querySelectorAll('p');
      for (const p of ps) {
        const t = (p.textContent || '').trim();
        if (/\\d/.test(t)) return t;
      }
      return null;
    }
    """
    try:
        val = page.evaluate(js)
        if val and val.strip():
            return val.strip()
    except Exception:
        pass
    return None

def maybe_refresh_dedication(page):
    now = time.time()
    with STATE["lock"]:
        ts = STATE["dedication_ts"]
    if ts is None or (now - ts) >= DEDICATION_REFRESH:
        val = fetch_dedication(page)
        if val is not None:
            with STATE["lock"]:
                STATE["dedication"] = val
                STATE["dedication_ts"] = now

# ── Buscar módulos por nombre ───────────────────────────────────────────────
# Palabras que descartan un enlace aunque coincida con un MODULE_NAME (evita exámenes etc.)
EXCLUDE_KEYWORDS = ("ejercicio", "examen", "test", "cuestionario", "evaluaci", "prueba", "quiz")
# Tipos de módulo Moodle que NO queremos abrir nunca
EXCLUDE_MOD_TYPES = ("/mod/quiz/", "/mod/assign/", "/mod/feedback/", "/mod/choice/",
                     "/mod/survey/", "/mod/workshop/", "/mod/scorm/", "/mod/h5pactivity/")

def find_module_links(page):
    js = """
    () => {
      const c = document.querySelector('#region-main') || document.body;
      return Array.from(c.querySelectorAll('a[href]'))
        .filter(a => a.offsetParent !== null)
        .map(a => ({href: a.href, text: (a.innerText || a.textContent || '').trim()}));
    }
    """
    items = page.evaluate(js)
    out = []
    seen = set()
    for it in items:
        t = normalize(it["text"])
        if not t:
            continue
        # Descarta por palabras de evaluación en el texto
        if any(bad in t for bad in EXCLUDE_KEYWORDS):
            continue
        href, _ = urldefrag(it["href"])
        # Descarta enlaces a la propia página base (ej. anclas de sección)
        if href.rstrip("/") == START_URL.rstrip("/"):
            continue
        # Descarta por tipo de módulo en la URL
        if any(bad in href for bad in EXCLUDE_MOD_TYPES):
            continue
        if urlparse(href).netloc != TARGET_HOST or href in seen:
            continue
        for idx, name in enumerate(MODULE_NAMES_NORM):
            if name in t:
                seen.add(href)
                out.append((href, MODULE_NAMES[idx]))
                break
    return out

# ── Detección y clicks H5P ──────────────────────────────────────────────────
def _do_click(page, locator):
    human_click(page, locator)

def find_visible_video(scope):
    try:
        videos = scope.locator("video")
        n = videos.count()
        for i in range(n):
            v = videos.nth(i)
            try:
                if v.is_visible(timeout=300):
                    return v
            except Exception:
                continue
    except Exception:
        pass
    return None

def play_video(page, scope):
    v = find_visible_video(scope)
    if v is None:
        return None
    try:
        v.evaluate(
            "el => { el.muted = true; const p = el.play(); if (p && p.catch) p.catch(() => {}); }"
        )
    except Exception:
        try:
            _do_click(page, v)
        except Exception:
            pass
    duration = None
    for _ in range(20):
        try:
            d = v.evaluate("el => el.duration")
            if isinstance(d, (int, float)) and d > 0:
                duration = float(d)
                break
        except Exception:
            pass
        time.sleep(0.5)
    if duration is None:
        wait = 90.0
        label = "video (duración desconocida, espero 90s)"
    else:
        wait = min(duration, 15 * 60) + random.uniform(2, 6)
        m, s = divmod(int(duration), 60)
        label = f"video {m:02d}:{s:02d}"
    print(c(f"      ⏵ reproduciendo {label}", "blue"))
    time.sleep(wait)
    return label

def get_h5p_scope(page):
    try:
        if page.locator(".h5p-interactive-book-status-arrow").count() > 0:
            return page
    except Exception:
        pass
    for fr in page.frames:
        try:
            if fr.locator(".h5p-interactive-book-status-arrow").count() > 0:
                return fr
        except Exception:
            continue
    return None

def trigger_resize(page):
    """Dispara evento resize en la página y todos sus iframes para que H5P recalcule layout."""
    try:
        page.evaluate("window.dispatchEvent(new Event('resize'))")
    except Exception:
        pass
    for fr in page.frames:
        try:
            fr.evaluate("window.dispatchEvent(new Event('resize'))")
        except Exception:
            continue

def get_h5p_container_size(page):
    """Devuelve (w,h) del contenedor H5P principal, o None si no encontrado."""
    selectors = [
        ".h5p-interactive-book",
        ".h5p-iframe",
        ".h5p-content",
        ".h5pactivity-content",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                box = loc.bounding_box()
                if box:
                    return (box["width"], box["height"])
        except Exception:
            continue
    # También buscar en iframes
    for fr in page.frames:
        for sel in selectors:
            try:
                loc = fr.locator(sel).first
                if loc.count() > 0:
                    box = loc.bounding_box()
                    if box:
                        return (box["width"], box["height"])
            except Exception:
                continue
    return None

def wait_for_h5p_ready(page, max_attempts=3, per_attempt_timeout=8.0):
    """
    Espera a que el H5P se renderice correctamente.
    - Si no aparece o aparece muy pequeño, recarga y reintenta.
    - Tras detectarlo, dispara resize para forzar recálculo del layout H5P.
    """
    for attempt in range(1, max_attempts + 1):
        start = time.time()
        detected = False
        while time.time() - start < per_attempt_timeout:
            if get_h5p_scope(page) is not None:
                detected = True
                break
            time.sleep(0.5)

        if detected:
            # Forzar resize para que H5P redibuje a tamaño correcto
            trigger_resize(page)
            time.sleep(1.0)
            size = get_h5p_container_size(page)
            if size:
                print(c(f"    H5P listo ({size[0]:.0f}x{size[1]:.0f} px)", "dim"))
            else:
                print(c("    H5P listo (tamaño no medible)", "dim"))
            return True
        else:
            if attempt < max_attempts:
                print(c(f"    H5P no apareció en {per_attempt_timeout:.0f}s, "
                        f"recargando (intento {attempt+1}/{max_attempts})...", "yellow"))

        if attempt < max_attempts:
            try:
                page.reload(wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                print(c(f"    Recarga falló: {e}", "red"))
            time.sleep(random.uniform(2.0, 3.5))
    return False

def h5p_session(page):
    n_clicks = random.randint(8, 20)
    print(c(f"    H5P: {n_clicks} interacciones planeadas", "dim"))
    fails = 0
    for i in range(1, n_clicks + 1):
        scope = get_h5p_scope(page)
        if scope is None:
            print(c("    H5P no detectado, salgo del módulo", "yellow"))
            return
        # Inventario de acciones disponibles + pesos
        actions = []
        try:
            if scope.locator(".h5p-interactive-book-status-arrow.next:not(.disabled)").count() > 0:
                actions.append(("next", 50))
        except Exception:
            pass
        try:
            if scope.locator(".h5p-interactive-book-status-arrow.previous:not(.disabled)").count() > 0:
                actions.append(("prev", 10))
        except Exception:
            pass
        # Moodle Book: enlaces "Siguiente / Anterior" al pie del libro (fuera del iframe H5P)
        try:
            if page.locator(".navbottom a.booknext").count() > 0:
                actions.append(("booknext", 25))
        except Exception:
            pass
        try:
            if page.locator(".navbottom a.bookprev").count() > 0:
                actions.append(("bookprev", 8))
        except Exception:
            pass
        ch_loc = scope.locator(".h5p-interactive-book-navigation-chapter-button")
        safe_chapters = []
        try:
            for ci in range(ch_loc.count()):
                try:
                    title = ch_loc.nth(ci).locator(
                        ".h5p-interactive-book-navigation-chapter-title-text"
                    ).first.text_content(timeout=300) or ""
                except Exception:
                    title = ""
                t = title.lower()
                if not any(k in t for k in ("ejercicio", "examen", "test", "cuestionario", "evaluaci", "prueba", "quiz")):
                    safe_chapters.append((ci, title.strip()))
        except Exception:
            pass
        if safe_chapters:
            actions.append(("chapter", 12))
        panels = scope.locator('.h5p-panel-button[aria-expanded="false"]')
        try:
            if panels.count() > 0:
                actions.append(("panel", 14))
        except Exception:
            pass
        hotspots = scope.locator(".h5p-image-hotspot")
        try:
            if hotspots.count() > 0:
                actions.append(("hotspot", 14))
        except Exception:
            pass
        if find_visible_video(scope) is not None:
            actions.append(("video", 22))

        names = [a[0] for a in actions]
        weights = [a[1] for a in actions]
        choice = random.choices(names, weights=weights, k=1)[0]
        try:
            if choice == "next":
                _do_click(page, scope.locator(".h5p-interactive-book-status-arrow.next").first)
                label = "siguiente"
            elif choice == "prev":
                _do_click(page, scope.locator(".h5p-interactive-book-status-arrow.previous").first)
                label = "anterior"
            elif choice == "chapter":
                idx, title = random.choice(safe_chapters)
                _do_click(page, ch_loc.nth(idx))
                label = f"capítulo «{title}»" if title else f"capítulo #{idx+1}"
            elif choice == "booknext":
                _do_click(page, page.locator(".navbottom a.booknext").first)
                label = "siguiente (Moodle Book)"
            elif choice == "bookprev":
                _do_click(page, page.locator(".navbottom a.bookprev").first)
                label = "anterior (Moodle Book)"
            elif choice == "panel":
                count = panels.count()
                idx = random.randint(0, count - 1)
                _do_click(page, panels.nth(idx))
                label = "desplegable abierto"
            elif choice == "hotspot":
                count = hotspots.count()
                idx = random.randint(0, count - 1)
                _do_click(page, hotspots.nth(idx))
                read = random.uniform(4.0, 10.0)
                time.sleep(read)
                try:
                    _do_click(page, scope.locator(".h5p-image-hotspot-close-popup-button").first)
                except Exception:
                    pass
                label = f"hotspot #{idx+1} ({read:.1f}s)"
            else:  # video
                info = play_video(page, scope)
                label = info or "video no reproducido"
                try:
                    _do_click(page, scope.locator(".h5p-interactive-book-status-arrow.next").first)
                except Exception:
                    pass
            fails = 0
            with STATE["lock"]:
                STATE["h5p_clicks"] += 1
                STATE["last_action"] = label
            print(c(f"      [{i:02d}/{n_clicks:02d}] {label}", "gray"))
        except Exception as e:
            fails += 1
            print(c(f"      [{i:02d}/{n_clicks:02d}] click falló: {type(e).__name__}", "red"))
            if fails >= 3:
                print(c("    3 fallos seguidos, salgo del módulo", "yellow"))
                return
            time.sleep(1.5)
            continue
        time.sleep(random.uniform(0.5, 1.2))
        if random.random() < 0.5:
            human_scroll(page)
        pauser.pause()

# ── Panel de estado ─────────────────────────────────────────────────────────
def status_panel():
    with STATE["lock"]:
        s = dict(STATE)
        s.pop("lock", None)
    sess = fmt_secs(time.time() - s["session_start"])
    ded = s["dedication"] or "—"
    if s["dedication_ts"]:
        age = fmt_secs(time.time() - s["dedication_ts"])
        ded_line = f"{c(ded, 'bold', 'cyan')}  {c(f'(actualizado hace {age})', 'dim')}"
    else:
        ded_line = c("pendiente de leer…", "dim")
    last_pause = f"{s['last_pause']:.1f}s" if s["last_pause"] else "—"
    mod = s["current_module"] or "—"

    bar = c("─" * 60, "gray")
    print()
    print(c("┌─ ESTADO ", "magenta", "bold") + bar)
    print(c("│ ", "magenta") + f"{c('Sesión activa:', 'bold')}   {c(sess, 'green', 'bold')}")
    print(c("│ ", "magenta") + f"{c('Iteración:', 'bold')}      #{s['iteration']}   "
          f"{c('Módulos:', 'bold')} {s['modules_visited']}   "
          f"{c('Clicks H5P:', 'bold')} {s['h5p_clicks']}")
    print(c("│ ", "magenta") + f"{c('Última pausa:', 'bold')}   {last_pause}   "
          f"{c('Última acción:', 'bold')} {s['last_action']}")
    print(c("│ ", "magenta") + f"{c('Módulo actual:', 'bold')}  {c(mod, 'yellow')}")
    print(c("│ ", "magenta") + f"{c('Dedicación web:', 'bold')} {ded_line}")
    print(c("└", "magenta") + bar)
    print()

def start_status_thread():
    stop_evt = threading.Event()
    def loop():
        while not stop_evt.wait(STATUS_REFRESH):
            status_panel()
    threading.Thread(target=loop, daemon=True).start()
    return stop_evt

# ── Loop principal ──────────────────────────────────────────────────────────
def in_course_view(url: str) -> bool:
    return "/course/view.php" in url

def is_logged_out(page) -> bool:
    """True si vemos el botón de login o estamos en /login/."""
    try:
        if "/login/" in page.url:
            return True
        return page.evaluate(
            """() => {
                if (document.querySelector('#pre-login-form')) return true;
                const b = document.querySelector('button.btn-login');
                return !!b;
            }"""
        )
    except Exception:
        return False

def wait_for_relogin(page):
    """Pausa la navegación hasta que el usuario vuelva a iniciar sesión."""
    print(c("\n⚠  Sesión caducada o cerrada — botón de login detectado.", "red", "bold"))
    print(c("   Vuelve a iniciar sesión en el navegador y pulsa ENTER aquí.", "yellow"))
    while True:
        try:
            input(c(">> ENTER para continuar... ", "yellow", "bold"))
        except EOFError:
            time.sleep(5)
            continue
        try:
            page.goto(START_URL, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass
        if not is_logged_out(page):
            print(c("✓  Sesión recuperada, sigo navegando.\n", "green", "bold"))
            return
        print(c("   Sigo viendo el botón de login. Revisa e intenta de nuevo.", "red"))

def main():
    print(c("╔══════════════════════════════════════════════════════════╗", "cyan", "bold"))
    print(c("║              WEB WALKER — campus QA                       ║", "cyan", "bold"))
    print(c("╚══════════════════════════════════════════════════════════╝", "cyan", "bold"))
    print(c(f"  URL inicial: {START_URL}", "dim"))
    print(c("  (la página base real se fijará al pulsar ENTER tras el login)", "dim"))
    print(c(f"  Módulos objetivo: {len(MODULE_NAMES)}", "dim"))
    if MAX_MINUTES > 0:
        print(c(f"  Duración máx: {MAX_MINUTES:g} min (Ctrl+C para parar antes)", "dim"))
    else:
        print(c("  Modo: infinito (Ctrl+C para parar)", "dim"))

    with Stealth().use_sync(sync_playwright()) as p:
        launch_args = []
        if DISABLE_GPU:
            # Solo si se pasa --no-gpu (workaround para crash SIGTRAP en GPUs AMD).
            launch_args = [
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-gpu-compositing",
            ]
            print(c("  GPU desactivada (--no-gpu). Ojo: puede aumentar el uso de RAM.", "yellow"))
        browser = p.chromium.launch(headless=False, args=launch_args)
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 820},
            locale="es-ES",
            timezone_id="Europe/Madrid",
        )
        page = ctx.new_page()
        page.goto(START_URL, wait_until="domcontentloaded")

        input(c(
            "\n>> Inicia sesión en el navegador y pulsa ENTER para empezar... ",
            "yellow", "bold"))
        # La URL del CLI se usa como página base del bucle (a esa URL vuelve
        # entre módulos). No se captura nada del navegador.

        STATE["session_start"] = time.time()
        stop_status = start_status_thread()
        last_module_name = None
        try:
            while True:
                if MAX_MINUTES > 0 and (time.time() - STATE["session_start"]) >= MAX_MINUTES * 60:
                    print(c(f"\n[FIN] Alcanzada duración máxima de {MAX_MINUTES:g} min", "yellow", "bold"))
                    break
                # ir al índice del curso
                STATE["iteration"] += 1
                page.goto(START_URL, wait_until="domcontentloaded")
                if is_logged_out(page):
                    wait_for_relogin(page)
                with STATE["lock"]:
                    STATE["current_url"] = page.url
                    STATE["current_module"] = "(índice del curso)"
                print(c(f"\n══ #{STATE['iteration']} · índice del curso ══", "cyan", "bold"))
                human_scroll(page)
                maybe_refresh_dedication(page)
                pauser.pause()

                links = find_module_links(page)
                if not links:
                    print(c("  No se encontraron módulos coincidentes. Reintento en 15s.", "yellow"))
                    time.sleep(15)
                    continue

                # Excluir el último módulo visitado si hay alternativas
                candidates = [l for l in links if l[1] != last_module_name] or links
                href, name = random.choice(candidates)
                last_module_name = name
                with STATE["lock"]:
                    STATE["current_module"] = name
                    STATE["current_url"] = href
                print(c(f"  → Entrando: {name}", "green", "bold"))
                print(c(f"    {href}", "dim"))
                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=25000)
                except Exception as e:
                    print(c(f"  fallo abriendo módulo: {e}", "red"))
                    time.sleep(5)
                    continue

                # Pausa breve para que Moodle empiece a renderizar
                time.sleep(random.uniform(1.5, 2.5))
                if is_logged_out(page):
                    wait_for_relogin(page)
                    continue
                # Esperar a que H5P esté listo (con recargas si no aparece)
                if wait_for_h5p_ready(page):
                    human_scroll(page)
                    h5p_session(page)
                else:
                    print(c("    H5P no apareció tras varios reintentos, paso al siguiente módulo", "yellow"))
                with STATE["lock"]:
                    STATE["modules_visited"] += 1
                pauser.pause()
        except KeyboardInterrupt:
            print(c("\n[INTERRUMPIDO]", "yellow", "bold"))
        finally:
            stop_status.set()
            status_panel()
            total = fmt_secs(time.time() - STATE["session_start"])
            print(c(f"[FIN] Tiempo total de sesión: {total}", "green", "bold"))
            input(c(">> ENTER para cerrar el navegador... ", "yellow"))
            ctx.close()
            browser.close()

if __name__ == "__main__":
    main()
