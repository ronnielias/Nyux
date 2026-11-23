# Nyux 0.2 — extensiones alojadas

Nyux 0.2 se desarrolla junto a Nyux 0.1. El tag `v0.1`, `nyux.py` y el comando
`./nyux run ...` permanecen intactos. La entrada 0.2 está en `nyux02.py` y se
ejecuta con `./nyux2 run archivo.nyux`.

No necesita paquetes externos: usa Python 3 y su biblioteca estándar. Su
bytecode aún se ejecuta en una VM escrita en Python.

## Datos, CSV y predicción sencilla

- `CSV::load(path)` o `Data::load_csv(path)` carga UTF-8/CSV con cabecera.
  Límites actuales: 32 MiB y 100000 filas.
- Un DataFrame admite `count()`, `columns_json()`, `value(indice, columna)`,
  `mean(columna)` y `to_json()`.
- `AI::fit_linear(data, entrada, objetivo)` ajusta una regresión lineal por
  mínimos cuadrados; `modelo.predict(x)` predice y `summary_json()` devuelve
  coeficientes y R².
- Es un ejemplo de ML numérico simple, no una IA general ni una red neuronal.

```nyux
let scores = CSV::load("examples/v0.2/scores.csv")
let model = AI::fit_linear(scores, "hours", "score")
print("Predicción:", model.predict(6))
```

`examples/v0.2/prediction.nyux` realiza esta predicción con datos del CSV.

## Web con archivos y handlers Nyux

- `Web::app(puerto, carpeta)` sirve archivos desde una carpeta local y escucha
  solo en `127.0.0.1`.
- `app.route("GET" o "POST", ruta, "funcion")` conecta una función Nyux que
  recibe `(path, body)`. Devuelve texto mediante `Web::html(texto)` o una
  respuesta JSON mediante `Web::json(valor)`.
- Sirve `index.html`, CSS y JavaScript; bloquea escapes del directorio raíz,
  limita cuerpos entrantes a 1 MiB y archivos servidos a 8 MiB.
- Esto no es un servidor listo para producción: no incluye TLS, autenticación,
  sesiones, límites de tasa ni un framework completo.

`examples/v0.2/web-app.nyux` usa los activos de
`examples/v0.2/site/public/` y prueba el endpoint POST `/api/echo`.

## Bytecode y errores antes de ejecutar

- La VM 0.2 pliega expresiones constantes sencillas durante la compilación. No
  es compilación nativa ni una promesa de rendimiento comparable con C++/Rust.
- El verificador previo detecta nombres no definidos, llamadas a funciones
  desconocidas o con aridad incorrecta, métodos estáticos desconocidos,
  argumentos incompatibles en APIs conocidas, redeclaraciones en un bloque,
  algunos tipos incompatibles y errores evidentes en límites de `for`.
- Las anotaciones `let age: Int = 30` (también `Float`, `Number`, `String`,
  `Bool`) se validan estáticamente cuando el valor es conocido y además se
  comprueban en ejecución. `fun double(x: Int): Int = x * 2` es una forma
  corta; las anotaciones conocidas se propagan a los puntos de llamada para
  rechazar argumentos o resultados incompatibles antes de ejecutar. Los
  valores dinámicos conservan también la comprobación en ejecución.
- No es un sistema de tipos estático completo: valores dinámicos y muchas APIs
  anfitrionas solo se pueden validar al ejecutarse. No ofrece las garantías
  formales de Swift o Rust.

## Concurrencia

- `Task::spawn("funcion", argumentos...)` ejecuta funciones Nyux en un pool
  acotado a 16 hilos y 1024 tareas pendientes por runtime; si se alcanza el
  límite, devuelve un error claro en vez de aceptar trabajo sin límite.
- `Task::all(tarea1, tarea2, ...)` recoge sus resultados; los handles admiten
  `.await()`, `.result()`, `.done()` y `.cancel()`. `await` y `result` esperan
  como máximo 30 segundos por defecto; se puede indicar otro timeout en
  milisegundos, como `tarea.await(5000)`. Si vence, informa que la tarea sigue
  ejecutándose y el handle puede volver a consultarse.
- `Time::sleep(ms)` puede usarse para esperas acotadas (máximo 60 segundos).
- Se verificaron 40 tareas pendientes. Los threads de CPython están sujetos al
  GIL: no se afirma aceleración de cómputo CPU-bound ni ausencia formal de
  data races.

## Hardware anfitrión y sensores

- En Linux, `Hardware::read_text(path)` lee archivos de solo lectura bajo
  `/proc` o `/sys`, tras resolver la ruta para impedir salir de esos árboles.
  Limita la lectura a 1 MiB.
- `Sensor::temperatures()` lee zonas térmicas expuestas bajo
  `/sys/class/thermal`; puede devolver cero lecturas si el host no publica
  sensores.
- No lee/escribe puertos, RAM física, registros de dispositivo, ni instala
  drivers. En Windows/macOS estas APIs dan un error explícito de plataforma no
  implementada.

## Juegos, apps y plataformas

- `Game::world(título, ancho, alto)` crea un pequeño mundo 2D; `spawn`,
  `move`, `add_score` y `state_json` permiten simular entidades. `run()` abre
  una ventana Tk con entidades, movimiento de teclado y marcador.
- SQL, páginas web y ventanas anfitrionas sirven de base para demos de
  aplicaciones empresariales; no equivalen a un framework empresarial ni a
  Unity.
- Las funciones cortas reducen código repetitivo, pero no generan APK ni
  integran el SDK de Android/Kotlin.

## Límites pendientes para un lenguaje completo

1. Compilador nativo, ABI, linker, optimización de máquina y benchmarks
   reproducibles en comparación con lenguajes compilados.
2. Sistema de tipos estático completo, análisis de flujo y garantías formales de
   memoria/threads.
3. Backends Android, Windows/macOS para sensores, APIs de hardware seguras,
   firmware/kernel y drivers reales.
4. Framework web de producción (TLS, autenticación, sesiones, middlewares) y
   datos estructurados nativos en la sintaxis Nyux.
5. Motor de juegos con renderer/audio/física y runtime nativo multiplataforma.
6. IA general/modelos entrenables y pruebas de compatibilidad por plataforma.

## Ejecutar los ejemplos 0.2

```sh
./nyux2 run examples/v0.2/sql-json.nyux
./nyux2 run examples/v0.2/concurrency.nyux
./nyux2 run examples/v0.2/prediction.nyux
./nyux2 run examples/v0.2/short-functions.nyux
./nyux2 run examples/v0.2/hardware.nyux
./nyux2 run examples/v0.2/game.nyux
./nyux2 run examples/v0.2/web-app.nyux
```

La app de juego y el servidor web son interactivos; para parar el servidor
usa Ctrl+C. La suite completa se ejecuta con
`python3 -m unittest discover -s tests -v`.
