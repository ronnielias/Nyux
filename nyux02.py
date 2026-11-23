#!/usr/bin/env python3
"""Nyux 0.2 hosted extensions; Nyux 0.1 remains in nyux.py unchanged."""

from __future__ import annotations

import concurrent.futures
import csv
import http.server
import json
import math
import mimetypes
import operator as py_operator
import platform
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

import nyux


TYPED_LET = re.compile(
    r"^(?P<indent>\s*)let\s+(?P<name>[A-Za-z_]\w*)\s*:\s*"
    r"(?P<type>Int|Float|Number|String|Bool)\s*=\s*(?P<value>.+?)\s*$"
)
SCALAR_TYPES = {"Int", "Float", "Number", "String", "Bool"}
SHORT_FUNCTION = re.compile(
    r"^(?P<indent>\s*)fun\s+(?P<name>[A-Za-z_]\w*)\s*"
    r"\((?P<parameters>[^()]*)\)\s*(?::\s*(?P<return_type>"
    r"Int|Float|Number|String|Bool)\s*)?=\s*(?P<value>.+?)\s*$"
)


def _split_line_comment(line: str) -> tuple[str, str]:
    in_string = False
    escaped = False
    for index, character in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif line.startswith("//", index):
            return line[:index].rstrip(), line[index:]
    return line, ""


def _lower_short_functions(source: str) -> str:
    lowered: list[str] = []
    for line_number, line in enumerate(source.splitlines(keepends=True), start=1):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        code, comment = _split_line_comment(body)
        match = SHORT_FUNCTION.fullmatch(code)
        if match is None:
            lowered.append(line)
            continue
        indent, name, raw_parameters, return_type, value = match.groups()
        parameters: list[str] = []
        checks: list[str] = []
        if raw_parameters.strip():
            for raw_parameter in raw_parameters.split(","):
                parameter = re.fullmatch(
                    r"\s*([A-Za-z_]\w*)\s*(?::\s*(Int|Float|Number|String|Bool))?\s*",
                    raw_parameter,
                )
                if parameter is None:
                    raise nyux.NyuxError(
                        f"parámetro inválido en la función corta {name}",
                        line_number,
                        suggestion="usa parámetros separados por comas, opcionalmente anotados con Int, Float, Number, String o Bool.",
                    )
                parameter_name, parameter_type = parameter.groups()
                parameters.append(parameter_name)
                if parameter_type:
                    checks.append(
                        f'{parameter_name} = Type::check("{parameter_type}", {parameter_name})'
                    )
        result = (
            f'Type::check("{return_type}", ({value}))'
            if return_type
            else value
        )
        checks.append(f"return {result}")
        body_text = "; ".join(checks)
        lowered.append(
            f"{indent}fn {name}({', '.join(parameters)}) {{ {body_text} }}{comment}{ending}"
        )
    return "".join(lowered)


def lower_typed_declarations(source: str) -> str:
    """Lower scalar type declarations into checked VM calls, preserving lines."""
    lowered: list[str] = []
    source = _lower_short_functions(source)
    for line in source.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        code, comment = _split_line_comment(body)
        match = TYPED_LET.fullmatch(code)
        if match is None:
            lowered.append(line)
            continue
        indent, name, type_name, value = match.group(
            "indent", "name", "type", "value"
        )
        lowered.append(
            f'{indent}let {name} = Type::check("{type_name}", ({value})){comment}{ending}'
        )
    return "".join(lowered)


@dataclass
class SqlRows:
    columns: list[str]
    rows: list[dict[str, object]]

    def to_json(self) -> str:
        return json.dumps(self.rows, ensure_ascii=False, separators=(",", ":"))

    @property
    def count(self) -> int:
        return len(self.rows)


class SqlDatabase:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.closed = False

    def execute(
        self, statement: str, parameters: tuple[object, ...]
    ) -> int:
        self._ensure_open()
        cursor = self.connection.execute(statement, parameters)
        try:
            return cursor.rowcount
        finally:
            cursor.close()

    def query(self, statement: str, parameters: tuple[object, ...]) -> SqlRows:
        self._ensure_open()
        cursor = self.connection.execute(statement, parameters)
        try:
            columns = [str(column[0]) for column in (cursor.description or [])]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            return SqlRows(columns, rows)
        finally:
            cursor.close()

    def commit(self) -> None:
        self._ensure_open()
        self.connection.commit()

    def close(self) -> None:
        if not self.closed:
            self.connection.close()
            self.closed = True

    def _ensure_open(self) -> None:
        if self.closed:
            raise ValueError("la base de datos ya está cerrada")


@dataclass
class NyuxTask:
    future: concurrent.futures.Future[object]


@dataclass(frozen=True)
class NyuxHttpResponse:
    status: int
    content_type: str
    body: bytes


@dataclass
class NyuxDataFrame:
    columns: list[str]
    rows: list[dict[str, str]]

    @property
    def count(self) -> int:
        return len(self.rows)

    def value(self, row_index: int, column: str) -> str:
        if not isinstance(row_index, int) or isinstance(row_index, bool):
            raise TypeError("el índice de fila debe ser entero")
        if row_index < 0 or row_index >= len(self.rows):
            raise IndexError("índice de fila fuera de rango")
        if column not in self.columns:
            raise ValueError(f"columna inexistente: {column}")
        return self.rows[row_index][column]

    def mean(self, column: str) -> float:
        values = [self._number(row[column], column) for row in self.rows]
        if not values:
            raise ValueError("no se puede calcular la media de un CSV sin filas")
        return sum(values) / len(values)

    def to_json(self) -> str:
        return json.dumps(self.rows, ensure_ascii=False, separators=(",", ":"))

    def numeric_pairs(self, x_column: str, y_column: str) -> tuple[list[float], list[float]]:
        if x_column not in self.columns or y_column not in self.columns:
            missing = x_column if x_column not in self.columns else y_column
            raise ValueError(f"columna inexistente: {missing}")
        pairs = [
            (
                self._number(row[x_column], x_column),
                self._number(row[y_column], y_column),
            )
            for row in self.rows
        ]
        if not pairs:
            raise ValueError("el CSV no contiene filas para entrenar")
        return [pair[0] for pair in pairs], [pair[1] for pair in pairs]

    @staticmethod
    def _number(value: str, column: str) -> float:
        try:
            number = float(value)
        except ValueError as error:
            raise ValueError(
                f"el valor {value!r} de {column!r} no es numérico"
            ) from error
        if not math.isfinite(number):
            raise ValueError(f"el valor {value!r} de {column!r} no es finito")
        return number


@dataclass(frozen=True)
class LinearModel:
    x_column: str
    y_column: str
    slope: float
    intercept: float
    r_squared: float

    def predict(self, value: int | float) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError("la predicción requiere un número")
        try:
            numeric_value = float(value)
        except OverflowError as error:
            raise ValueError("el valor de predicción excede el rango numérico") from error
        if not math.isfinite(numeric_value):
            raise ValueError("el valor de predicción debe ser finito")
        prediction = self.slope * numeric_value + self.intercept
        if not math.isfinite(prediction):
            raise ValueError("la predicción excede el rango numérico")
        return prediction

    def summary_json(self) -> str:
        return json.dumps(
            {
                "model": "linear_regression",
                "input": self.x_column,
                "target": self.y_column,
                "slope": self.slope,
                "intercept": self.intercept,
                "r_squared": self.r_squared,
            },
            separators=(",", ":"),
        )


@dataclass
class SensorReadings:
    readings: list[dict[str, str | float]]

    @property
    def count(self) -> int:
        return len(self.readings)

    def to_json(self) -> str:
        return json.dumps(self.readings, ensure_ascii=False, separators=(",", ":"))


@dataclass
class GameEntity:
    name: str
    x: float
    y: float


@dataclass
class GameWorld:
    title: str
    width: int
    height: int
    entities: dict[str, GameEntity]
    score: int = 0

    def state_json(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "width": self.width,
                "height": self.height,
                "score": self.score,
                "entities": [
                    {"name": item.name, "x": item.x, "y": item.y}
                    for item in self.entities.values()
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


class NyuxWebApp:
    MAX_REQUEST_BYTES = 1024 * 1024
    MAX_STATIC_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        port: int,
        root: Path,
        dispatch: Callable[[str, str, str], object],
    ) -> None:
        self.port = port
        self.root = root.resolve()
        self.dispatch = dispatch
        self.routes: dict[tuple[str, str], str] = {}
        self.server: http.server.ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        if not self.root.is_dir():
            raise ValueError(f"la carpeta web no existe: {self.root}")

    def route(self, method: str, path: str, function_name: str) -> None:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("WebApp solo admite rutas GET y POST")
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("la ruta web debe empezar con '/' y no incluir query ni fragmento")
        if not function_name:
            raise ValueError("la ruta necesita el nombre de una función Nyux")
        self.routes[(method, path)] = function_name

    def start(self) -> None:
        if self.server is not None:
            raise RuntimeError("el servidor web ya está iniciado")
        self.server = self._create_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"[Nyux 0.2 Web] http://127.0.0.1:{self.port}")

    def serve(self) -> None:
        if self.server is not None:
            raise RuntimeError("el servidor web ya está iniciado")
        self.server = self._create_server()
        print(f"[Nyux 0.2 Web] http://127.0.0.1:{self.port} (Ctrl+C para detener)")
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.server.server_close()
            self.server = None

    def stop(self) -> None:
        if self.server is None:
            return
        server = self.server
        server.shutdown()
        server.server_close()
        self.server = None
        if self.thread is not None:
            self.thread.join(timeout=3)
            self.thread = None

    def _create_server(self) -> http.server.ThreadingHTTPServer:
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._handle("GET")

            def do_POST(self) -> None:
                self._handle("POST")

            def _handle(self, method: str) -> None:
                parsed = urlsplit(self.path)
                route_name = owner.routes.get((method, parsed.path))
                if route_name is not None:
                    self._serve_dynamic(route_name, self.path, method)
                    return
                if method != "GET":
                    self._send(404, "text/plain; charset=utf-8", b"Ruta no encontrada")
                    return
                self._serve_static(unquote(parsed.path))

            def _serve_dynamic(self, name: str, path: str, method: str) -> None:
                body = ""
                if method == "POST":
                    raw_length = self.headers.get("Content-Length", "0")
                    try:
                        length = int(raw_length)
                    except ValueError:
                        self._send(400, "text/plain; charset=utf-8", b"Content-Length invalido")
                        return
                    if length < 0 or length > owner.MAX_REQUEST_BYTES:
                        self._send(413, "text/plain; charset=utf-8", b"Solicitud demasiado grande")
                        return
                    body = self.rfile.read(length).decode("utf-8", errors="replace")
                try:
                    result = owner.dispatch(name, path, body)
                    if isinstance(result, NyuxHttpResponse):
                        self._send(result.status, result.content_type, result.body)
                        return
                    if isinstance(result, (dict, list, bool, int, float)) or result is None:
                        payload = json.dumps(
                            result, ensure_ascii=False, allow_nan=False
                        ).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                    elif isinstance(result, str):
                        payload = result.encode("utf-8")
                        content_type = "text/html; charset=utf-8"
                    else:
                        raise TypeError("el handler web debe devolver texto o datos JSON")
                except (
                    nyux.NyuxError,
                    ArithmeticError,
                    OSError,
                    RuntimeError,
                    sqlite3.Error,
                    TypeError,
                    ValueError,
                ) as error:
                    payload = f"Error del handler Nyux: {error}".encode("utf-8")
                    self._send(500, "text/plain; charset=utf-8", payload)
                    return
                self._send(200, content_type, payload)

            def _serve_static(self, url_path: str) -> None:
                relative = url_path.lstrip("/") or "index.html"
                candidate = (owner.root / relative).resolve()
                try:
                    candidate.relative_to(owner.root)
                except ValueError:
                    self._send(403, "text/plain; charset=utf-8", b"Acceso denegado")
                    return
                if candidate.is_dir():
                    candidate = (candidate / "index.html").resolve()
                    try:
                        candidate.relative_to(owner.root)
                    except ValueError:
                        self._send(403, "text/plain; charset=utf-8", b"Acceso denegado")
                        return
                try:
                    size = candidate.stat().st_size
                    if size > owner.MAX_STATIC_BYTES:
                        self._send(413, "text/plain; charset=utf-8", b"Archivo demasiado grande")
                        return
                    payload = candidate.read_bytes()
                except FileNotFoundError:
                    self._send(404, "text/plain; charset=utf-8", b"Archivo no encontrado")
                    return
                except OSError as error:
                    self._send(500, "text/plain; charset=utf-8", str(error).encode("utf-8"))
                    return
                content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                if content_type.startswith("text/") or content_type in {
                    "application/javascript",
                    "application/json",
                }:
                    content_type += "; charset=utf-8"
                self._send(200, content_type, payload)

            def _send(self, status: int, content_type: str, payload: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                _ = format, args

        server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        server.daemon_threads = True
        self.port = int(server.server_address[1])
        return server


@dataclass(frozen=True)
class StaticMethodSpec:
    minimum: int
    maximum: int | None
    return_type: str | None = None
    argument_types: tuple[str | None, ...] = ()
    variadic_type: str | None = None


@dataclass(frozen=True)
class FunctionSignature:
    parameter_types: dict[str, str]
    return_type: str | None


class Nyux02Checker:
    STATIC_METHODS = {
        "AI": {
            "fit_linear": StaticMethodSpec(
                3, 3, "LinearModel", ("DataFrame", "String", "String")
            ),
            "run": StaticMethodSpec(0, 0),
        },
        "CPU": {"info": StaticMethodSpec(0, 0, "String")},
        "CSV": {
            "load": StaticMethodSpec(1, 1, "DataFrame", ("String",)),
            "load_csv": StaticMethodSpec(1, 1, "DataFrame", ("String",)),
        },
        "Data": {"load_csv": StaticMethodSpec(1, 1, "DataFrame", ("String",))},
        "Database": {"open": StaticMethodSpec(1, 1, "SqlDatabase", ("String",))},
        "Driver": {"load": StaticMethodSpec(0, 0)},
        "File": {
            "open": StaticMethodSpec(1, 2, "NyuxFile", ("String", "String"))
        },
        "Game": {
            "start": StaticMethodSpec(0, 1, "NyuxGame", ("String",)),
            "world": StaticMethodSpec(
                3, 3, "GameWorld", ("String", "Int", "Int")
            ),
        },
        "Hardware": {
            "read_text": StaticMethodSpec(1, 1, "String", ("String",))
        },
        "Json": {
            "parse": StaticMethodSpec(1, 1, "JsonValue", ("String",)),
            "stringify": StaticMethodSpec(1, 1, "String"),
            "valid": StaticMethodSpec(1, 1, "Bool", ("String",)),
        },
        "Memory": {"init": StaticMethodSpec(0, 1, "MemoryArena", ("Int",))},
        "Process": {
            "spawn": StaticMethodSpec(
                1, None, "NyuxProcess", variadic_type="String"
            )
        },
        "Screen": {"write": StaticMethodSpec(1, 1)},
        "Sensor": {"temperatures": StaticMethodSpec(0, 0, "SensorReadings")},
        "Sql": {"open": StaticMethodSpec(1, 1, "SqlDatabase", ("String",))},
        "System": {"boot": StaticMethodSpec(0, 0, "SystemState")},
        "Task": {
            "all": StaticMethodSpec(0, None, "List", variadic_type="NyuxTask"),
            "spawn": StaticMethodSpec(
                1, None, "NyuxTask", ("String",)
            ),
        },
        "Time": {"sleep": StaticMethodSpec(1, 1, argument_types=("Int",))},
        "Type": {"check": StaticMethodSpec(2, 2)},
        "Web": {
            "api": StaticMethodSpec(1, 1, argument_types=("String",)),
            "app": StaticMethodSpec(2, 2, "WebApp", ("Int", "String")),
            "html": StaticMethodSpec(1, 1, "HttpResponse", ("String",)),
            "json": StaticMethodSpec(1, 1, "HttpResponse"),
            "page": StaticMethodSpec(
                2, 2, argument_types=("String", "String")
            ),
            "server": StaticMethodSpec(1, 1, "NyuxWebServer", ("Int",)),
        },
        "Window": {
            "create": StaticMethodSpec(
                3, 3, "XanWindow", ("String", "Int", "Int")
            ),
            "new": StaticMethodSpec(
                3, 3, "XanWindow", ("String", "Int", "Int")
            ),
        },
        "XanOS": {"new": StaticMethodSpec(1, 1, "XanOS", ("String",))},
    }
    METHODS: dict[str, dict[str, tuple[int, int, str | None]]] = {
        "DataFrame": {
            "count": (0, 0, "Int"),
            "columns_json": (0, 0, "String"),
            "value": (2, 2, "String"),
            "mean": (1, 1, "Float"),
            "to_json": (0, 0, "String"),
        },
        "LinearModel": {
            "predict": (1, 1, "Float"),
            "summary_json": (0, 0, "String"),
        },
        "NyuxTask": {
            "await": (0, 1, None),
            "result": (0, 1, None),
            "done": (0, 0, "Bool"),
            "cancel": (0, 0, "Bool"),
        },
        "SqlDatabase": {
            "execute": (1, 1000, "Int"),
            "query": (1, 1000, "SqlRows"),
            "commit": (0, 0, None),
            "close": (0, 0, None),
        },
        "SqlRows": {
            "to_json": (0, 0, "String"),
            "count": (0, 0, "Int"),
        },
        "WebApp": {
            "route": (3, 3, None),
            "serve": (0, 0, None),
            "start": (0, 0, None),
            "stop": (0, 0, None),
            "port": (0, 0, "Int"),
        },
        "GameWorld": {
            "spawn": (3, 3, None),
            "move": (3, 3, None),
            "add_score": (0, 1, "Int"),
            "state_json": (0, 0, "String"),
            "run": (0, 0, None),
        },
        "SensorReadings": {
            "count": (0, 0, "Int"),
            "to_json": (0, 0, "String"),
        },
        "NyuxWebServer": {
            "page": (2, 2, None),
            "api": (1, 1, None),
            "serve": (0, 0, None),
            "start": (0, 0, None),
            "stop": (0, 0, None),
            "port": (0, 0, "Int"),
        },
        "NyuxGame": {"run": (0, 0, None)},
        "SystemState": {"name": (0, 0, "String")},
        "MemoryArena": {
            "read": (2, 2, "Bytes"),
            "read_text": (2, 2, "String"),
            "write": (2, 2, "Int"),
            "capacity": (0, 0, "Int"),
        },
        "XanWindow": {
            "show": (0, 0, None),
            "set_glass": (1, 1, None),
            "add_button": (1, 2, None),
        },
        "XanOS": {
            "set_wallpaper": (1, 1, None),
            "set_theme": (1, 1, None),
            "run": (1, 1, None),
        },
    }

    def __init__(self, functions: dict[str, nyux.BytecodeFunction]) -> None:
        self.functions = functions
        self.scopes: list[dict[str, str | None]] = []
        self.function_depth = 0
        self.function_signatures: dict[str, FunctionSignature] = {}
        self.current_return_type: str | None = None

    def check(self, program: nyux.Program) -> None:
        self.scopes = [{}]
        self.function_signatures = {
            statement.name: self._function_signature(statement)
            for statement in program.body
            if isinstance(statement, nyux.Fn)
        }
        for statement in program.body:
            if not isinstance(statement, (nyux.Fn, nyux.Use)):
                self._statement(statement)
        for statement in program.body:
            if not isinstance(statement, nyux.Fn):
                continue
            self.scopes.append({name: None for name in statement.parameters})
            self.function_depth += 1
            self.current_return_type = self.function_signatures[
                statement.name
            ].return_type
            try:
                self._block(statement.body)
            finally:
                self.current_return_type = None
                self.function_depth -= 1
                self.scopes.pop()

    @staticmethod
    def _checked_type(expression: nyux.Expression) -> str | None:
        if (
            not isinstance(expression, nyux.CallStatic)
            or not isinstance(expression.namespace, nyux.Var)
            or expression.namespace.name != "Type"
            or expression.method != "check"
            or len(expression.arguments) != 2
            or not isinstance(expression.arguments[0], nyux.Str)
            or expression.arguments[0].value not in SCALAR_TYPES
        ):
            return None
        return str(expression.arguments[0].value)

    @classmethod
    def _function_signature(cls, function: nyux.Fn) -> FunctionSignature:
        parameter_types: dict[str, str] = {}
        return_types: set[str] = set()
        for statement in function.body:
            if (
                isinstance(statement, nyux.Assign)
                and statement.name in function.parameters
                and isinstance(statement.value, nyux.CallStatic)
                and len(statement.value.arguments) == 2
                and isinstance(statement.value.arguments[1], nyux.Var)
                and statement.value.arguments[1].name == statement.name
            ):
                parameter_type = cls._checked_type(statement.value)
                if parameter_type is not None:
                    parameter_types[statement.name] = parameter_type
            if isinstance(statement, nyux.Return) and statement.value is not None:
                return_type = cls._checked_type(statement.value)
                if return_type is not None:
                    return_types.add(return_type)
        if len(return_types) > 1:
            raise nyux.NyuxError(
                f"la función {function.name} declara tipos de retorno incompatibles",
                function.line,
            )
        return FunctionSignature(
            parameter_types,
            next(iter(return_types)) if return_types else None,
        )

    def _block(self, statements: list[nyux.Statement]) -> None:
        self.scopes.append({})
        try:
            for statement in statements:
                self._statement(statement)
        finally:
            self.scopes.pop()

    def _statement(self, statement: nyux.Statement) -> None:
        if isinstance(statement, nyux.Let):
            scope = self.scopes[-1]
            if statement.name in scope:
                raise nyux.NyuxError(
                    f"la variable {statement.name!r} ya está declarada en este bloque",
                    statement.line,
                )
            scope[statement.name] = self._expression(statement.value)
        elif isinstance(statement, nyux.Assign):
            value_type = self._expression(statement.value)
            for scope in reversed(self.scopes):
                if statement.name in scope:
                    expected = scope[statement.name]
                    self._require_compatible(expected, value_type, statement.line)
                    if expected is None:
                        scope[statement.name] = value_type
                    return
            raise nyux.NyuxError(
                f"variable no definida: {statement.name}; declara primero con 'let'",
                statement.line,
            )
        elif isinstance(statement, nyux.Print):
            for value in statement.values:
                self._expression(value)
        elif isinstance(statement, nyux.If):
            self._expression(statement.condition)
            self._block(statement.then_body)
            self._block(statement.else_body)
        elif isinstance(statement, nyux.For):
            start_type = self._expression(statement.start)
            stop_type = self._expression(statement.stop)
            if start_type not in {None, "Int"} or stop_type not in {None, "Int"}:
                raise nyux.NyuxError("los límites de 'for' deben ser enteros", statement.line)
            self.scopes.append({statement.name: "Int"})
            self._block(statement.body)
            self.scopes.pop()
        elif isinstance(statement, nyux.While):
            self._expression(statement.condition)
            self._block(statement.body)
        elif isinstance(statement, nyux.Return):
            if statement.value is None:
                if self.current_return_type is not None:
                    raise nyux.NyuxError(
                        f"la función debe devolver {self.current_return_type}",
                        statement.line,
                    )
                return
            actual_type = self._expression(statement.value)
            self._require_compatible(
                self.current_return_type, actual_type, statement.line
            )
        elif isinstance(statement, nyux.ExpressionStatement):
            self._expression(statement.value)

    def _expression(self, expression: nyux.Expression) -> str | None:
        if isinstance(expression, nyux.Num):
            return "Int" if isinstance(expression.value, int) else "Float"
        if isinstance(expression, nyux.Str | nyux.EnumValue):
            return "String"
        if isinstance(expression, nyux.Bool):
            return "Bool"
        if isinstance(expression, nyux.Var):
            for scope in reversed(self.scopes):
                if expression.name in scope:
                    return scope[expression.name]
            raise nyux.NyuxError(
                f"variable no definida: {expression.name}",
                expression.line,
            )
        if isinstance(expression, nyux.UnaryOp):
            value_type = self._expression(expression.value)
            if expression.operator == "-" and value_type not in {None, "Int", "Float", "Number"}:
                raise nyux.NyuxError("el operador '-' requiere un número", expression.line)
            return "Bool" if expression.operator in {"!", "not"} else value_type
        if isinstance(expression, nyux.BinOp):
            left = self._expression(expression.left)
            right = self._expression(expression.right)
            operator = expression.operator
            if operator in {"and", "or"}:
                return "Bool"
            if operator in {"+", "-", "*", "/", "%"}:
                if operator == "+" and (left == "String" or right == "String"):
                    if left == right == "String":
                        return "String"
                    if left is None or right is None:
                        return None
                    raise nyux.NyuxError(
                        "el operador '+' requiere dos números o dos cadenas",
                        expression.line,
                    )
                if left not in {None, "Int", "Float", "Number"} or right not in {
                    None,
                    "Int",
                    "Float",
                    "Number",
                }:
                    raise nyux.NyuxError(
                        f"el operador {operator!r} requiere números compatibles",
                        expression.line,
                    )
                if left is None or right is None:
                    return None
                return "Float" if "Float" in {left, right} or operator == "/" else "Int"
            if left is not None and right is not None:
                if left != right and not ({left, right} <= {"Int", "Float", "Number"}):
                    raise nyux.NyuxError(
                        "la comparación requiere valores de tipos compatibles",
                        expression.line,
                    )
            return "Bool"
        if isinstance(expression, nyux.CallStatic):
            if not isinstance(expression.namespace, nyux.Var):
                raise nyux.NyuxError("el constructor requiere un nombre de tipo", expression.line)
            namespace = expression.namespace.name
            namespace_methods = self.STATIC_METHODS.get(namespace)
            if namespace_methods is None:
                raise nyux.NyuxError(
                    f"tipo o módulo desconocido: {namespace}",
                    expression.line,
                )
            signature = namespace_methods.get(expression.method)
            if signature is None:
                raise nyux.NyuxError(
                    f"método estático no definido: {namespace}::{expression.method}",
                    expression.line,
                )
            argument_count = len(expression.arguments)
            if argument_count < signature.minimum or (
                signature.maximum is not None
                and argument_count > signature.maximum
            ):
                if signature.maximum == signature.minimum:
                    expected_count = str(signature.minimum)
                else:
                    maximum = (
                        str(signature.maximum)
                        if signature.maximum is not None
                        else "cualquier cantidad"
                    )
                    expected_count = f"entre {signature.minimum} y {maximum}"
                noun = "argumento" if expected_count == "1" else "argumentos"
                raise nyux.NyuxError(
                    f"{namespace}::{expression.method} espera {expected_count} "
                    f"{noun}; recibió {argument_count}",
                    expression.line,
                )
            argument_types = [
                self._expression(argument) for argument in expression.arguments
            ]
            for index, expected in enumerate(signature.argument_types):
                if index < argument_count:
                    self._require_compatible(
                        expected, argument_types[index], expression.line
                    )
            if signature.variadic_type is not None:
                first_variadic = len(signature.argument_types)
                for actual in argument_types[first_variadic:]:
                    self._require_compatible(
                        signature.variadic_type, actual, expression.line
                    )
            if namespace == "Type" and expression.method == "check":
                if (
                    not isinstance(expression.arguments[0], nyux.Str)
                    or expression.arguments[0].value not in SCALAR_TYPES
                ):
                    raise nyux.NyuxError("anotación de tipo escalar inválida", expression.line)
                expected = str(expression.arguments[0].value)
                self._require_compatible(expected, argument_types[1], expression.line)
                return expected
            if namespace == "Task" and expression.method == "spawn":
                if not expression.arguments or not isinstance(expression.arguments[0], nyux.Str):
                    raise nyux.NyuxError(
                        "Task::spawn necesita el nombre literal de una función",
                        expression.line,
                    )
                function = self.functions.get(str(expression.arguments[0].value))
                if function is None:
                    raise nyux.NyuxError(
                        f"función concurrente desconocida: {expression.arguments[0].value}",
                        expression.line,
                    )
                if len(expression.arguments) - 1 != len(function.parameters):
                    raise nyux.NyuxError(
                        f"{function.name} espera {len(function.parameters)} argumentos "
                        f"y recibió {len(expression.arguments) - 1}",
                        expression.line,
                    )
                function_signature = self.function_signatures.get(function.name)
                if function_signature is not None:
                    for parameter, actual_type in zip(
                        function.parameters, argument_types[1:]
                    ):
                        self._require_compatible(
                            function_signature.parameter_types.get(parameter),
                            actual_type,
                            expression.line,
                        )
                return "NyuxTask"
            return signature.return_type
        if isinstance(expression, nyux.CallMethod):
            receiver_type = self._expression(expression.receiver)
            argument_types = [
                self._expression(argument) for argument in expression.arguments
            ]
            methods = self.METHODS.get(receiver_type or "")
            if methods is None:
                return None
            signature = methods.get(expression.method)
            if signature is None:
                raise nyux.NyuxError(
                    f"método no definido para {receiver_type}: {expression.method}",
                    expression.line,
                )
            minimum, maximum, result_type = signature
            if not minimum <= len(expression.arguments) <= maximum:
                raise nyux.NyuxError(
                    f"{receiver_type}.{expression.method} espera entre "
                    f"{minimum} y {maximum} argumentos; recibió {len(expression.arguments)}",
                    expression.line,
                )
            if receiver_type == "WebApp" and expression.method == "route":
                http_method, path, handler_name = expression.arguments
                if (
                    not isinstance(http_method, nyux.Str)
                    or http_method.value not in {"GET", "POST"}
                    or not isinstance(path, nyux.Str)
                    or not isinstance(handler_name, nyux.Str)
                ):
                    raise nyux.NyuxError(
                        'route requiere ("GET"|"POST", ruta_texto, "nombre_handler")',
                        expression.line,
                    )
                handler = self.functions.get(handler_name.value)
                if handler is None:
                    raise nyux.NyuxError(
                        f"handler Nyux no definido: {handler_name.value}",
                        expression.line,
                    )
                if len(handler.parameters) != 2:
                    raise nyux.NyuxError(
                        f"el handler {handler_name.value} debe recibir (path, body)",
                        expression.line,
                    )
                if not path.value.startswith("/"):
                    raise nyux.NyuxError(
                        "la ruta web debe empezar con '/'",
                        expression.line,
                    )
            return result_type
        if isinstance(expression, nyux.Call):
            if not isinstance(expression.callee, nyux.Var):
                raise nyux.NyuxError("solo se pueden llamar funciones por nombre", expression.line)
            function = self.functions.get(expression.callee.name)
            if function is None:
                raise nyux.NyuxError(
                    f"función desconocida: {expression.callee.name}",
                    expression.line,
                )
            if len(expression.arguments) != len(function.parameters):
                raise nyux.NyuxError(
                    f"{function.name} espera {len(function.parameters)} argumentos "
                    f"y recibió {len(expression.arguments)}",
                    expression.line,
                )
            argument_types = [
                self._expression(argument) for argument in expression.arguments
            ]
            signature = self.function_signatures.get(function.name)
            if signature is None:
                return None
            for parameter, actual_type in zip(
                function.parameters, argument_types
            ):
                self._require_compatible(
                    signature.parameter_types.get(parameter),
                    actual_type,
                    expression.line,
                )
            return signature.return_type
        return None

    @staticmethod
    def _require_compatible(
        expected: str | None, actual: str | None, line: int
    ) -> None:
        if expected is None or actual is None:
            return
        compatible = expected == actual or (
            expected == "Number" and actual in {"Int", "Float"}
        )
        if not compatible:
            raise nyux.NyuxError(
                f"se esperaba {expected}, se recibió {actual}",
                line,
                suggestion="corrige el tipo del valor antes de ejecutar el programa.",
            )


class Nyux02Evaluator(nyux.Evaluator):
    MAX_TASKS = 16
    MAX_PENDING_TASKS = 1024
    DEFAULT_TASK_TIMEOUT_MS = 30_000

    def __init__(self, module_directory: Path) -> None:
        super().__init__(module_directory)
        self.databases: list[SqlDatabase] = []
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.MAX_TASKS, thread_name_prefix="nyux-task"
        )
        self.task_slots = threading.BoundedSemaphore(self.MAX_PENDING_TASKS)
        self.vm: Nyux02VM | None = None
        self.web_apps: list[NyuxWebApp] = []

    def close_extensions(self) -> None:
        for web_app in self.web_apps:
            web_app.stop()
        self.executor.shutdown(wait=True, cancel_futures=False)
        for database in self.databases:
            if not database.closed:
                database.close()

    def run(self, program: nyux.Program) -> None:
        try:
            vm = Nyux02VM(self)
            bytecode = vm.compile(program)
            vm.execute_program(bytecode)
        finally:
            self.close_extensions()

    def call_static_values(
        self, type_name: str, method: str, arguments: list[object], line: int
    ) -> object:
        if type_name == "Type" and method == "check" and len(arguments) == 2:
            expected, value = arguments
            if not isinstance(expected, str) or not self._matches_type(expected, value):
                actual = self._type_name(value)
                raise nyux.NyuxError(
                    f"se esperaba {expected}, se recibió {actual}",
                    line,
                    suggestion="ajusta el valor al tipo declarado o corrige la anotación.",
                )
            return value

        if type_name == "Json":
            if method == "parse" and len(arguments) == 1 and isinstance(arguments[0], str):
                try:
                    return json.loads(arguments[0])
                except json.JSONDecodeError as error:
                    raise nyux.NyuxError(
                        f"JSON inválido: {error.msg} (columna {error.colno})",
                        line,
                        suggestion="corrige la sintaxis JSON y comprueba comillas, comas y llaves.",
                    ) from error
            if method == "stringify" and len(arguments) == 1:
                try:
                    return json.dumps(
                        arguments[0],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                except (TypeError, ValueError) as error:
                    raise nyux.NyuxError(f"no se pudo serializar JSON: {error}", line) from error
            if method == "valid" and len(arguments) == 1 and isinstance(arguments[0], str):
                try:
                    json.loads(arguments[0])
                    return True
                except json.JSONDecodeError:
                    return False
            raise nyux.NyuxError(
                "usa Json::parse(texto), Json::stringify(valor) o Json::valid(texto)",
                line,
            )

        if type_name in {"Sql", "Database"} and method == "open" and len(arguments) == 1:
            path = arguments[0]
            if not isinstance(path, str) or not path:
                raise nyux.NyuxError("Sql::open requiere una ruta no vacía", line)
            target = Path(path).expanduser()
            if not target.is_absolute() and path != ":memory:":
                target = self.module_directory / target
            try:
                connection = sqlite3.connect(str(target), check_same_thread=False)
                connection.row_factory = sqlite3.Row
            except sqlite3.Error as error:
                raise nyux.NyuxError(f"no se pudo abrir SQLite: {error}", line) from error
            database = SqlDatabase(connection)
            self.databases.append(database)
            return database

        if type_name == "Task" and method == "spawn":
            if not arguments or not isinstance(arguments[0], str):
                raise nyux.NyuxError(
                    'Task::spawn requiere el nombre de una función, por ejemplo Task::spawn("work", 3)',
                    line,
                )
            if self.vm is None:
                raise nyux.NyuxError("el runtime de tareas aún no está inicializado", line)
            function = self.vm.functions.get(arguments[0])
            if function is None:
                raise nyux.NyuxError(f"función concurrente desconocida: {arguments[0]}", line)
            values = arguments[1:]
            if len(values) != len(function.parameters):
                raise nyux.NyuxError(
                    f"{function.name} espera {len(function.parameters)} argumentos "
                    f"y recibió {len(values)}",
                    line,
                )
            if not self.task_slots.acquire(blocking=False):
                raise nyux.NyuxError(
                    "se alcanzó el límite de 1024 tareas pendientes",
                    line,
                    suggestion="espera o recoge resultados con Task::all(...) antes de enviar más tareas.",
                )
            try:
                future = self.executor.submit(self._run_task, function, values)
            except RuntimeError as error:
                self.task_slots.release()
                raise nyux.NyuxError(f"no se pudo iniciar la tarea: {error}", line) from error
            future.add_done_callback(lambda _: self.task_slots.release())
            return NyuxTask(future)

        if type_name == "Task" and method == "all":
            tasks = arguments
            if not all(isinstance(task, NyuxTask) for task in tasks):
                raise nyux.NyuxError("Task::all solo acepta handles de Task", line)
            return [self._await_task(task, line) for task in tasks]

        if type_name in {"Data", "CSV"} and method == "load_csv" or (
            type_name == "CSV" and method == "load"
        ):
            if len(arguments) != 1 or not isinstance(arguments[0], str):
                raise nyux.NyuxError("CSV::load(path) requiere una ruta de texto", line)
            return self._load_csv(arguments[0], line)

        if type_name == "AI" and method == "fit_linear" and len(arguments) == 3:
            frame, x_column, y_column = arguments
            if (
                not isinstance(frame, NyuxDataFrame)
                or not isinstance(x_column, str)
                or not isinstance(y_column, str)
            ):
                raise nyux.NyuxError(
                    "AI::fit_linear requiere (DataFrame, columna_entrada, columna_objetivo)",
                    line,
                )
            try:
                x_values, y_values = frame.numeric_pairs(x_column, y_column)
                return self._fit_linear(x_values, y_values, x_column, y_column)
            except ValueError as error:
                raise nyux.NyuxError(f"no se pudo entrenar el modelo: {error}", line) from error

        if type_name == "Web" and method == "app" and len(arguments) == 2:
            port, root = arguments
            if (
                not isinstance(port, int)
                or isinstance(port, bool)
                or not 0 <= port <= 65535
                or not isinstance(root, str)
            ):
                raise nyux.NyuxError("Web::app requiere (puerto, carpeta_web)", line)
            path = Path(root).expanduser()
            if not path.is_absolute():
                path = self.module_directory / path
            app = NyuxWebApp(port, path, self._dispatch_web)
            self.web_apps.append(app)
            return app

        if type_name == "Web" and method == "html" and len(arguments) == 1:
            if not isinstance(arguments[0], str):
                raise nyux.NyuxError("Web::html requiere texto", line)
            return NyuxHttpResponse(
                200,
                "text/html; charset=utf-8",
                arguments[0].encode("utf-8"),
            )

        if type_name == "Web" and method == "json" and len(arguments) == 1:
            try:
                body = json.dumps(
                    arguments[0], ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise nyux.NyuxError(f"Web::json no acepta estos datos: {error}", line) from error
            return NyuxHttpResponse(200, "application/json; charset=utf-8", body)

        if type_name == "Hardware" and method == "read_text" and len(arguments) == 1:
            if not isinstance(arguments[0], str):
                raise nyux.NyuxError("Hardware::read_text requiere una ruta de texto", line)
            return self._read_host_system_file(arguments[0], line)

        if type_name == "Sensor" and method == "temperatures" and not arguments:
            return self._read_temperatures(line)

        if type_name == "Game" and method == "world" and len(arguments) == 3:
            title, width, height = arguments
            if (
                not isinstance(title, str)
                or not isinstance(width, int)
                or isinstance(width, bool)
                or not isinstance(height, int)
                or isinstance(height, bool)
                or width < 160
                or height < 120
            ):
                raise nyux.NyuxError(
                    "Game::world requiere (título, ancho >= 160, alto >= 120)",
                    line,
                )
            return GameWorld(title, width, height, {})

        if type_name == "Time" and method == "sleep" and len(arguments) == 1:
            milliseconds = arguments[0]
            if (
                not isinstance(milliseconds, int)
                or isinstance(milliseconds, bool)
                or not 0 <= milliseconds <= 60_000
            ):
                raise nyux.NyuxError(
                    "Time::sleep requiere milisegundos entre 0 y 60000",
                    line,
                )
            time.sleep(milliseconds / 1000)
            return None

        return super().call_static_values(type_name, method, arguments, line)

    def call_method_values(
        self, receiver: object, method: str, arguments: list[object], line: int
    ) -> object:
        if isinstance(receiver, SqlDatabase):
            try:
                if method in {"execute", "query"} and arguments and isinstance(arguments[0], str):
                    statement = arguments[0]
                    parameters = tuple(arguments[1:])
                    if method == "execute":
                        return receiver.execute(statement, parameters)
                    return receiver.query(statement, parameters)
                if method == "commit" and not arguments:
                    receiver.commit()
                    return None
                if method == "close" and not arguments:
                    receiver.close()
                    return None
            except (sqlite3.Error, ValueError) as error:
                raise nyux.NyuxError(f"falló SQLite: {error}", line) from error
            raise nyux.NyuxError(
                "método SQL inválido: usa execute/query(sql, parámetros...), commit() o close()",
                line,
            )

        if isinstance(receiver, SqlRows):
            if method == "to_json" and not arguments:
                return receiver.to_json()
            if method == "count" and not arguments:
                return receiver.count

        if isinstance(receiver, NyuxTask):
            if method in {"await", "result"} and len(arguments) <= 1:
                timeout_ms = (
                    arguments[0]
                    if arguments
                    else self.DEFAULT_TASK_TIMEOUT_MS
                )
                if (
                    not isinstance(timeout_ms, int)
                    or isinstance(timeout_ms, bool)
                    or timeout_ms < 0
                ):
                    raise nyux.NyuxError(
                        "el timeout de una tarea debe ser entero no negativo",
                        line,
                    )
                return self._await_task(receiver, line, timeout_ms)
            if method == "done" and not arguments:
                return receiver.future.done()
            if method == "cancel" and not arguments:
                return receiver.future.cancel()

        if isinstance(receiver, NyuxDataFrame):
            try:
                if method == "count" and not arguments:
                    return receiver.count
                if method == "columns_json" and not arguments:
                    return json.dumps(receiver.columns, ensure_ascii=False)
                if method == "to_json" and not arguments:
                    return receiver.to_json()
                if method == "value" and len(arguments) == 2:
                    return receiver.value(arguments[0], arguments[1])  # type: ignore[arg-type]
                if method == "mean" and len(arguments) == 1 and isinstance(arguments[0], str):
                    return receiver.mean(arguments[0])
            except (IndexError, TypeError, ValueError) as error:
                raise nyux.NyuxError(f"operación de datos inválida: {error}", line) from error

        if isinstance(receiver, LinearModel):
            try:
                if method == "predict" and len(arguments) == 1:
                    return receiver.predict(arguments[0])  # type: ignore[arg-type]
                if method == "summary_json" and not arguments:
                    return receiver.summary_json()
            except (TypeError, ValueError) as error:
                raise nyux.NyuxError(f"predicción inválida: {error}", line) from error

        if isinstance(receiver, NyuxWebApp):
            try:
                if method == "route" and len(arguments) == 3 and all(
                    isinstance(item, str) for item in arguments
                ):
                    http_method, path, function_name = arguments
                    if self.vm is None:
                        raise ValueError("el runtime web no está inicializado")
                    function = self.vm.functions.get(function_name)
                    if function is None:
                        raise ValueError(f"handler Nyux no definido: {function_name}")
                    if len(function.parameters) != 2:
                        raise ValueError(
                            f"el handler {function_name} debe recibir (path, body)"
                        )
                    receiver.route(http_method, path, function_name)
                    return None
                if method == "serve" and not arguments:
                    receiver.serve()
                    return None
                if method == "start" and not arguments:
                    receiver.start()
                    return None
                if method == "stop" and not arguments:
                    receiver.stop()
                    return None
                if method == "port" and not arguments:
                    return receiver.port
            except (RuntimeError, ValueError) as error:
                raise nyux.NyuxError(f"configuración web inválida: {error}", line) from error

        if isinstance(receiver, SensorReadings):
            if method == "count" and not arguments:
                return receiver.count
            if method == "to_json" and not arguments:
                return receiver.to_json()

        if isinstance(receiver, GameWorld):
            try:
                if method == "spawn" and len(arguments) == 3:
                    name, x, y = arguments
                    if not isinstance(name, str) or not name:
                        raise ValueError("el nombre de entidad no puede estar vacío")
                    if name in receiver.entities:
                        raise ValueError(f"entidad duplicada: {name}")
                    receiver.entities[name] = self._game_entity(name, x, y, receiver)
                    return None
                if method == "move" and len(arguments) == 3:
                    name, x, y = arguments
                    if not isinstance(name, str) or name not in receiver.entities:
                        raise ValueError(f"entidad inexistente: {name}")
                    entity = self._game_entity(name, x, y, receiver)
                    receiver.entities[name].x = entity.x
                    receiver.entities[name].y = entity.y
                    return None
                if method == "add_score" and (not arguments or len(arguments) == 1):
                    points = arguments[0] if arguments else 1
                    if not isinstance(points, int) or isinstance(points, bool):
                        raise ValueError("los puntos deben ser enteros")
                    receiver.score += points
                    return receiver.score
                if method == "state_json" and not arguments:
                    return receiver.state_json()
                if method == "run" and not arguments:
                    self._run_game_world(receiver, line)
                    return None
            except ValueError as error:
                raise nyux.NyuxError(f"operación de juego inválida: {error}", line) from error

        return super().call_method_values(receiver, method, arguments, line)

    @staticmethod
    def _await_task(task: NyuxTask, line: int, timeout_ms: int = 30_000) -> object:
        try:
            return task.future.result(timeout=timeout_ms / 1000)
        except concurrent.futures.TimeoutError as error:
            raise nyux.NyuxError(
                f"la tarea no terminó en {timeout_ms} ms; sigue ejecutándose",
                line,
                suggestion="espera más tiempo con tarea.await(milisegundos) o revisa el trabajo enviado.",
            ) from error
        except nyux.NyuxError as error:
            raise nyux.NyuxError(
                f"falló la tarea: {error}", line, suggestion=error.suggestion
            ) from error
        except (
            ArithmeticError,
            concurrent.futures.CancelledError,
            KeyError,
            OSError,
            RuntimeError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as error:
            raise nyux.NyuxError(f"falló la tarea: {error}", line) from error

    def _load_csv(self, filename: str, line: int) -> NyuxDataFrame:
        path = Path(filename).expanduser()
        if not path.is_absolute():
            path = self.module_directory / path
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("el CSV supera el límite de 32 MiB")
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if (
                    not reader.fieldnames
                    or any(not column.strip() for column in reader.fieldnames)
                    or len(set(reader.fieldnames)) != len(reader.fieldnames)
                ):
                    raise ValueError("el CSV necesita una cabecera no vacía y sin nombres repetidos")
                columns = list(reader.fieldnames)
                rows: list[dict[str, str]] = []
                for row in reader:
                    if len(rows) >= 100_000:
                        raise ValueError("el CSV supera el límite de 100000 filas")
                    if None in row:
                        raise ValueError("una fila tiene más valores que columnas")
                    rows.append({key: value or "" for key, value in row.items()})
            return NyuxDataFrame(columns, rows)
        except (OSError, UnicodeError, csv.Error, ValueError) as error:
            raise nyux.NyuxError(f"no se pudo cargar CSV {path}: {error}", line) from error

    @staticmethod
    def _fit_linear(
        x_values: list[float],
        y_values: list[float],
        x_column: str,
        y_column: str,
    ) -> LinearModel:
        if len(x_values) != len(y_values) or not x_values:
            raise ValueError("se requieren pares de datos numéricos")
        x_mean = sum(x_values) / len(x_values)
        y_mean = sum(y_values) / len(y_values)
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        if denominator == 0:
            raise ValueError("la columna de entrada no tiene variación")
        slope = sum(
            (x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values)
        ) / denominator
        intercept = y_mean - slope * x_mean
        residual = sum(
            (y - (slope * x + intercept)) ** 2
            for x, y in zip(x_values, y_values)
        )
        total = sum((y - y_mean) ** 2 for y in y_values)
        r_squared = 1.0 if total == 0 and residual == 0 else (
            0.0 if total == 0 else 1 - residual / total
        )
        if not all(math.isfinite(value) for value in (slope, intercept, r_squared)):
            raise ValueError("los valores numéricos exceden el rango soportado")
        return LinearModel(x_column, y_column, slope, intercept, r_squared)

    @staticmethod
    def _read_host_system_file(filename: str, line: int) -> str:
        if platform.system() != "Linux":
            raise nyux.NyuxError(
                "Hardware::read_text solo está disponible en Linux; "
                "TODO: añadir backends de Windows y macOS",
                line,
            )
        try:
            candidate = Path(filename).resolve(strict=True)
        except OSError as error:
            raise nyux.NyuxError(
                f"no se pudo resolver el archivo de sistema {filename}: {error}",
                line,
            ) from error
        allowed_roots = (Path("/proc").resolve(), Path("/sys").resolve())
        if not any(candidate == root or root in candidate.parents for root in allowed_roots):
            raise nyux.NyuxError(
                "solo se permite lectura dentro de /proc y /sys",
                line,
                suggestion="usa una ruta de información del host, como /proc/cpuinfo o /sys/class/thermal/thermal_zone0/temp.",
            )
        if not candidate.is_file():
            raise nyux.NyuxError(f"no es un archivo del sistema: {candidate}", line)
        try:
            with candidate.open("rb") as handle:
                contents = handle.read(1024 * 1024 + 1)
            if len(contents) > 1024 * 1024:
                raise nyux.NyuxError("el archivo del sistema supera el límite de 1 MiB", line)
            return contents.decode("utf-8", errors="replace")
        except OSError as error:
            raise nyux.NyuxError(f"no se pudo leer {candidate}: {error}", line) from error

    def _read_temperatures(self, line: int) -> SensorReadings:
        if platform.system() != "Linux":
            raise nyux.NyuxError(
                "Sensor::temperatures solo está implementado para Linux; "
                "TODO: integrar APIs de sensores de otras plataformas",
                line,
            )
        readings: list[dict[str, str | float]] = []
        for sensor_file in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
            try:
                label_path = sensor_file.parent / "type"
                label = label_path.read_text(encoding="utf-8", errors="replace").strip()
                raw_value = sensor_file.read_text(encoding="ascii").strip()
                raw_temperature = int(raw_value)
                temperature = raw_temperature / 1000 if abs(raw_temperature) >= 1000 else float(raw_temperature)
                readings.append({"name": label or sensor_file.parent.name, "celsius": temperature})
            except (OSError, UnicodeError, ValueError) as error:
                raise nyux.NyuxError(
                    f"no se pudo leer el sensor {sensor_file}: {error}",
                    line,
                ) from error
        return SensorReadings(readings)

    @staticmethod
    def _game_entity(
        name: str, x: object, y: object, world: GameWorld
    ) -> GameEntity:
        if (
            not isinstance(x, (int, float))
            or isinstance(x, bool)
            or not isinstance(y, (int, float))
            or isinstance(y, bool)
            or not math.isfinite(x)
            or not math.isfinite(y)
        ):
            raise ValueError("las coordenadas deben ser números finitos")
        if not (0 <= x <= world.width and 0 <= y <= world.height):
            raise ValueError("las coordenadas deben estar dentro del mundo")
        return GameEntity(name, float(x), float(y))

    @staticmethod
    def _run_game_world(world: GameWorld, line: int) -> None:
        try:
            import tkinter as tk
        except ImportError as error:
            raise nyux.NyuxError(
                "Tkinter no está instalado; no se puede abrir el juego", line
            ) from error
        try:
            root = tk.Tk()
            root.title(world.title)
            root.resizable(False, False)
            canvas = tk.Canvas(
                root,
                width=world.width,
                height=world.height,
                background="#101827",
                highlightthickness=0,
            )
            canvas.pack()
            score_label = tk.Label(root, text=f"Puntos: {world.score}")
            score_label.pack()
            def redraw() -> None:
                canvas.delete("entity")
                for entity in world.entities.values():
                    canvas.create_oval(
                        entity.x - 12,
                        entity.y - 12,
                        entity.x + 12,
                        entity.y + 12,
                        fill="#36a3ff",
                        outline="",
                        tags=("entity", entity.name),
                    )

            def click(event: object) -> None:
                x = float(event.x)  # type: ignore[attr-defined]
                y = float(event.y)  # type: ignore[attr-defined]
                for entity in world.entities.values():
                    if abs(entity.x - x) <= 16 and abs(entity.y - y) <= 16:
                        world.score += 1
                        score_label.configure(text=f"Puntos: {world.score}")
                        break

            def move_player(event: object) -> None:
                entity = world.entities.get("player")
                if entity is None:
                    return
                deltas = {"Left": (-10, 0), "Right": (10, 0), "Up": (0, -10), "Down": (0, 10)}
                dx, dy = deltas.get(event.keysym, (0, 0))  # type: ignore[attr-defined]
                entity.x = min(max(entity.x + dx, 12), world.width - 12)
                entity.y = min(max(entity.y + dy, 12), world.height - 12)
                redraw()

            redraw()
            canvas.bind("<Button-1>", click)
            root.bind("<Key>", move_player)
            root.mainloop()
        except tk.TclError as error:
            raise nyux.NyuxError(
                f"no se pudo inicializar la ventana del juego: {error}",
                line,
                suggestion="asegúrate de tener una sesión gráfica activa.",
            ) from error
        except OSError as error:
            raise nyux.NyuxError(f"no se pudo abrir la ventana del juego: {error}", line) from error

    def _dispatch_web(self, function_name: str, path: str, body: str) -> object:
        if self.vm is None:
            raise RuntimeError("VM web no inicializada")
        function = self.vm.functions.get(function_name)
        if function is None:
            raise nyux.NyuxError(f"handler Nyux desconocido: {function_name}", 1)
        child_runtime = Nyux02Evaluator(self.module_directory)
        child_vm = Nyux02VM(child_runtime)
        child_vm.functions = self.vm.functions
        try:
            return child_vm._call_function(function, [path, body], function.line)
        finally:
            child_runtime.close_extensions()

    def _run_task(
        self, function: nyux.BytecodeFunction, arguments: list[object]
    ) -> object:
        child_runtime = Nyux02Evaluator(self.module_directory)
        child_vm = Nyux02VM(child_runtime)
        child_vm.functions = self.vm.functions if self.vm is not None else {}
        try:
            return child_vm._call_function(function, arguments, function.line)
        finally:
            child_runtime.close_extensions()

    @staticmethod
    def _type_name(value: object) -> str:
        if isinstance(value, bool):
            return "Bool"
        if isinstance(value, int):
            return "Int"
        if isinstance(value, float):
            return "Float"
        if isinstance(value, str):
            return "String"
        return type(value).__name__

    @staticmethod
    def _matches_type(expected: str, value: object) -> bool:
        if expected == "Int":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "Float":
            return isinstance(value, float)
        if expected == "Number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "String":
            return isinstance(value, str)
        if expected == "Bool":
            return isinstance(value, bool)
        return False


class Nyux02VM(nyux.BytecodeVM):
    def __init__(self, runtime: Nyux02Evaluator) -> None:
        super().__init__(runtime)
        runtime.vm = self

    def compile(self, program: nyux.Program) -> nyux.BytecodeProgram:
        bytecode = super().compile(program)
        Nyux02Checker(self.functions).check(program)
        bytecode.entry.instructions = self._optimize(bytecode.entry.instructions)
        for function in bytecode.functions.values():
            function.instructions = self._optimize(function.instructions)
        self.functions = bytecode.functions
        return bytecode

    @staticmethod
    def _optimize(
        instructions: list[nyux.BytecodeInstruction],
    ) -> list[nyux.BytecodeInstruction]:
        binary_operators: dict[str, Callable[[object, object], object]] = {
            "+": py_operator.add,
            "-": py_operator.sub,
            "*": py_operator.mul,
            "/": py_operator.truediv,
            "%": py_operator.mod,
            "==": py_operator.eq,
            "!=": py_operator.ne,
            "<": py_operator.lt,
            "<=": py_operator.le,
            ">": py_operator.gt,
            ">=": py_operator.ge,
        }
        optimized: list[nyux.BytecodeInstruction] = []
        for instruction in instructions:
            if (
                instruction.opcode == "BINARY"
                and len(optimized) >= 2
                and optimized[-1].opcode == "CONST"
                and optimized[-2].opcode == "CONST"
                and isinstance(instruction.operand, str)
                and instruction.operand in binary_operators
            ):
                left, right = optimized[-2].operand, optimized[-1].operand
                try:
                    value = binary_operators[instruction.operand](left, right)
                except (ArithmeticError, TypeError, ValueError):
                    optimized.append(instruction)
                    continue
                optimized[-2:] = [
                    nyux.BytecodeInstruction("CONST", value, instruction.line)
                ]
                continue
            if (
                instruction.opcode == "UNARY"
                and optimized
                and optimized[-1].opcode == "CONST"
            ):
                value = optimized[-1].operand
                if instruction.operand in {"!", "not"}:
                    optimized[-1] = nyux.BytecodeInstruction(
                        "CONST", not value, instruction.line
                    )
                    continue
                if instruction.operand == "-" and isinstance(value, (int, float)):
                    optimized[-1] = nyux.BytecodeInstruction(
                        "CONST", -value, instruction.line
                    )
                    continue
            optimized.append(instruction)
        return optimized

def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print("Uso: ./nyux2 run <archivo.nyux>", file=sys.stderr)
        return 2
    source_path = Path(arguments[0])
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        print(
            f"Error: no se pudo leer {source_path}: {error.strerror or error}",
            file=sys.stderr,
        )
        return 1

    evaluator = Nyux02Evaluator(Path(__file__).resolve().parent)
    try:
        tokens = nyux.lex(lower_typed_declarations(source))
        program = nyux.Parser(tokens).parse()
        vm = Nyux02VM(evaluator)
        bytecode = vm.compile(program)
        print(f"[Nyux 0.2] {bytecode.instruction_count} instrucciones de bytecode")
        vm.execute_program(bytecode)
        print("[Nyux 0.2] Ejecución finalizada.")
        return 0
    except nyux.NyuxError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        try:
            evaluator.close_extensions()
        except sqlite3.Error as error:
            print(f"Error al cerrar recursos 0.2: {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
