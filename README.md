# Nyux 0.1

## Cómo usarlo en 30 segundos

**Instala:** Python 3.10 o superior y Git. Nyux no necesita paquetes de Python.

**Clona el repositorio y entra en la carpeta:**

```sh
git clone https://github.com/ronnielias/Nyux.git
cd Nyux
```

**Corre un ejemplo:**

```sh
./nyux run examples/mi-primer-nyux.nyux
```

**Tu primer archivo `.nyux` tiene 3 líneas.** Guárdalo, por ejemplo, como
`examples/mi-primer-nyux.nyux`:

```nyux
fn main() {
    print("Hola, Nyux!")
}
```

Para correr cualquier archivo, siempre usa el mismo formato:
`./nyux run ruta/al/archivo.nyux`.

## Ejemplos incluidos

Cada ejemplo se ejecuta con el mismo comando oficial:

```sh
./nyux run examples/app.nyux
./nyux run examples/web.nyux
./nyux run examples/system.nyux
```

- `app.nyux` abre una ventana Login real de 320×340 con un botón. Ciérrala para
  terminar.
- `web.nyux` sirve una página en `http://127.0.0.1:8080/` y JSON fijo de demo
  en `/users`; termina con Ctrl+C.
- `system.nyux` escribe, lee y verifica datos en una arena de memoria del
  proceso y muestra información real del sistema anfitrión. No es un kernel.

El lanzador `./nyux` funciona en Linux/macOS y en Windows desde Git Bash.
Para la app se necesita una sesión gráfica. Consulta [HONEST.md](./HONEST.md)
para los límites actuales y [SPEC.md](./SPEC.md) para la sintaxis.

Nyux compila sus archivos a bytecode y lo ejecuta en una VM alojada en Python;
no genera código máquina nativo.

## Nyux 0.2 (extensiones en desarrollo)

La 0.1 publicada y su comando `./nyux run ...` se conservan. Las extensiones
experimentales 0.2 usan una entrada separada:

```sh
./nyux2 run examples/v0.2/sql-json.nyux
./nyux2 run examples/v0.2/concurrency.nyux
```

Incluyen comprobaciones de tipos escalares en ejecución, SQLite, JSON y tareas
concurrentes alojadas en Python. No son un compilador nativo ni acceso a
hardware. Detalles y límites: [NYUX_0.2.md](./NYUX_0.2.md).
