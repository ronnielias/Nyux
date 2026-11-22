# Nyux 0.1: estado honesto

## Qué hace hoy

- Tokeniza y analiza archivos `.nyux`, crea un AST y lo compila a instrucciones
  de una máquina virtual de pila.
- Ejecuta esas instrucciones en una VM escrita en Python. `python3 nyux.py
  --bytecode examples/main.nyux` muestra el bytecode generado y luego lo corre.
- Implementa funciones, variables y ámbitos, asignación, `if`/`else`, `for`,
  `while` con límite, `return`, llamadas, operaciones aritméticas y booleanas.
- Reporta errores del lenguaje con línea y sugerencia en español.
- Abre una ventana nativa de host con un botón real (Tkinter en Linux; Win32 en
  Windows).
- Sirve páginas y rutas HTTP reales, solo en loopback (`127.0.0.1`). Las rutas
  API responden JSON fijo de demostración.
- Reserva una arena `bytearray` dentro del proceso Python y comprueba sus
  límites; permite leer y escribir en ella. `CPU::info()` y `System::boot()`
  consultan metadatos de la máquina anfitriona.

## Qué no hace

- El bytecode no es código máquina nativo; la VM depende de Python.
- `System::boot()` no arranca firmware ni un kernel. La arena no representa
  RAM física y no permite acceso a CPU, framebuffer, puertos o dispositivos.
- No tiene compilador bare-metal, bootloader, sistema de tipos estático ni
  garantías formales de memoria o concurrencia.
- El servidor no admite handlers dinámicos; `Web::api` devuelve una respuesta
  JSON fija.
- No carga drivers. `Driver::load()` falla con un mensaje explícito.
- No ejecuta IA. `AI::run()` falla hasta conectar un motor y un modelo.
- `XanOS.set_theme()` falla con un TODO: no aplica un tema real.
- Cambiar el fondo en Linux solo está implementado para GNOME; otros escritorios
  reciben un error explícito. Windows usa la API Win32.
- El efecto de vidrio depende del compositor: Linux solicita blur a KWin y, si
  no está disponible, usa transparencia con un aviso. No se garantiza blur en
  todos los entornos. Windows solicita el material DWM y avisa si falla.
- `Game::run()` tiene una demo gráfica en Linux; en Windows informa que falta
  implementar su renderizador.

## Errores y pruebas

Los errores de lexer, parser, compilación y ejecución conservan la línea de
origen y dan una sugerencia. La suite automatizada cubre compilación/VM,
funciones, ámbitos, control de flujo, errores, GUI (configuración de ventana),
HTTP loopback y APIs no implementadas.

Ejecuta:

```sh
python3 -m unittest discover -s tests -v
```

Los ejemplos son demos funcionales del runtime alojado, no evidencia de que
Nyux pueda construir aplicaciones autónomas o un sistema operativo.

## Trabajo necesario para un 10/10

1. Definir semántica y tipos del lenguaje, y compilar a código máquina sin
   depender del intérprete Python.
2. Construir targets nativos, runtime/ABI, linker y cadena de arranque.
3. Implementar kernel, memoria física, interrupciones, drivers y seguridad
   verificable, con pruebas en hardware/VM.
4. Añadir handlers web dinámicos y backends gráficos/juego completos por
   plataforma.
5. Implementar o integrar un motor/modelo de IA y la API de temas, o mantener
   esas interfaces explícitamente fuera del lenguaje.
6. Ejecutar validación automatizada en Linux y Windows, además de ampliar las
   pruebas de errores y bytecode.
