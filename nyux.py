#!/usr/bin/env python3
"""Prototipo alojado de Nyux: lexer, parser, AST y APIs de sistema acotadas."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import platform
import subprocess
import sys
import threading
from dataclasses import dataclass, field as dataclass_field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, IO, TypeAlias
from urllib.parse import quote, urlsplit


@dataclass(frozen=True)
class Token:
    kind: str
    value: str | int | float
    line: int
    column: int


class NyuxError(Exception):
    def __init__(
        self, message: str, line: int, column: int = 1, suggestion: str | None = None
    ) -> None:
        if suggestion is None:
            suggestion = self._suggest_fix(message)
        super().__init__(
            f"Error en línea {line}, columna {column}: {message}\n"
            f"Sugerencia: {suggestion}"
        )
        self.line = line
        self.column = column
        self.suggestion = suggestion

    @staticmethod
    def _suggest_fix(message: str) -> str:
        if "variable no definida" in message:
            return "declárala antes con 'let' o corrige el nombre de la variable."
        if "'return' solo se permite dentro de una función" in message:
            return "mueve 'return' dentro del cuerpo de una función declarada con 'fn'."
        if "se esperaba" in message:
            return "añade el elemento esperado y revisa paréntesis, comas y llaves."
        if "TODO:" in message:
            return "usa una API implementada; esta operación requiere completar el TODO indicado."
        if "interfaz gráfica no disponible" in message:
            return "inicia una sesión gráfica compatible y vuelve a ejecutar el ejemplo."
        if "argumentos" in message or "requiere" in message or "desconocido" in message:
            return "comprueba el nombre de la API, sus argumentos y los tipos esperados."
        if "bucle" in message:
            return "revisa la condición y actualiza dentro del bucle la variable que la controla."
        if "operador" in message or "operación" in message or "evaluar" in message:
            return "comprueba que los operandos tengan tipos compatibles y que no haya división por cero."
        if "no se pudo" in message or "falló" in message:
            return "comprueba la ruta, los permisos y la disponibilidad del recurso indicado."
        return "corrige la instrucción indicada y vuelve a ejecutar el archivo."


KEYWORDS = {
    "and",
    "else",
    "false",
    "fn",
    "for",
    "if",
    "let",
    "not",
    "or",
    "print",
    "return",
    "true",
    "use",
    "while",
}
TWO_CHAR_OPERATORS = {"::", "==", "!=", ">=", "<="}
ONE_CHAR_OPERATORS = set("+-*/%(){}.,;=<>!")


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1

    def advance(text: str) -> None:
        nonlocal line, column
        newlines = text.count("\n")
        if newlines:
            line += newlines
            column = len(text.rsplit("\n", 1)[-1]) + 1
        else:
            column += len(text)

    while index < len(source):
        char = source[index]
        if char.isspace():
            advance(char)
            index += 1
            continue
        if source.startswith("//", index):
            end = source.find("\n", index)
            if end == -1:
                advance(source[index:])
                break
            advance(source[index:end])
            index = end
            continue

        start_line, start_column = line, column
        if char == '"':
            index += 1
            advance('"')
            value: list[str] = []
            while index < len(source) and source[index] != '"':
                current = source[index]
                if current == "\n":
                    raise NyuxError("cadena sin cerrar", start_line, start_column)
                if current == "\\":
                    if index + 1 >= len(source):
                        raise NyuxError("escape incompleto en cadena", line, column)
                    escaped = source[index + 1]
                    replacements = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
                    if escaped not in replacements:
                        raise NyuxError(f"escape no reconocido: \\{escaped}", line, column)
                    value.append(replacements[escaped])
                    advance(source[index : index + 2])
                    index += 2
                else:
                    value.append(current)
                    advance(current)
                    index += 1
            if index >= len(source):
                raise NyuxError("cadena sin cerrar", start_line, start_column)
            advance('"')
            index += 1
            tokens.append(Token("STRING", "".join(value), start_line, start_column))
            continue

        if char.isdigit():
            end = index
            while end < len(source) and source[end].isdigit():
                end += 1
            if end < len(source) and source[end] == "." and end + 1 < len(source) and source[end + 1].isdigit():
                end += 1
                while end < len(source) and source[end].isdigit():
                    end += 1
            text = source[index:end]
            value: int | float = float(text) if "." in text else int(text)
            tokens.append(Token("NUMBER", value, start_line, start_column))
            advance(text)
            index = end
            continue

        if char.isalpha() or char == "_":
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] == "_"):
                end += 1
            word = source[index:end]
            kind = "KEYWORD" if word in KEYWORDS else "IDENT"
            tokens.append(Token(kind, word, start_line, start_column))
            advance(word)
            index = end
            continue

        operator = source[index : index + 2]
        if operator in TWO_CHAR_OPERATORS:
            tokens.append(Token("OP", operator, start_line, start_column))
            advance(operator)
            index += 2
            continue
        if char in ONE_CHAR_OPERATORS:
            tokens.append(Token("OP", char, start_line, start_column))
            advance(char)
            index += 1
            continue
        raise NyuxError(f"carácter no válido: {char!r}", start_line, start_column)

    tokens.append(Token("EOF", "", line, column))
    return tokens


class Node:
    line: int


@dataclass
class Program(Node):
    body: list[Statement]
    line: int


@dataclass
class Let(Node):
    name: str
    value: Expression
    line: int


@dataclass
class Assign(Node):
    name: str
    value: Expression
    line: int


@dataclass
class Fn(Node):
    name: str
    parameters: list[str]
    body: list[Statement]
    line: int


@dataclass
class If(Node):
    condition: Expression
    then_body: list[Statement]
    else_body: list[Statement]
    line: int


@dataclass
class For(Node):
    name: str
    start: Expression
    stop: Expression
    body: list[Statement]
    line: int


@dataclass
class While(Node):
    condition: Expression
    body: list[Statement]
    line: int


@dataclass
class Print(Node):
    values: list[Expression]
    line: int


@dataclass
class Return(Node):
    value: Expression | None
    line: int


@dataclass
class ExpressionStatement(Node):
    value: Expression
    line: int


@dataclass
class Use(Node):
    module: str
    line: int


@dataclass
class BinOp(Node):
    operator: str
    left: Expression
    right: Expression
    line: int


@dataclass
class UnaryOp(Node):
    operator: str
    value: Expression
    line: int


@dataclass
class Var(Node):
    name: str
    line: int


@dataclass
class Num(Node):
    value: int | float
    line: int


@dataclass
class Str(Node):
    value: str
    line: int


@dataclass
class Bool(Node):
    value: bool
    line: int


@dataclass
class EnumValue(Node):
    name: str
    line: int


@dataclass
class CallStatic(Node):
    namespace: Expression
    method: str
    arguments: list[Expression]
    line: int


@dataclass
class CallMethod(Node):
    receiver: Expression
    method: str
    arguments: list[Expression]
    line: int


@dataclass
class Call(Node):
    callee: Expression
    arguments: list[Expression]
    line: int


Expression: TypeAlias = (
    BinOp | UnaryOp | Var | Num | Str | Bool | EnumValue | CallStatic | CallMethod | Call
)
Statement: TypeAlias = (
    Let | Assign | Fn | If | For | While | Print | Return | ExpressionStatement | Use
)


class Parser:
    PRECEDENCE = {
        "or": 1,
        "and": 2,
        "==": 3,
        "!=": 3,
        "<": 3,
        "<=": 3,
        ">": 3,
        ">=": 3,
        "+": 4,
        "-": 4,
        "*": 5,
        "/": 5,
        "%": 5,
    }

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.position = 0
        self.function_depth = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.position]

    def advance(self) -> Token:
        token = self.current
        if token.kind != "EOF":
            self.position += 1
        return token

    def match(self, value: str) -> bool:
        if self.current.value == value:
            self.advance()
            return True
        return False

    def peek_ahead(self, distance: int = 1) -> Token:
        return self.tokens[min(self.position + distance, len(self.tokens) - 1)]

    def expect(self, value: str) -> Token:
        token = self.current
        if token.value != value:
            raise NyuxError(f"se esperaba {value!r} y se encontró {token.value!r}", token.line, token.column)
        return self.advance()

    def expect_identifier(self) -> Token:
        token = self.current
        if token.kind != "IDENT":
            raise NyuxError("se esperaba un identificador", token.line, token.column)
        return self.advance()

    def parse(self) -> Program:
        body: list[Statement] = []
        while self.current.kind != "EOF":
            body.append(self.parse_statement())
        return Program(body, 1)

    def parse_statement(self) -> Statement:
        token = self.current
        if self.match("let"):
            name = self.expect_identifier()
            self.expect("=")
            value = self.parse_expression()
            self.match(";")
            return Let(str(name.value), value, token.line)
        if self.current.kind == "IDENT" and self.peek_ahead().value == "=":
            name = self.advance()
            self.advance()
            value = self.parse_expression()
            self.match(";")
            return Assign(str(name.value), value, token.line)
        if self.match("fn"):
            if self.function_depth:
                raise NyuxError("no se permiten funciones anidadas", token.line, token.column)
            name = self.expect_identifier()
            self.expect("(")
            parameters: list[str] = []
            if self.current.value != ")":
                parameters.append(str(self.expect_identifier().value))
                while self.match(","):
                    parameter_token = self.expect_identifier()
                    parameter = str(parameter_token.value)
                    if parameter in parameters:
                        raise NyuxError(
                            f"parámetro duplicado: {parameter}",
                            parameter_token.line,
                            parameter_token.column,
                        )
                    parameters.append(parameter)
            self.expect(")")
            self.function_depth += 1
            try:
                body = self.parse_block()
            finally:
                self.function_depth -= 1
            return Fn(str(name.value), parameters, body, token.line)
        if self.match("if"):
            self.expect("(")
            condition = self.parse_expression()
            self.expect(")")
            then_body = self.parse_block()
            else_body = self.parse_block() if self.match("else") else []
            return If(condition, then_body, else_body, token.line)
        if self.match("for"):
            self.expect("(")
            name = self.expect_identifier()
            self.expect("=")
            start = self.parse_expression()
            self.expect(",")
            stop = self.parse_expression()
            self.expect(")")
            return For(str(name.value), start, stop, self.parse_block(), token.line)
        if self.match("while"):
            self.expect("(")
            condition = self.parse_expression()
            self.expect(")")
            return While(condition, self.parse_block(), token.line)
        if self.match("print"):
            return Print(self.parse_arguments(), token.line)
        if self.match("return"):
            if not self.function_depth:
                raise NyuxError("'return' solo se permite dentro de una función", token.line, token.column)
            value = None if self.current.value in (";", "}") or self.current.kind == "EOF" else self.parse_expression()
            self.match(";")
            return Return(value, token.line)
        if self.match("use"):
            if self.function_depth:
                raise NyuxError("'use' solo se permite al nivel superior", token.line, token.column)
            module = self.expect_identifier()
            self.match(";")
            return Use(str(module.value), token.line)
        if self.current.kind == "EOF" or self.current.value == "}":
            raise NyuxError("se esperaba una instrucción", self.current.line, self.current.column)
        expression = self.parse_expression()
        self.match(";")
        return ExpressionStatement(expression, token.line)

    def parse_block(self) -> list[Statement]:
        self.expect("{")
        body: list[Statement] = []
        while self.current.value != "}":
            if self.current.kind == "EOF":
                raise NyuxError("bloque sin cerrar; falta '}'", self.current.line, self.current.column)
            body.append(self.parse_statement())
        self.advance()
        return body

    def parse_arguments(self) -> list[Expression]:
        self.expect("(")
        arguments: list[Expression] = []
        if self.current.value != ")":
            arguments.append(self.parse_expression())
            while self.match(","):
                arguments.append(self.parse_expression())
        self.expect(")")
        return arguments

    def parse_expression(self, minimum_precedence: int = 0) -> Expression:
        left = self.parse_prefix()
        while True:
            if self.current.value == ".":
                dot = self.advance()
                method = self.expect_identifier()
                left = CallMethod(left, str(method.value), self.parse_arguments(), dot.line)
                continue
            if self.current.value == "::":
                separator = self.advance()
                method = self.expect_identifier()
                left = CallStatic(left, str(method.value), self.parse_arguments(), separator.line)
                continue
            if self.current.value == "(":
                left = Call(left, self.parse_arguments(), left.line)
                continue

            operator = str(self.current.value)
            precedence = self.PRECEDENCE.get(operator, -1)
            if precedence < minimum_precedence:
                break
            operator_token = self.advance()
            right = self.parse_expression(precedence + 1)
            left = BinOp(operator, left, right, operator_token.line)
        return left

    def parse_prefix(self) -> Expression:
        token = self.advance()
        if token.kind == "NUMBER":
            return Num(token.value, token.line)  # type: ignore[arg-type]
        if token.kind == "STRING":
            return Str(str(token.value), token.line)
        if token.value == "true":
            return Bool(True, token.line)
        if token.value == "false":
            return Bool(False, token.line)
        if token.kind == "IDENT":
            return Var(str(token.value), token.line)
        if token.value == ".":
            return EnumValue(str(self.expect_identifier().value), token.line)
        if token.value in ("!", "not", "-"):
            return UnaryOp(str(token.value), self.parse_expression(4), token.line)
        if token.value == "(":
            expression = self.parse_expression()
            self.expect(")")
            return expression
        raise NyuxError(f"no se esperaba {token.value!r} en una expresión", token.line, token.column)


@dataclass(frozen=True)
class BytecodeInstruction:
    opcode: str
    operand: object
    line: int


@dataclass
class BytecodeFunction:
    name: str
    parameters: list[str]
    instructions: list[BytecodeInstruction]
    line: int


@dataclass
class BytecodeProgram:
    entry: BytecodeFunction
    functions: dict[str, BytecodeFunction]

    @property
    def instruction_count(self) -> int:
        return len(self.entry.instructions) + sum(
            len(function.instructions) for function in self.functions.values()
        )

    def disassemble(self) -> str:
        sections = [self._format_function(self.entry)]
        sections.extend(self._format_function(function) for function in self.functions.values())
        return "\n".join(sections)

    @staticmethod
    def _format_function(function: BytecodeFunction) -> str:
        lines = [f"{function.name}:"]
        for index, instruction in enumerate(function.instructions):
            lines.append(
                f"  {index:04d}  {instruction.opcode} {instruction.operand!r}  "
                f"; línea {instruction.line}"
            )
        return "\n".join(lines)


class BytecodeCompiler:
    def compile(
        self, program: Program, functions: dict[str, Fn]
    ) -> BytecodeProgram:
        compiled_functions = {
            name: self._compile_function(function)
            for name, function in functions.items()
        }
        entry = BytecodeFunction("<programa>", [], [], program.line)
        for statement in program.body:
            if not isinstance(statement, (Fn, Use)):
                self._compile_statement(statement, entry.instructions)
        entry.instructions.append(BytecodeInstruction("HALT", None, program.line))
        return BytecodeProgram(entry, compiled_functions)

    def _compile_function(self, function: Fn) -> BytecodeFunction:
        result = BytecodeFunction(function.name, function.parameters, [], function.line)
        self._emit(result.instructions, "ENTER_SCOPE", None, function.line)
        for statement in function.body:
            self._compile_statement(statement, result.instructions)
        self._emit(result.instructions, "EXIT_SCOPE", None, function.line)
        result.instructions.append(BytecodeInstruction("CONST", None, function.line))
        result.instructions.append(BytecodeInstruction("RETURN", None, function.line))
        return result

    @staticmethod
    def _emit(
        instructions: list[BytecodeInstruction],
        opcode: str,
        operand: object,
        line: int,
    ) -> int:
        instructions.append(BytecodeInstruction(opcode, operand, line))
        return len(instructions) - 1

    @staticmethod
    def _patch(instructions: list[BytecodeInstruction], index: int, target: object) -> None:
        instruction = instructions[index]
        instructions[index] = BytecodeInstruction(
            instruction.opcode, target, instruction.line
        )

    def _compile_block(
        self, statements: list[Statement], instructions: list[BytecodeInstruction]
    ) -> None:
        self._emit(instructions, "ENTER_SCOPE", None, statements[0].line if statements else 1)
        for statement in statements:
            self._compile_statement(statement, instructions)
        self._emit(instructions, "EXIT_SCOPE", None, statements[-1].line if statements else 1)

    def _compile_statement(
        self, statement: Statement, instructions: list[BytecodeInstruction]
    ) -> None:
        if isinstance(statement, Let):
            self._compile_expression(statement.value, instructions)
            self._emit(instructions, "DECLARE", statement.name, statement.line)
        elif isinstance(statement, Assign):
            self._compile_expression(statement.value, instructions)
            self._emit(instructions, "ASSIGN", statement.name, statement.line)
        elif isinstance(statement, Print):
            for value in statement.values:
                self._compile_expression(value, instructions)
            self._emit(instructions, "PRINT", len(statement.values), statement.line)
        elif isinstance(statement, If):
            self._compile_expression(statement.condition, instructions)
            false_jump = self._emit(instructions, "JUMP_IF_FALSE", None, statement.line)
            self._compile_block(statement.then_body, instructions)
            if statement.else_body:
                end_jump = self._emit(instructions, "JUMP", None, statement.line)
                self._patch(instructions, false_jump, len(instructions))
                self._compile_block(statement.else_body, instructions)
                self._patch(instructions, end_jump, len(instructions))
            else:
                self._patch(instructions, false_jump, len(instructions))
        elif isinstance(statement, For):
            self._emit(instructions, "ENTER_SCOPE", None, statement.line)
            self._compile_expression(statement.start, instructions)
            self._compile_expression(statement.stop, instructions)
            self._emit(instructions, "MAKE_RANGE", None, statement.line)
            loop_start = len(instructions)
            next_instruction = self._emit(
                instructions, "FOR_NEXT", (statement.name, None), statement.line
            )
            self._compile_block(statement.body, instructions)
            self._emit(instructions, "JUMP", loop_start, statement.line)
            self._patch(instructions, next_instruction, (statement.name, len(instructions)))
            self._emit(instructions, "POP", None, statement.line)
            self._emit(instructions, "EXIT_SCOPE", None, statement.line)
        elif isinstance(statement, While):
            loop_start = len(instructions)
            self._compile_expression(statement.condition, instructions)
            end_jump = self._emit(instructions, "JUMP_IF_FALSE", None, statement.line)
            self._emit(instructions, "CHECK_LOOP", None, statement.line)
            self._compile_block(statement.body, instructions)
            self._emit(instructions, "JUMP", loop_start, statement.line)
            self._patch(instructions, end_jump, len(instructions))
        elif isinstance(statement, Return):
            if statement.value is None:
                self._emit(instructions, "CONST", None, statement.line)
            else:
                self._compile_expression(statement.value, instructions)
            self._emit(instructions, "RETURN", None, statement.line)
        elif isinstance(statement, ExpressionStatement):
            self._compile_expression(statement.value, instructions)
            self._emit(instructions, "POP", None, statement.line)
        elif isinstance(statement, (Fn, Use)):
            return
        else:
            raise NyuxError("instrucción no compatible con bytecode", statement.line)

    def _compile_expression(
        self, expression: Expression, instructions: list[BytecodeInstruction]
    ) -> None:
        if isinstance(expression, Num | Str | Bool):
            self._emit(instructions, "CONST", expression.value, expression.line)
        elif isinstance(expression, EnumValue):
            self._emit(instructions, "CONST", expression.name, expression.line)
        elif isinstance(expression, Var):
            self._emit(instructions, "LOAD", expression.name, expression.line)
        elif isinstance(expression, UnaryOp):
            self._compile_expression(expression.value, instructions)
            self._emit(instructions, "UNARY", expression.operator, expression.line)
        elif isinstance(expression, BinOp):
            if expression.operator in {"and", "or"}:
                self._compile_expression(expression.left, instructions)
                self._emit(instructions, "TO_BOOL", None, expression.line)
                self._emit(instructions, "DUP", None, expression.line)
                jump_opcode = "JUMP_IF_FALSE" if expression.operator == "and" else "JUMP_IF_TRUE"
                short_circuit = self._emit(instructions, jump_opcode, None, expression.line)
                self._emit(instructions, "POP", None, expression.line)
                self._compile_expression(expression.right, instructions)
                self._emit(instructions, "TO_BOOL", None, expression.line)
                self._patch(instructions, short_circuit, len(instructions))
            else:
                self._compile_expression(expression.left, instructions)
                self._compile_expression(expression.right, instructions)
                self._emit(instructions, "BINARY", expression.operator, expression.line)
        elif isinstance(expression, CallStatic):
            if not isinstance(expression.namespace, Var):
                raise NyuxError("el constructor requiere un nombre de tipo", expression.line)
            for argument in expression.arguments:
                self._compile_expression(argument, instructions)
            self._emit(
                instructions,
                "CALL_STATIC",
                (expression.namespace.name, expression.method, len(expression.arguments)),
                expression.line,
            )
        elif isinstance(expression, CallMethod):
            self._compile_expression(expression.receiver, instructions)
            for argument in expression.arguments:
                self._compile_expression(argument, instructions)
            self._emit(
                instructions,
                "CALL_METHOD",
                (expression.method, len(expression.arguments)),
                expression.line,
            )
        elif isinstance(expression, Call):
            if not isinstance(expression.callee, Var):
                raise NyuxError("solo se pueden llamar funciones por nombre", expression.line)
            for argument in expression.arguments:
                self._compile_expression(argument, instructions)
            self._emit(
                instructions,
                "CALL",
                (expression.callee.name, len(expression.arguments)),
                expression.line,
            )
        else:
            raise NyuxError("expresión no compatible con bytecode", expression.line)


@dataclass
class XanOS:
    name: str
    wallpaper: str | None = None
    running_window: XanWindow | None = None


@dataclass
class XanWindow:
    title: str
    width: int
    height: int
    glass: bool = False
    buttons: list[tuple[str, str]] = dataclass_field(default_factory=list)


@dataclass
class SystemState:
    os_name: str


@dataclass
class MemoryArena:
    storage: bytearray

    @property
    def capacity(self) -> int:
        return len(self.storage)

    def read(self, offset: int, length: int) -> bytes:
        self._check_range(offset, length)
        return bytes(self.storage[offset : offset + length])

    def read_text(self, offset: int, length: int) -> str:
        try:
            return self.read(offset, length).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("el rango de memoria no contiene texto UTF-8 válido") from error

    def write(self, offset: int, value: str) -> int:
        if not isinstance(value, str):
            raise TypeError("MemoryArena.write requiere texto")
        encoded = value.encode("utf-8")
        self._check_range(offset, len(encoded))
        self.storage[offset : offset + len(encoded)] = encoded
        return len(encoded)

    def _check_range(self, offset: int, length: int) -> None:
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not isinstance(length, int)
            or isinstance(length, bool)
        ):
            raise TypeError("los desplazamientos de memoria deben ser enteros")
        if offset < 0 or length < 0 or offset > self.capacity - length:
            raise ValueError(
                f"rango de memoria inválido: offset={offset}, longitud={length}, "
                f"capacidad={self.capacity}"
            )


@dataclass
class NyuxFile:
    handle: IO[Any]
    line: int
    closed: bool = False

    def read(self, length: int | None = None) -> str | bytes:
        if self.closed:
            raise ValueError("el archivo ya está cerrado")
        if length is not None and (
            not isinstance(length, int) or isinstance(length, bool) or length < 0
        ):
            raise ValueError("la longitud de lectura debe ser un entero no negativo")
        return self.handle.read(length) if length is not None else self.handle.read()

    def write(self, value: str) -> int:
        if self.closed:
            raise ValueError("el archivo ya está cerrado")
        if not isinstance(value, str):
            raise TypeError("File.write requiere texto")
        return self.handle.write(value)

    def close(self) -> None:
        if not self.closed:
            self.handle.close()
            self.closed = True


@dataclass
class NyuxProcess:
    process: subprocess.Popen[bytes]

    def running(self) -> bool:
        return self.process.poll() is None

    def wait(self) -> int:
        return self.process.wait()

    def terminate(self) -> None:
        if self.running():
            self.process.terminate()


class NyuxWebServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self.pages: dict[str, str] = {}
        self.apis: set[str] = set()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def page(self, route: str, content: str) -> None:
        if not route.startswith("/") or not isinstance(content, str):
            raise ValueError("una página necesita una ruta absoluta y contenido de texto")
        self.pages[route] = content

    def api(self, route: str) -> None:
        if not route.startswith("/"):
            raise ValueError("la ruta API debe comenzar con '/'")
        self.apis.add(route)

    def _create_server(self) -> ThreadingHTTPServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._respond()

            def do_POST(self) -> None:
                self._respond()

            def _respond(self) -> None:
                route = urlsplit(self.path).path
                if route in owner.pages:
                    status = 200
                    content_type = "text/html; charset=utf-8"
                    payload = owner.pages[route].encode("utf-8")
                elif route in owner.apis:
                    status = 200
                    content_type = "application/json; charset=utf-8"
                    payload = json.dumps(
                        {"ok": True, "route": route}, ensure_ascii=False
                    ).encode("utf-8")
                else:
                    status = 404
                    content_type = "text/plain; charset=utf-8"
                    payload = b"Nyux: ruta no encontrada"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                _ = format, args

        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        server.daemon_threads = True
        self.port = int(server.server_address[1])
        return server

    def start(self) -> None:
        if self.server is not None:
            raise RuntimeError("el servidor ya está iniciado")
        self.server = self._create_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"[Web] http://127.0.0.1:{self.port}")

    def serve(self) -> None:
        if self.server is not None:
            raise RuntimeError("el servidor ya está iniciado")
        self.server = self._create_server()
        print(f"[Web] http://127.0.0.1:{self.port} (Ctrl+C para detener)")
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
            self.thread.join(timeout=2)
            self.thread = None


@dataclass
class NyuxGame:
    title: str = "Nyux Game"


class TkBackend:
    def set_wallpaper(self, filename: str) -> None:
        path = Path(filename).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"no existe el archivo de fondo: {path}")
        if not self._set_gnome_wallpaper(path):
            raise RuntimeError(
                "el cambio de fondo solo está implementado para GNOME; "
                "TODO: integrar las APIs nativas de otros escritorios Linux"
            )

    @staticmethod
    def _set_gnome_wallpaper(path: Path) -> bool:
        library_name = ctypes.util.find_library("gio-2.0")
        if library_name is None:
            return False
        gio = ctypes.CDLL(library_name)
        gio.g_settings_schema_source_get_default.restype = ctypes.c_void_p
        gio.g_settings_schema_source_lookup.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        gio.g_settings_schema_source_lookup.restype = ctypes.c_void_p
        gio.g_settings_schema_has_key.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        gio.g_settings_schema_has_key.restype = ctypes.c_int
        gio.g_settings_new_full.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        gio.g_settings_new_full.restype = ctypes.c_void_p
        gio.g_settings_set_string.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        gio.g_settings_set_string.restype = ctypes.c_int
        gio.g_settings_apply.argtypes = [ctypes.c_void_p]
        gio.g_settings_schema_unref.argtypes = [ctypes.c_void_p]
        gio.g_settings_schema_unref.restype = None
        gobject_name = ctypes.util.find_library("gobject-2.0")
        if gobject_name is None:
            return False
        gobject = ctypes.CDLL(gobject_name)
        gobject.g_object_unref.argtypes = [ctypes.c_void_p]
        gobject.g_object_unref.restype = None

        schema_source = gio.g_settings_schema_source_get_default()
        if not schema_source:
            return False
        schema = gio.g_settings_schema_source_lookup(
            schema_source, b"org.gnome.desktop.background", 1
        )
        if not schema:
            return False
        if not gio.g_settings_schema_has_key(schema, b"picture-uri"):
            gio.g_settings_schema_unref(schema)
            return False
        settings = gio.g_settings_new_full(schema, None, None)
        gio.g_settings_schema_unref(schema)
        if not settings:
            return False
        try:
            uri = f"file://{quote(str(path))}".encode("utf-8")
            if not gio.g_settings_set_string(settings, b"picture-uri", uri):
                raise OSError("GNOME rechazó la ruta de fondo")
            gio.g_settings_apply(settings)
            return True
        finally:
            gobject.g_object_unref(settings)

    @staticmethod
    def _request_kwin_blur(window: object) -> bool:
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        if os.environ.get("KDE_FULL_SESSION") != "true" and "KDE" not in desktop:
            return False
        library_name = ctypes.util.find_library("X11")
        if library_name is None:
            return False
        x11 = ctypes.CDLL(library_name)
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XChangeProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
        ]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        display = x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            window_id = int(window.wm_frame(), 16)  # type: ignore[attr-defined]
            property_atom = x11.XInternAtom(
                display, b"_KDE_NET_WM_BLUR_BEHIND_REGION", 0
            )
            cardinal_atom = x11.XInternAtom(display, b"CARDINAL", 0)
            width = int(window.winfo_width())  # type: ignore[attr-defined]
            height = int(window.winfo_height())  # type: ignore[attr-defined]
            region = (ctypes.c_ulong * 4)(0, 0, width, height)
            x11.XChangeProperty(
                display,
                window_id,
                property_atom,
                cardinal_atom,
                32,
                0,
                ctypes.cast(region, ctypes.POINTER(ctypes.c_ubyte)),
                4,
            )
            x11.XFlush(display)
            return True
        finally:
            x11.XCloseDisplay(display)

    def run_window(self, window: XanWindow) -> None:
        import tkinter as tk
        from tkinter import ttk

        try:
            root = tk.Tk()
            root.title(window.title)
            root.geometry(f"{window.width}x{window.height}")
            root.resizable(False, False)
            if window.glass:
                root.configure(background="#eaf0f6")
                try:
                    root.wm_attributes("-alpha", 0.93)
                except tk.TclError:
                    print(
                        "[Xanlib] Aviso: este compositor no permite transparencia.",
                        file=sys.stderr,
                    )

            for label, action in window.buttons:
                button = ttk.Button(
                    root,
                    text=label,
                    command=lambda item=action: print(f"[UI] Evento de botón: {item}"),
                )
                button.pack(padx=16, pady=8, fill="x")

            root.deiconify()
            root.update_idletasks()
            if window.glass and not self._request_kwin_blur(root):
                print(
                    "[Xanlib] Aviso: el compositor no ofrece blur X11; "
                    "se usa transparencia nativa.",
                    file=sys.stderr,
                )
            root.mainloop()
        except tk.TclError as error:
            raise OSError(f"no se pudo abrir la ventana gráfica: {error}") from error

    def run_game(self, game: NyuxGame) -> None:
        import tkinter as tk

        try:
            root = tk.Tk()
            root.title(game.title)
            root.resizable(False, False)
            canvas = tk.Canvas(root, width=640, height=400, background="#101827", highlightthickness=0)
            canvas.pack()
            canvas.create_text(
                320, 32, text="Nyux click game - clic en el objetivo",
                fill="white", font=("TkDefaultFont", 16),
            )
            target = canvas.create_oval(300, 180, 340, 220, fill="#36a3ff", outline="")
            score_text = canvas.create_text(320, 370, text="Puntos: 0", fill="white")
            score = 0

            def hit_target(event: object) -> None:
                nonlocal score
                x1, y1, x2, y2 = canvas.coords(target)
                if x1 <= event.x <= x2 and y1 <= event.y <= y2:  # type: ignore[attr-defined]
                    score += 1
                    x = 40 + (score * 97) % 560
                    y = 80 + (score * 67) % 260
                    canvas.coords(target, x - 20, y - 20, x + 20, y + 20)
                    canvas.itemconfigure(score_text, text=f"Puntos: {score}")

            canvas.bind("<Button-1>", hit_target)

            def close_on_escape(event: object) -> None:
                if event.keysym == "Escape":  # type: ignore[attr-defined]
                    root.destroy()

            root.bind("<Escape>", close_on_escape)
            root.mainloop()
        except tk.TclError as error:
            raise OSError(f"no se pudo iniciar el juego: {error}") from error


class Win32Backend:
    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("Win32 solo está disponible en Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._callbacks: list[object] = []

    @staticmethod
    def _raise_last_error(action: str) -> None:
        error = ctypes.get_last_error()
        raise OSError(error, f"{action}: {ctypes.FormatError(error)}")

    def set_wallpaper(self, filename: str) -> None:
        path = Path(filename).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"no existe el archivo de fondo: {path}")
        self.user32.SystemParametersInfoW.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        ]
        self.user32.SystemParametersInfoW.restype = ctypes.c_int
        if not self.user32.SystemParametersInfoW(20, 0, str(path), 0x01 | 0x02):
            self._raise_last_error("no se pudo cambiar el fondo de escritorio")

    def run_window(self, window: XanWindow) -> None:
        from ctypes import wintypes

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        class_name = f"NyuxWindow_{id(window)}"
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        instance = self.kernel32.GetModuleHandleW(None)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long),
            ]

        WM_DESTROY, WM_COMMAND, WM_CLOSE = 0x0002, 0x0111, 0x0010
        WS_OVERLAPPEDWINDOW, WS_VISIBLE = 0x00CF0000, 0x10000000
        WS_CHILD, WS_TABSTOP = 0x40000000, 0x00010000
        WS_EX_CLIENTEDGE = 0x00000200
        button_actions: dict[int, str] = {}

        def window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == WM_COMMAND:
                action = button_actions.get(wparam & 0xFFFF)
                if action:
                    print(f"[UI] Evento de botón: {action}")
                return 0
            if message == WM_CLOSE:
                self.user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                self.user32.PostQuitMessage(0)
                return 0
            return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        self.user32.LoadCursorW.restype = wintypes.HANDLE
        arrow_cursor = ctypes.cast(ctypes.c_void_p(32512), wintypes.LPCWSTR)
        callback = WNDPROC(window_proc)
        self._callbacks.append(callback)
        window_class = WNDCLASSW(
            0,
            callback,
            0,
            0,
            instance,
            None,
            self.user32.LoadCursorW(None, arrow_cursor),
            ctypes.c_void_p(6),
            None,
            class_name,
        )
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        if not self.user32.RegisterClassW(ctypes.byref(window_class)):
            self._raise_last_error("no se pudo registrar la ventana")

        rect = wintypes.RECT(0, 0, window.width, window.height)
        self.user32.AdjustWindowRect.argtypes = [
            ctypes.POINTER(wintypes.RECT),
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        self.user32.AdjustWindowRect.restype = wintypes.BOOL
        self.user32.AdjustWindowRect(ctypes.byref(rect), WS_OVERLAPPEDWINDOW, False)
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HANDLE,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        hwnd = self.user32.CreateWindowExW(
            0,
            class_name,
            window.title,
            WS_OVERLAPPEDWINDOW,
            100,
            100,
            rect.right - rect.left,
            rect.bottom - rect.top,
            None,
            None,
            instance,
            None,
        )
        if not hwnd:
            self._raise_last_error("no se pudo crear la ventana")

        if window.glass:
            backdrop = ctypes.c_int(3)
            self.dwmapi.DwmSetWindowAttribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                wintypes.LPCVOID,
                wintypes.DWORD,
            ]
            self.dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
            result = self.dwmapi.DwmSetWindowAttribute(
                hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
            )
            if result != 0:
                print(
                    "[Xanlib] Aviso: el sistema no ofrece el material blur de DWM.",
                    file=sys.stderr,
                )

        for index, (label, action) in enumerate(window.buttons, start=100):
            button_actions[index] = action
            self.user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "BUTTON",
                label,
                WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                16,
                20 + (index - 100) * 48,
                window.width - 32,
                36,
                hwnd,
                ctypes.c_void_p(index),
                instance,
                None,
            )

        self.user32.ShowWindow(hwnd, 5)
        self.user32.UpdateWindow(hwnd)
        message = MSG()
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.user32.DispatchMessageW.restype = LRESULT
        self.user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self.user32.UnregisterClassW.restype = wintypes.BOOL
        while True:
            result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == 0:
                break
            if result == -1:
                self._raise_last_error("falló el bucle de mensajes Win32")
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))
        if not self.user32.UnregisterClassW(class_name, instance):
            self._raise_last_error("no se pudo liberar la clase de ventana")
        self._callbacks.remove(callback)

    def run_game(self, _: NyuxGame) -> None:
        raise RuntimeError(
            "Game::run aún no está implementado para Windows; "
            "TODO: crear el bucle y renderizador Win32 del juego"
        )


def create_gui_backend() -> TkBackend | Win32Backend:
    operating_system = platform.system()
    if operating_system == "Windows":
        return Win32Backend()
    if operating_system in {"Linux", "Darwin"}:
        return TkBackend()
    raise RuntimeError(f"Xanlib GUI no es compatible con {operating_system}")


class Evaluator:
    MAX_ARENA_BYTES = 64 * 1024 * 1024

    def __init__(self, module_directory: Path) -> None:
        self.module_directory = module_directory
        self.scopes: list[dict[str, object]] = [{}]
        self.functions: dict[str, Fn] = {}
        self.loaded_modules: set[str] = set()
        self.open_files: list[NyuxFile] = []
        self.current_web_server: NyuxWebServer | None = None
        self.gui_backend: TkBackend | Win32Backend | None = None

    def get_gui_backend(self, line: int) -> TkBackend | Win32Backend:
        if self.gui_backend is None:
            try:
                self.gui_backend = create_gui_backend()
            except RuntimeError as error:
                raise NyuxError(f"interfaz gráfica no disponible: {error}", line) from error
        return self.gui_backend

    def run(self, program: Program) -> None:
        BytecodeVM(self).run(program)

    def load_module(self, declaration: Use) -> None:
        if declaration.module in self.loaded_modules:
            return
        if declaration.module != "xanlib":
            raise NyuxError(f"módulo desconocido: {declaration.module}", declaration.line)
        module_path = self.module_directory / "xanlib.nyux"
        try:
            source = module_path.read_text(encoding="utf-8")
        except OSError as error:
            raise NyuxError(f"no se pudo leer xanlib.nyux: {error.strerror or error}", declaration.line) from error
        module = Parser(lex(source)).parse()
        if any(isinstance(item, Use) for item in module.body):
            raise NyuxError("xanlib.nyux no puede importar otros módulos", declaration.line)
        for item in module.body:
            if not isinstance(item, Fn):
                raise NyuxError("xanlib.nyux solo puede contener funciones", item.line)
            if item.name in self.functions:
                raise NyuxError(f"función duplicada: {item.name}", item.line)
            self.functions[item.name] = item
        self.loaded_modules.add(declaration.module)

    def call_static_values(
        self, type_name: str, method: str, arguments: list[object], line: int
    ) -> object:
        if type_name == "Web" and method == "server" and len(arguments) == 1:
            port = arguments[0]
            if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
                raise NyuxError("Web::server requiere un puerto entre 0 y 65535", line)
            self.current_web_server = NyuxWebServer(port)
            return self.current_web_server
        if type_name == "Web" and method in {"page", "api"}:
            if self.current_web_server is None:
                raise NyuxError(
                    "inicia Web::server(puerto) antes de registrar rutas",
                    line,
                )
            try:
                if method == "page" and len(arguments) == 2:
                    route, content = arguments
                    if not isinstance(route, str) or not isinstance(content, str):
                        raise ValueError("Web::page requiere ruta y contenido de texto")
                    self.current_web_server.page(route, content)
                    return None
                if method == "api" and len(arguments) == 1:
                    route = arguments[0]
                    if not isinstance(route, str):
                        raise ValueError("Web::api requiere una ruta de texto")
                    self.current_web_server.api(route)
                    return None
            except ValueError as error:
                raise NyuxError(str(error), line) from error
            raise NyuxError("argumentos inválidos para Web::page o Web::api", line)
        if type_name == "Game" and method == "start" and len(arguments) <= 1:
            title = arguments[0] if arguments else "Nyux Game"
            if not isinstance(title, str):
                raise NyuxError("Game::start solo acepta un título de texto", line)
            return NyuxGame(title)
        if type_name == "AI" and method == "run":
            raise NyuxError(
                "AI::run no está conectado: configura un motor y un modelo de inferencia",
                line,
            )
        if type_name == "System" and method == "boot" and not arguments:
            return SystemState(platform.system())
        if type_name == "Memory" and method == "init" and len(arguments) <= 1:
            capacity = arguments[0] if arguments else 1024 * 1024
            if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
                raise NyuxError("Memory::init requiere una capacidad entera mayor que cero", line)
            if capacity > self.MAX_ARENA_BYTES:
                raise NyuxError(
                    f"la arena alojada admite hasta 64 MiB "
                    f"({self.MAX_ARENA_BYTES} bytes); "
                    "reduce el tamaño o usa un target de kernel",
                    line,
                )
            try:
                return MemoryArena(bytearray(capacity))
            except MemoryError as error:
                raise NyuxError("no hay memoria disponible para crear la arena", line) from error
        if type_name == "Screen" and method == "write" and len(arguments) == 1:
            print(self.stringify(arguments[0]))
            return None
        if type_name == "CPU" and method == "info" and not arguments:
            model = platform.processor() or platform.machine() or "desconocido"
            return f"{model} ({os.cpu_count() or 1} hilos lógicos)"
        if type_name == "File" and method == "open" and 1 <= len(arguments) <= 2:
            path, mode = arguments[0], arguments[1] if len(arguments) == 2 else "r"
            if not isinstance(path, str) or not isinstance(mode, str):
                raise NyuxError("File::open requiere una ruta y un modo de texto", line)
            if mode not in {"r", "w", "a", "x", "r+", "w+", "a+", "x+"}:
                raise NyuxError(
                    "modo de archivo no admitido; usa r, w, a, x o su variante +",
                    line,
                )
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = self.module_directory / target
            try:
                opened = target.open(mode, encoding="utf-8")
            except OSError as error:
                raise NyuxError(f"no se pudo abrir {target}: {error.strerror or error}", line) from error
            handle = NyuxFile(opened, line)
            self.open_files.append(handle)
            return handle
        if type_name == "Process" and method == "spawn":
            if not arguments:
                raise NyuxError("Process::spawn requiere el nombre de un ejecutable", line)
            if not all(isinstance(argument, str) and argument for argument in arguments):
                raise NyuxError(
                    "Process::spawn requiere el ejecutable y cada argumento como texto no vacío",
                    line,
                )
            try:
                process = subprocess.Popen(
                    arguments,
                    cwd=self.module_directory,
                    shell=False,
                    close_fds=True,
                )
            except OSError as error:
                raise NyuxError(
                    f"no se pudo iniciar {arguments[0]}: {error.strerror or error}",
                    line,
                ) from error
            return NyuxProcess(process)
        if type_name == "Driver" and method == "load":
            raise NyuxError(
                "Driver::load requiere un kernel nativo y privilegios; "
                "el intérprete alojado no carga módulos del kernel",
                line,
            )
        if type_name == "XanOS" and method == "new" and len(arguments) == 1:
            name = arguments[0]
            if not isinstance(name, str):
                raise NyuxError("XanOS::new requiere un nombre de texto", line)
            return XanOS(name)
        if type_name == "Window" and method in {"new", "create"} and len(arguments) == 3:
            title, width, height = arguments
            if (
                not isinstance(title, str)
                or not isinstance(width, int)
                or isinstance(width, bool)
                or not isinstance(height, int)
                or isinstance(height, bool)
            ):
                raise NyuxError("Window::create requiere (texto, entero, entero)", line)
            if width <= 0 or height <= 0:
                raise NyuxError("el ancho y la altura de Window deben ser mayores que cero", line)
            return XanWindow(title, width, height)
        raise NyuxError(f"constructor desconocido: {type_name}::{method}", line)

    def call_method_values(
        self, receiver: object, method: str, arguments: list[object], line: int
    ) -> object:
        if isinstance(receiver, XanOS):
            if method == "set_wallpaper" and len(arguments) == 1 and isinstance(arguments[0], str):
                try:
                    self.get_gui_backend(line).set_wallpaper(arguments[0])
                except NyuxError:
                    raise
                except (OSError, RuntimeError) as error:
                    raise NyuxError(f"no se pudo cambiar el fondo de escritorio: {error}", line) from error
                receiver.wallpaper = arguments[0]
                print(f"[Xanlib] Fondo: {receiver.wallpaper}")
                return None
            if method == "set_theme" and len(arguments) == 1 and isinstance(arguments[0], str):
                raise NyuxError(
                    "XanOS.set_theme aún no cambia el tema del sistema; TODO: integrar la API nativa del escritorio",
                    line,
                )
            if method == "run" and len(arguments) == 1 and isinstance(arguments[0], XanWindow):
                receiver.running_window = arguments[0]
                try:
                    self.get_gui_backend(line).run_window(receiver.running_window)
                except NyuxError:
                    raise
                except (OSError, RuntimeError) as error:
                    raise NyuxError(f"no se pudo iniciar Xanlib: {error}", line) from error
                return None
        if isinstance(receiver, XanWindow):
            if method == "show" and not arguments:
                try:
                    self.get_gui_backend(line).run_window(receiver)
                except NyuxError:
                    raise
                except (OSError, RuntimeError) as error:
                    raise NyuxError(f"no se pudo abrir la ventana: {error}", line) from error
                return None
            if method == "set_glass" and len(arguments) == 1 and isinstance(arguments[0], bool):
                receiver.glass = arguments[0]
                return None
            if method == "add_button" and len(arguments) in {1, 2} and all(
                isinstance(argument, str) for argument in arguments
            ):
                action = arguments[1] if len(arguments) == 2 else arguments[0]
                receiver.buttons.append((arguments[0], action))
                return None
        if isinstance(receiver, NyuxGame) and method == "run" and not arguments:
            try:
                self.get_gui_backend(line).run_game(receiver)
            except NyuxError:
                raise
            except (OSError, RuntimeError) as error:
                raise NyuxError(f"no se pudo iniciar el juego: {error}", line) from error
            return None
        if isinstance(receiver, SystemState) and method == "name" and not arguments:
            return receiver.os_name
        if isinstance(receiver, NyuxWebServer):
            try:
                if method == "page" and len(arguments) == 2:
                    route, content = arguments
                    if not isinstance(route, str) or not isinstance(content, str):
                        raise ValueError("page requiere ruta y contenido de texto")
                    receiver.page(route, content)
                    return None
                if method == "api" and len(arguments) == 1:
                    if not isinstance(arguments[0], str):
                        raise ValueError("api requiere una ruta de texto")
                    receiver.api(arguments[0])
                    return None
                if method == "serve" and not arguments:
                    receiver.serve()
                    return None
                if method == "port" and not arguments:
                    return receiver.port
            except (OSError, RuntimeError, ValueError) as error:
                raise NyuxError(f"falló el servidor web: {error}", line) from error
        if isinstance(receiver, MemoryArena):
            if method == "read" and len(arguments) == 2:
                offset, length = arguments
                return receiver.read(offset, length)  # type: ignore[arg-type]
            if method == "read_text" and len(arguments) == 2:
                offset, length = arguments
                try:
                    return receiver.read_text(offset, length)  # type: ignore[arg-type]
                except (TypeError, ValueError) as error:
                    raise NyuxError(f"falló la lectura de texto en memoria: {error}", line) from error
            if method == "write" and len(arguments) == 2:
                offset, value = arguments
                return receiver.write(offset, value)  # type: ignore[arg-type]
            if method == "capacity" and not arguments:
                return receiver.capacity
        if isinstance(receiver, NyuxFile):
            try:
                if method == "read" and len(arguments) <= 1:
                    length = arguments[0] if arguments else None
                    return receiver.read(length)  # type: ignore[arg-type]
                if method == "write" and len(arguments) == 1:
                    return receiver.write(arguments[0])  # type: ignore[arg-type]
                if method == "close" and not arguments:
                    receiver.close()
                    return None
            except (OSError, ValueError, TypeError) as error:
                raise NyuxError(f"falló la operación de archivo: {error}", line) from error
        if isinstance(receiver, NyuxProcess):
            try:
                if method == "running" and not arguments:
                    return receiver.running()
                if method == "wait" and not arguments:
                    return receiver.wait()
                if method == "terminate" and not arguments:
                    receiver.terminate()
                    return None
            except OSError as error:
                raise NyuxError(f"falló la operación del proceso: {error}", line) from error
        raise NyuxError(f"método desconocido o argumentos inválidos: {method}", line)

    @staticmethod
    def stringify(value: object) -> str:
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return f"0x{value.hex()}"
        return str(value)


class BytecodeVM:
    MAX_WHILE_ITERATIONS = 1_000_000

    def __init__(self, runtime: Evaluator) -> None:
        self.runtime = runtime
        self.scopes = runtime.scopes
        self.functions: dict[str, BytecodeFunction] = {}

    def run(self, program: Program) -> None:
        bytecode = self.compile(program)
        self.execute_program(bytecode)

    def compile(self, program: Program) -> BytecodeProgram:
        for statement in program.body:
            if isinstance(statement, Fn):
                if statement.name in self.runtime.functions:
                    raise NyuxError(f"función duplicada: {statement.name}", statement.line)
                self.runtime.functions[statement.name] = statement
            elif isinstance(statement, Use):
                self.runtime.load_module(statement)
        bytecode = BytecodeCompiler().compile(program, self.runtime.functions)
        self.functions = bytecode.functions
        return bytecode

    def execute_program(self, bytecode: BytecodeProgram) -> None:
        try:
            self._execute(bytecode.entry)
            main = self.functions.get("main")
            if main is not None:
                self._call_function(main, [], main.line)
        finally:
            for file in self.runtime.open_files:
                if not file.closed:
                    try:
                        file.close()
                    except OSError as error:
                        raise NyuxError(
                            f"no se pudo cerrar un archivo abierto: {error}",
                            file.line,
                        ) from error

    def _execute(self, function: BytecodeFunction) -> object:
        stack: list[object] = []
        loop_counts: dict[int, int] = {}
        position = 0
        scope_depth = len(self.scopes)
        instructions = function.instructions
        try:
            while position < len(instructions):
                instruction = instructions[position]
                opcode = instruction.opcode
                operand = instruction.operand
                line = instruction.line
                position += 1
                try:
                    if opcode == "CONST":
                        stack.append(operand)
                    elif opcode == "LOAD":
                        stack.append(self._lookup(str(operand), line))
                    elif opcode == "DECLARE":
                        name = str(operand)
                        if name in self.scopes[-1]:
                            raise NyuxError(
                                f"la variable {name!r} ya está declarada en este bloque",
                                line,
                            )
                        self.scopes[-1][name] = stack.pop()
                    elif opcode == "ASSIGN":
                        name = str(operand)
                        value = stack.pop()
                        for scope in reversed(self.scopes):
                            if name in scope:
                                scope[name] = value
                                break
                        else:
                            raise NyuxError(
                                f"variable no definida: {name}; declara primero con 'let'",
                                line,
                            )
                    elif opcode == "POP":
                        stack.pop()
                    elif opcode == "PRINT":
                        count = int(operand)
                        values = stack[-count:] if count else []
                        if count:
                            del stack[-count:]
                        print(" ".join(self.runtime.stringify(value) for value in values))
                    elif opcode == "ENTER_SCOPE":
                        self.scopes.append({})
                    elif opcode == "EXIT_SCOPE":
                        if len(self.scopes) <= scope_depth:
                            raise NyuxError("bytecode intentó salir de un ámbito inválido", line)
                        self.scopes.pop()
                    elif opcode == "JUMP":
                        position = int(operand)
                    elif opcode == "JUMP_IF_FALSE":
                        if not stack.pop():
                            position = int(operand)
                    elif opcode == "JUMP_IF_TRUE":
                        if stack.pop():
                            position = int(operand)
                    elif opcode == "TO_BOOL":
                        stack.append(bool(stack.pop()))
                    elif opcode == "DUP":
                        stack.append(stack[-1])
                    elif opcode == "UNARY":
                        value = stack.pop()
                        if operand in {"!", "not"}:
                            stack.append(not value)
                        elif operand == "-" and isinstance(value, (int, float)):
                            stack.append(-value)
                        else:
                            raise NyuxError("el operador '-' requiere un número", line)
                    elif opcode == "BINARY":
                        right = stack.pop()
                        left = stack.pop()
                        stack.append(self._binary(str(operand), left, right, line))
                    elif opcode == "MAKE_RANGE":
                        stop = stack.pop()
                        start = stack.pop()
                        if (
                            not isinstance(start, int)
                            or isinstance(start, bool)
                            or not isinstance(stop, int)
                            or isinstance(stop, bool)
                        ):
                            raise NyuxError("los límites de 'for' deben ser enteros", line)
                        stack.append(iter(range(start, stop)))
                    elif opcode == "FOR_NEXT":
                        name, target = operand  # type: ignore[misc]
                        try:
                            self.scopes[-1][str(name)] = next(stack[-1])  # type: ignore[arg-type]
                        except StopIteration:
                            position = int(target)
                    elif opcode == "CHECK_LOOP":
                        count = loop_counts.get(position - 1, 0)
                        if count >= self.MAX_WHILE_ITERATIONS:
                            raise NyuxError(
                                "el bucle 'while' superó 1.000.000 iteraciones; "
                                "revisa la condición o actualiza su variable",
                                line,
                            )
                        loop_counts[position - 1] = count + 1
                    elif opcode == "CALL":
                        name, count = operand  # type: ignore[misc]
                        arguments = self._pop_arguments(stack, int(count))
                        target = self.functions.get(str(name))
                        if target is None:
                            raise NyuxError(f"función desconocida: {name}", line)
                        stack.append(self._call_function(target, arguments, line))
                    elif opcode == "CALL_STATIC":
                        type_name, method, count = operand  # type: ignore[misc]
                        arguments = self._pop_arguments(stack, int(count))
                        stack.append(
                            self.runtime.call_static_values(
                                str(type_name), str(method), arguments, line
                            )
                        )
                    elif opcode == "CALL_METHOD":
                        method, count = operand  # type: ignore[misc]
                        arguments = self._pop_arguments(stack, int(count))
                        receiver = stack.pop()
                        stack.append(
                            self.runtime.call_method_values(
                                receiver, str(method), arguments, line
                            )
                        )
                    elif opcode == "RETURN":
                        return stack.pop()
                    elif opcode == "HALT":
                        return None
                    else:
                        raise NyuxError(f"opcode desconocido: {opcode}", line)
                except NyuxError:
                    raise
                except (ArithmeticError, IndexError, KeyError, TypeError, ValueError) as error:
                    raise NyuxError(
                        f"falló la instrucción {opcode}: {error}", line
                    ) from error
            return None
        finally:
            while len(self.scopes) > scope_depth:
                self.scopes.pop()

    def _call_function(
        self, function: BytecodeFunction, arguments: list[object], call_line: int
    ) -> object:
        if len(arguments) != len(function.parameters):
            raise NyuxError(
                f"{function.name} esperaba {len(function.parameters)} argumentos "
                f"y recibió {len(arguments)}",
                call_line,
            )
        self.scopes.append(dict(zip(function.parameters, arguments)))
        try:
            return self._execute(function)
        finally:
            self.scopes.pop()

    @staticmethod
    def _pop_arguments(stack: list[object], count: int) -> list[object]:
        if count == 0:
            return []
        arguments = stack[-count:]
        del stack[-count:]
        return arguments

    def _lookup(self, name: str, line: int) -> object:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise NyuxError(f"variable no definida: {name}", line)

    @staticmethod
    def _binary(operator: str, left: object, right: object, line: int) -> object:
        operations = {
            "+": lambda: left + right,  # type: ignore[operator]
            "-": lambda: left - right,  # type: ignore[operator]
            "*": lambda: left * right,  # type: ignore[operator]
            "/": lambda: left / right,  # type: ignore[operator]
            "%": lambda: left % right,  # type: ignore[operator]
            "==": lambda: left == right,
            "!=": lambda: left != right,
            "<": lambda: left < right,  # type: ignore[operator]
            "<=": lambda: left <= right,  # type: ignore[operator]
            ">": lambda: left > right,  # type: ignore[operator]
            ">=": lambda: left >= right,  # type: ignore[operator]
        }
        try:
            return operations[operator]()
        except KeyError as error:
            raise NyuxError(f"operador no reconocido: {operator}", line) from error
        except (ArithmeticError, TypeError, ValueError) as error:
            raise NyuxError(f"operación {operator!r} inválida: {error}", line) from error


def main(arguments: list[str]) -> int:
    show_bytecode = len(arguments) == 3 and arguments[1] == "--bytecode"
    if len(arguments) not in {2, 3} or (len(arguments) == 3 and not show_bytecode):
        print(
            "Uso: python3 nyux.py [--bytecode] archivo.nyux",
            file=sys.stderr,
        )
        return 2
    source_path = Path(arguments[2] if show_bytecode else arguments[1])
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"Error: no se pudo leer {source_path}: {error.strerror or error}", file=sys.stderr)
        return 1

    try:
        tokens = lex(source)
        print(f"[Lexer] {len(tokens) - 1} tokens")
        program = Parser(tokens).parse()
        print("[Parser] AST creado")
        vm = BytecodeVM(Evaluator(Path(__file__).resolve().parent))
        bytecode = vm.compile(program)
        print(f"[Bytecode] {bytecode.instruction_count} instrucciones compiladas")
        if show_bytecode:
            print(bytecode.disassemble())
        print("[VM] Ejecutando bytecode...")
        vm.execute_program(bytecode)
    except NyuxError as error:
        print(str(error), file=sys.stderr)
        return 1
    print("[Nyux] Ejecución finalizada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
