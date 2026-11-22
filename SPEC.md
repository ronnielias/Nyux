# Nyux Universal — especificación del prototipo

## Propósito y estado

Nyux pretende usar un mismo lenguaje para programas de escritorio, web, juegos
y código de sistema. Nyux 0.1 tiene lexer, parser, AST, compilador a bytecode
de pila y una VM en `nyux.py`. La VM está escrita en Python 3: el bytecode no
es código máquina nativo y requiere Python para ejecutarse.

La sintaxis de usuario no requiere headers ni puntos y coma, y no expone
punteros ni `malloc/free`. Estas decisiones de sintaxis no son por sí solas una
garantía de seguridad de memoria.

## Sintaxis disponible

Las instrucciones son `let`, asignación (`nombre = valor`), `fn`, `if`/`else`,
`for`, `while`, `return`, `print` y `use`. Las expresiones incluyen cadenas,
números, booleanos, paréntesis, llamadas estáticas y de método, enums,
operadores aritméticos y de comparación, y operadores lógicos `and`, `or`, `not`.
Los puntos y coma son opcionales.

```nyux
fn main() {
    let total = 0
    while (total < 8 and true) {
        total = total + 1
    }
    if (total == 8) {
        print("Nyux")
    }
}
```

La precedencia, de menor a mayor, es `or`, `and`, comparación (`==`, `!=`, `<`,
`<=`, `>`, `>=`), suma/resta y multiplicación/división/módulo. `let` declara
una variable de bloque y la asignación actualiza una declaración existente.
Las variables locales de `if`, `for`, `while` y funciones no escapan de su
bloque.

`while` tiene un límite de seguridad de 1.000.000 iteraciones. Todavía no
existen colecciones, tipos definidos por el usuario, `break`/`continue`,
concurrencia, macros ni un sistema de tipos estático.

## APIs del runtime anfitrión

### Escritorio

- `Window::new(título, ancho, alto)` construye una ventana.
- `window.add_button(texto)` agrega un botón; `window.show()` la abre.
- En Linux se usa Tkinter/X11; en Windows, Win32 mediante `ctypes`.
- El blur/transparencia depende del compositor; el runtime avisa cuando no
  puede aplicar el efecto solicitado.
- `XanOS.set_theme(...)` aún no aplica un tema y devuelve un error TODO.
- Cambiar wallpaper usa la API Win32 en Windows y GSettings en GNOME. Otros
  escritorios Linux devuelven un error TODO en vez de simular un cambio.

### Web

- `Web::server(puerto)` crea un servidor HTTP enlazado solo a `127.0.0.1`.
- `Web::page(ruta, contenido)` registra una página HTML.
- `Web::api(ruta)` registra una respuesta JSON fija de demostración.
- `server.serve()` bloquea hasta Ctrl+C.

### Sistema alojado

- `System::boot()` crea un descriptor de la plataforma anfitriona;
  `system.name()` devuelve su nombre. No arranca firmware ni reemplaza el SO.
- `Memory::init()` o `Memory::init(bytes)` crea una arena Python inicializada a
  cero, limitada a 64 MiB. `memory.read(offset, length)`,
  `memory.read_text(offset, length)`, `memory.write(offset, texto)` y
  `memory.capacity()` comprueban los límites.
- `Process::spawn(programa, argumentos...)` inicia un proceso anfitrión sin
  shell.
- `File::open(ruta, modo)` abre archivos de texto del host; los handles que
  siguen abiertos se cierran al terminar el programa.
- `Screen::write(texto)` escribe en la consola del proceso.
- `CPU::info()` lee arquitectura y conteo de hilos reportados por el host.
- `Driver::load(...)` devuelve un error: el runtime alojado no puede instalar
  drivers del kernel.

### Juegos e IA

- `Game::start()` crea una sesión; `game.run()` abre un bucle gráfico del host.
  En Linux el backend incluye una demo de clic; en Windows devuelve un error
  TODO.
- `AI::run()` devuelve un error claro porque no hay un motor ni un modelo
  configurado; no inventa resultados ni simula inferencia.

## Garantías y límites

El programa fuente se compila a instrucciones de una VM de pila y estas se
ejecutan realmente; `python3 nyux.py --bytecode archivo.nyux` muestra las
instrucciones antes de ejecutarlas. La VM sigue dependiendo de Python y de su
administración de memoria. No genera código máquina ni garantiza velocidad
comparable a C, ausencia formal de data races ni las garantías de
tipos/ownership de Rust. Los errores de fuente indican línea y sugerencia.

`System::boot`, `Memory::init` y `Screen::write` operan sobre el proceso y los
recursos del sistema anfitrión: no arrancan firmware, no reservan RAM física y
no escriben al framebuffer. No hay compilador nativo, target bare-metal,
bootloader, modelo de concurrencia, inferencia de IA ni carga de drivers. Por
eso Nyux Universal es hoy un prototipo ejecutable multipropósito, no un lenguaje
listo para crear o arrancar un sistema operativo completo.

El README contiene los comandos de ejecución y `HONEST.md` enumera el alcance
actual y los TODOs. La meta de ergonomía es ofrecer una experiencia más simple que las APIs
manuales de C y evitar que el usuario tenga que escribir reglas de ownership
como las de Rust en ejemplos pequeños. Aún no se puede afirmar que supere en
rendimiento, seguridad formal o capacidades a C, Rust, Python o JavaScript.
