import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import nyux


def parse_file(path: Path) -> nyux.Program:
    return nyux.Parser(nyux.lex(path.read_text(encoding="utf-8"))).parse()


class NyuxRuntimeTests(unittest.TestCase):
    def test_official_launcher_runs_a_nyux_file(self) -> None:
        result = subprocess.run(
            [str(ROOT / "nyux"), "run", str(ROOT / "examples" / "mi-primer-nyux.nyux")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Hola, Nyux!", result.stdout)

    def test_examples_main_and_system_finish(self) -> None:
        expected = {
            "main.nyux": "Nyux Universal: 8",
            "system.nyux": "Lectura verificada: NyxOS",
        }
        for example, marker in expected.items():
            with self.subTest(example=example):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    with patch.object(
                        nyux.Evaluator,
                        "get_gui_backend",
                        side_effect=AssertionError("un programa no gráfico pidió una GUI"),
                    ):
                        nyux.Evaluator(ROOT).run(parse_file(ROOT / "examples" / example))
                self.assertIn(marker, output.getvalue())

    def test_app_example_builds_window_and_button(self) -> None:
        class FakeBackend:
            window: nyux.XanWindow | None = None

            def run_window(self, window: nyux.XanWindow) -> None:
                self.window = window

        backend = FakeBackend()
        with patch.object(nyux.Evaluator, "get_gui_backend", return_value=backend):
            runtime = nyux.Evaluator(ROOT)
            runtime.run(parse_file(ROOT / "examples" / "app.nyux"))
        if backend.window is None:
            self.fail("la app no pasó su ventana al backend")
        self.assertEqual(
            (backend.window.title, backend.window.width, backend.window.height),
            ("Login", 320, 340),
        )
        self.assertEqual(backend.window.buttons, [("Guardar", "Guardar")])

    def test_memory_checks_bounds_and_types(self) -> None:
        arena = nyux.MemoryArena(bytearray(4))
        self.assertEqual(arena.write(1, "abc"), 3)
        self.assertEqual(arena.read(1, 3), b"abc")
        self.assertEqual(arena.read_text(1, 3), "abc")
        with self.assertRaises(ValueError):
            arena.read(3, 2)
        with self.assertRaises(TypeError):
            arena.read(True, 1)

    def test_return_is_rejected_outside_function(self) -> None:
        with self.assertRaisesRegex(nyux.NyuxError, "return.*dentro de una función"):
            nyux.Parser(nyux.lex("return 1")).parse()

    def test_block_variables_do_not_escape(self) -> None:
        source = "fn main() { if (true) { let hidden = 1 } print(hidden) }"
        runtime = nyux.Evaluator(ROOT)
        with self.assertRaisesRegex(nyux.NyuxError, "variable no definida: hidden"):
            runtime.run(nyux.Parser(nyux.lex(source)).parse())

    def test_assignment_while_and_word_operators(self) -> None:
        source = '''
fn main() {
    let count = 0
    while (count < 3 and true) {
        count = count + 1
    }
    print(count)
}
'''
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            nyux.Evaluator(ROOT).run(nyux.Parser(nyux.lex(source)).parse())
        self.assertEqual(output.getvalue(), "3\n")

    def test_compiler_emits_stack_bytecode_and_vm_executes_functions(self) -> None:
        source = """
fn add(left, right) {
    return left + right
}
fn main() {
    print(add(20, 22))
}
"""
        program = nyux.Parser(nyux.lex(source)).parse()
        vm = nyux.BytecodeVM(nyux.Evaluator(ROOT))
        bytecode = vm.compile(program)
        self.assertIn("BINARY '+'", bytecode.disassemble())
        self.assertIn("CALL ('add', 2)", bytecode.disassemble())

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            vm.execute_program(bytecode)
        self.assertEqual(output.getvalue(), "42\n")

    def test_runtime_error_reports_source_line_and_repair_hint(self) -> None:
        source = "fn main() {\n    print(missing)\n}"
        runtime = nyux.Evaluator(ROOT)
        with self.assertRaises(nyux.NyuxError) as raised:
            runtime.run(nyux.Parser(nyux.lex(source)).parse())
        self.assertEqual(raised.exception.line, 2)
        self.assertIn("Error en línea 2", str(raised.exception))
        self.assertIn("Sugerencia:", str(raised.exception))
        self.assertIn("declárala antes con 'let'", str(raised.exception))

    def test_parser_error_reports_source_line_and_repair_hint(self) -> None:
        source = "fn main() {\n    print(1\n}"
        with self.assertRaises(nyux.NyuxError) as raised:
            nyux.Parser(nyux.lex(source)).parse()
        self.assertEqual(raised.exception.line, 3)
        self.assertIn("se esperaba ')'", str(raised.exception))
        self.assertIn("Sugerencia:", str(raised.exception))

    def test_web_page_and_api_are_served_on_loopback(self) -> None:
        source = '''
let server = Web::server(0)
Web::page("/", "<h1>Nyux</h1>")
Web::api("/users")
'''
        runtime = nyux.Evaluator(ROOT)
        runtime.run(nyux.Parser(nyux.lex(source)).parse())
        server = runtime.scopes[0]["server"]
        if not isinstance(server, nyux.NyuxWebServer):
            self.fail("Web::server no devolvió un servidor")
        with contextlib.redirect_stdout(io.StringIO()):
            server.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.port}/", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"<h1>Nyux</h1>")
            with urlopen(f"http://127.0.0.1:{server.port}/users", timeout=2) as response:
                self.assertEqual(json.loads(response.read()), {"ok": True, "route": "/users"})
        finally:
            server.stop()

    def test_unavailable_ai_and_driver_apis_explain_the_limit(self) -> None:
        for source, expected in (
            ('fn main() { AI::run() }', "modelo de inferencia"),
            ('fn main() { Driver::load("device") }', "kernel nativo"),
        ):
            with self.subTest(source=source):
                runtime = nyux.Evaluator(ROOT)
                program = nyux.Parser(nyux.lex(source)).parse()
                with self.assertRaisesRegex(nyux.NyuxError, expected):
                    runtime.run(program)

    def test_unimplemented_theme_fails_instead_of_succeeding_silently(self) -> None:
        source = '''
fn main() {
    let os = XanOS::new("Nyux")
    os.set_theme("LiquidGlass")
}
'''
        runtime = nyux.Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(source)).parse()
        with self.assertRaisesRegex(nyux.NyuxError, "TODO: integrar la API nativa"):
            runtime.run(program)


if __name__ == "__main__":
    unittest.main()
