# web-walker

Bot de navegación humanizada para cursos Moodle con contenido H5P. Abre un Chromium real, espera tu login y luego recorre los módulos del curso solo, simulando un usuario real (pausas variables, ratón con trayectorias curvas, clicks en hotspots, lectura de vídeos, etc.).

---

## ¿Qué hace?

- Abre un Chromium **visible** y espera a que inicies sesión a mano.
- La URL del CLI es solo el **punto de partida**. La página que tengas activa cuando pulses ENTER queda como **página base**: a esa URL vuelve entre módulos.
- Recorre los módulos del curso por su **nombre** (configurable).
- Dentro de cada módulo, hace clicks aleatorios y humanizados sobre:
  - **Página siguiente / anterior** del libro H5P.
  - **Capítulos** (excluyendo los llamados "Ejercicios").
  - **Desplegables** (`.h5p-panel-button`).
  - **Hotspots**: clicka, espera unos segundos como leyendo, cierra el bubble.
  - **Vídeos**: detecta `<video>`, lo reproduce silenciado y espera su duración real.
- **Pausas** entre clicks con distribución lognormal + inercia (más natural que aleatorio uniforme).
- **Ratón** con trayectorias Bézier y velocidad variable (`playwright-stealth` + helpers propios).
- **Panel de estado** coloreado en la terminal cada 30s con tiempo de sesión, módulo actual, total de clicks, dedicación leída de la propia web, etc.
- **Detección de sesión caducada**: si Moodle te tira al login, pausa el bucle y espera a que vuelvas a entrar.
- **No repite el mismo módulo dos veces seguidas**.

---

## Instalación paso a paso

> ⚠️ Probado únicamente en **Ubuntu 24.04**. En otras distros Linux debería funcionar adaptando los `apt install` a tu gestor de paquetes, pero no está verificado.

### 1. Instala lo necesario del sistema

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Si tu distro no trae las librerías que Chromium necesita, instala también:

```bash
sudo apt install -y \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64
```

### 2. Clona el repositorio

```bash
git clone https://github.com/genre01/web-walker.git
cd web-walker
```

### 3. Crea el entorno virtual e instala las dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install playwright playwright-stealth
playwright install chromium
```

> `playwright install chromium` descarga ~170 MB la primera vez. Solo hay que hacerlo una vez por máquina.

A partir de aquí ya tienes todo instalado.

---

## Cómo ejecutarlo paso a paso

### 1. Activa el venv (si aún no está activo)

```bash
cd /ruta/a/web-walker
source .venv/bin/activate
```

Sabrás que está activo porque tu prompt empieza con `(.venv)`.

### 2. Lanza el script con una URL inicial

```bash
python web_walker_stealth.py "https://tu-campus.ejemplo.com/"
```

La URL puede ser la home del campus, la página de login o directamente la del curso — solo es donde se abrirá Chromium para que arranques.

### 3. Qué pasa después de lanzar

1. Se abre una ventana de Chromium en la URL indicada.
2. **El script se pausa en la terminal esperando ENTER**. **NO** lo pulses todavía.
3. En el navegador, **inicia sesión** con tu usuario y contraseña.
4. Después del login, **navega manualmente hasta la página principal del curso** que quieres testear (la que lista los módulos).
5. Ahora vuelve a la terminal y **pulsa ENTER**. La URL que tengas abierta en ese instante se convierte en la **página base**. A partir de aquí el script empieza a navegar solo.

### 4. Para detenerlo

- **Ctrl+C** en la terminal donde corre.
- O pasa una duración máxima como 2º argumento (ver más abajo).

### 5. Al terminar

```bash
deactivate
```

Vuelve a tu Python normal del sistema.

---

## Duración limitada (opcional)

Pasa los minutos como 2º argumento:

```bash
# 30 minutos exactos
python web_walker_stealth.py "https://tu-campus.ejemplo.com/" 30

# 2 horas
python web_walker_stealth.py "https://tu-campus.ejemplo.com/" 120

# Acepta decimales: 1.5 = 90 segundos
python web_walker_stealth.py "https://tu-campus.ejemplo.com/" 1.5
```

Sin argumento → corre indefinidamente hasta Ctrl+C.

---

## Lanzamiento sin activar el venv (un solo comando)

Útil para alias o scripts:

```bash
cd /ruta/a/web-walker
./.venv/bin/python web_walker_stealth.py "https://tu-campus.ejemplo.com/"
```

### En una ventana de terminal aparte (Linux con gnome-terminal)

```bash
gnome-terminal --title="Walker" -- bash -c \
  'cd /ruta/a/web-walker && \
   ./.venv/bin/python web_walker_stealth.py "https://tu-campus.ejemplo.com/"; \
   echo "--- terminado, pulsa Enter para cerrar"; read'
```

---

## Configuración

Edita el inicio de `web_walker_stealth.py`:

```python
# Lista de módulos del curso a los que el script entrará.
# Sustituye estos textos por los títulos visibles de los módulos en tu curso.
# La coincidencia es por substring sin acentos y case-insensitive.
MODULE_NAMES = [
    "Tema 1",
    "Tema 2",
    "Tema 3",
    "Tema 4",
    "Tema 5",
]

DEDICATION_REFRESH = 90  # segundos entre lecturas del bloque "Dedicación al curso"
STATUS_REFRESH = 30      # segundos entre paneles de estado en terminal
```

Solo cambia `MODULE_NAMES` por los títulos exactos de los módulos a los que quieras entrar. El resto de parámetros (pausas, pesos de acciones, clicks por módulo) están en las funciones `HumanPauser` y `h5p_session`.

---

## Qué se ve en la terminal

Mientras el script corre verás dos tipos de mensajes intercalados: el **log de navegación** y el **panel de estado**.

### Log de navegación

```
══ #3 · índice del curso ══
  → Entrando: Tema 2
    https://tu-campus.../mod/book/view.php?id=4567
    H5P: 14 interacciones planeadas
      [01/14] siguiente
      [02/14] capítulo «Dimensiones»
      [03/14] desplegable abierto
      [04/14] hotspot #2 (6.4s)
      [05/14] ⏵ reproduciendo video 03:42
      [05/14] video 03:42
      ...
```

Significado:
- `══ #N · índice del curso ══` → empieza la iteración N. El script ha vuelto a la página base.
- `→ Entrando: <nombre>` → módulo elegido al azar (de los definidos en `MODULE_NAMES`).
- `H5P: N interacciones planeadas` → número de clicks que va a hacer en este módulo.
- `[i/N] <acción>` → cada click del libro H5P:
  - `siguiente` / `anterior` → botones de paginación del libro.
  - `capítulo «Nombre»` → salto a un capítulo (filtra los llamados "Ejercicios").
  - `desplegable abierto` → expandió un panel colapsable.
  - `hotspot #X (Ys)` → clickó un hotspot y esperó Y segundos antes de cerrarlo.
  - `⏵ reproduciendo video MM:SS` → reproduce un vídeo y espera su duración real.
- `[TIEMPO] sesión activa: 00h 12m 30s` → reloj cada 30s (puede aparecer mezclado).

### Panel de estado

Cada 30 segundos:

```
┌─ ESTADO ─────────────────────────────────────────────
│ Sesión activa:   00h 12m 30s
│ Iteración:      #4   Módulos: 3   Clicks H5P: 32
│ Última pausa:   7.3s   Última acción: capítulo «Dimensiones»
│ Módulo actual:  Tema 2
│ Dedicación web: 8 horas 44 minutos  (actualizado hace 01m 12s)
└──────────────────────────────────────────────────────
```

Línea a línea:
- **Sesión activa**: tiempo desde que pulsaste ENTER para arrancar.
- **Iteración**: veces que ha vuelto al índice del curso para elegir módulo.
- **Módulos**: cuántos ha completado en esta sesión.
- **Clicks H5P**: total de interacciones dentro de los libros H5P.
- **Última pausa**: segundos esperados entre el click anterior y el actual.
- **Última acción**: la acción más reciente registrada.
- **Módulo actual**: en qué módulo está ahora (`(índice del curso)` si está eligiendo el siguiente).
- **Dedicación web**: valor del bloque "Dedicación al curso" de Moodle. Solo se lee en la página base, máximo una vez cada 90s.

### Mensajes especiales

- `⚠ Sesión caducada o cerrada` → Moodle te tiró al login. Vuelve a entrar y pulsa ENTER en la terminal.
- `✓ Sesión recuperada, sigo navegando` → ya estás logado y sigue.
- `(sin módulos nuevos en esta vista, vuelvo al índice)` → no encontró módulos coincidentes; reintenta.
- `[FIN] Alcanzada duración máxima de N min` → llegaste al tiempo límite.
- `[INTERRUMPIDO]` → pulsaste Ctrl+C.

---

## Notas operativas

- **Bloqueo de pantalla**: sigue funcionando (la sesión gráfica no se mata). **Pero la suspensión automática del equipo sí lo para**. Para sesiones largas, desactívala temporalmente:
  ```bash
  systemd-inhibit --what=sleep --who="walker" --why="QA tráfico" sleep infinity &
  ```
- **Foco de ventana**: al cargar páginas Chromium puede robar el foco. Si vas a trabajar en paralelo, mueve la ventana del navegador a otro workspace (Ctrl+Alt+→).
- **Logout en mitad de la sesión**: el script lo detecta y se queda en pausa esperando que vuelvas a entrar y pulses ENTER.

---

## Problemas conocidos

### El navegador se cierra solo con "Aw, Snap! / SIGTRAP"

Suele pasar en algunas **gráficas AMD** en Linux: el proceso de GPU del navegador crashea. Solución: lanza con el flag `--no-gpu`, que desactiva la aceleración por GPU.

```bash
python web_walker_stealth.py "https://tu-campus.ejemplo.com/course/view.php?id=123" --no-gpu
```

> ⚠️ **Usa `--no-gpu` solo si tienes ese crash.** En equipos con GPU que funciona bien, desactivarla hace que el navegador renderice por software y el consumo de RAM puede dispararse (hasta agotar la memoria). Por eso la GPU va activada por defecto.

El flag se puede combinar con los minutos:

```bash
python web_walker_stealth.py "https://tu-campus.ejemplo.com/course/view.php?id=123" 120 --no-gpu
```

---

## Estructura del repositorio

```
web-walker/
├── web_walker_stealth.py    # único script principal
├── README.md
└── .gitignore
```

---

## Licencia

Uso personal / QA. Sin licencia explícita: pregunta antes de redistribuir.
