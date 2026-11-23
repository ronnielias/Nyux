import contextlib
import io
import json
import platform
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import nyux
import nyux02


class Nyux02Tests(unittest.TestCase):
    def test_typed_declarations_keep_line_comments_and_url_strings(self) -> None:
        source = 'fn main() {\n    let url: String = "https://example.test" // comment\n    print(url)\n}\n'
        lowered = nyux02.lower_typed_declarations(source)
        program = nyux.Parser(nyux.lex(lowered)).parse()
        self.assertEqual(program.body[0].line, 1)

    def test_short_typed_function_lowers_and_runs(self) -> None:
        output = self.run_source(
            """
fun double(value: Int): Int = value * 2
fn main() {
    let input: Int = 21
    print(double(input))
}
"""
        )
        self.assertIn("42", output)

    def run_source(self, source: str) -> str:
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(nyux02.lower_typed_declarations(source))).parse()
        vm = nyux02.Nyux02VM(runtime)
        output = io.StringIO()
        try:
            bytecode = vm.compile(program)
            with contextlib.redirect_stdout(output):
                vm.execute_program(bytecode)
        finally:
            runtime.close_extensions()
        return output.getvalue()

    def test_sql_json_and_runtime_type_check(self) -> None:
        output = self.run_source(
            '''
fn main() {
    let message: String = "safe value"
    let db = Sql::open(":memory:")
    db.execute("CREATE TABLE items (value TEXT)")
    db.execute("INSERT INTO items VALUES (?)", message)
    db.commit()
    let rows = db.query("SELECT value FROM items")
    print(rows.to_json())
    print(Json::valid(rows.to_json()))
    db.close()
}
'''
        )
        self.assertIn('[{"value":"safe value"}]', output)
        self.assertIn("true", output)

    def test_numeric_data_and_predictions_reject_non_finite_values(self) -> None:
        frame = nyux02.NyuxDataFrame(
            ["hours", "score"],
            [{"hours": "nan", "score": "1"}],
        )
        with self.assertRaisesRegex(ValueError, "no es finito"):
            frame.numeric_pairs("hours", "score")

        model = nyux02.LinearModel("hours", "score", 2.0, 0.0, 1.0)
        with self.assertRaisesRegex(ValueError, "debe ser finito"):
            model.predict(float("inf"))
        with self.assertRaisesRegex(ValueError, "excede el rango numérico"):
            model.predict(10**1000)

    def test_typed_declaration_rejects_mismatched_runtime_value(self) -> None:
        source = "fn main() {\n    let age: Int = \"old\"\n}"
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(nyux02.lower_typed_declarations(source))).parse()
        vm = nyux02.Nyux02VM(runtime)
        try:
            with self.assertRaisesRegex(nyux.NyuxError, "se esperaba Int"):
                vm.compile(program)
        finally:
            runtime.close_extensions()

    def test_typed_parameters_are_checked_when_dynamic_call_reaches_them(self) -> None:
        source = """
fn dynamic_age() {
    return "not an integer"
}
fun accept_age(age: Int): Int = age
fn main() {
    print(accept_age(dynamic_age()))
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(nyux02.lower_typed_declarations(source))).parse()
        vm = nyux02.Nyux02VM(runtime)
        try:
            bytecode = vm.compile(program)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(nyux.NyuxError, "se esperaba Int"):
                    vm.execute_program(bytecode)
            self.assertEqual(output.getvalue(), "")
        finally:
            runtime.close_extensions()

    def test_typed_function_parameters_are_rejected_before_execution(self) -> None:
        source = """
fun accept_age(age: Int): Int = age
fn main() {
    print(accept_age("not an integer"))
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(nyux02.lower_typed_declarations(source))).parse()
        try:
            with self.assertRaisesRegex(nyux.NyuxError, "se esperaba Int, se recibió String"):
                nyux02.Nyux02VM(runtime).compile(program)
        finally:
            runtime.close_extensions()

    def test_typed_short_function_return_type_flows_into_call_sites(self) -> None:
        source = """
fun double(value: Int): Int = value * 2
fn main() {
    let message: String = double(21)
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(nyux02.lower_typed_declarations(source))).parse()
        try:
            with self.assertRaisesRegex(nyux.NyuxError, "se esperaba String, se recibió Int"):
                nyux02.Nyux02VM(runtime).compile(program)
        finally:
            runtime.close_extensions()

    def test_tasks_execute_nyux_functions_and_return_values(self) -> None:
        output = self.run_source(
            '''
fn double(value) {
    return value * 2
}
fn main() {
    let task = Task::spawn("double", 21)
    print(task.await())
}
'''
        )
        self.assertIn("42", output)

    def test_task_pool_handles_many_pending_functions(self) -> None:
        declarations = "\n".join(
            f'    let task_{index} = Task::spawn("square", {index})'
            for index in range(40)
        )
        handles = ", ".join(f"task_{index}" for index in range(40))
        source = f"""
fn square(value) {{
    return value * value
}}
fn main() {{
{declarations}
    print(Json::stringify(Task::all({handles})))
}}
"""
        output = self.run_source(source)
        self.assertEqual(json.loads(output), [value * value for value in range(40)])

    def test_task_await_timeout_reports_that_task_is_still_running(self) -> None:
        source = """
fn work() {
    Time::sleep(100)
}
fn main() {
    let task = Task::spawn("work")
    task.await(1)
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(source)).parse()
        vm = nyux02.Nyux02VM(runtime)
        try:
            bytecode = vm.compile(program)
            with self.assertRaisesRegex(nyux.NyuxError, "no terminó en 1 ms; sigue ejecutándose"):
                vm.execute_program(bytecode)
        finally:
            runtime.close_extensions()

    def test_task_submission_rejects_a_full_pending_queue(self) -> None:
        source = """
fn work() {
    return 1
}
fn main() {
    Task::spawn("work")
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(source)).parse()
        vm = nyux02.Nyux02VM(runtime)
        try:
            bytecode = vm.compile(program)
            for _ in range(runtime.MAX_PENDING_TASKS):
                self.assertTrue(runtime.task_slots.acquire(blocking=False))
            with self.assertRaisesRegex(nyux.NyuxError, "límite de 1024 tareas pendientes"):
                vm.execute_program(bytecode)
        finally:
            runtime.close_extensions()

    def test_static_checker_reports_unknown_variable_before_vm_runs(self) -> None:
        source = """
fn main() {
    print(missing)
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(source)).parse()
        vm = nyux02.Nyux02VM(runtime)
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(nyux.NyuxError, "variable no definida: missing"):
                    vm.compile(program)
            self.assertEqual(output.getvalue(), "")
        finally:
            runtime.close_extensions()

    def test_static_checker_reports_unknown_static_method_before_vm_runs(self) -> None:
        source = """
fn main() {
    System::boot_now()
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(source)).parse()
        vm = nyux02.Nyux02VM(runtime)
        try:
            with self.assertRaisesRegex(
                nyux.NyuxError,
                "método estático no definido: System::boot_now",
            ):
                vm.compile(program)
        finally:
            runtime.close_extensions()

    def test_static_api_signatures_check_arity_and_known_argument_types(self) -> None:
        cases = (
            (
                "fn main() { System::boot(1) }",
                "System::boot espera 0 argumentos; recibió 1",
            ),
            (
                "fn main() { Json::parse(1) }",
                "se esperaba String, se recibió Int",
            ),
        )
        for source, message in cases:
            with self.subTest(message=message):
                runtime = nyux02.Nyux02Evaluator(ROOT)
                program = nyux.Parser(nyux.lex(source)).parse()
                try:
                    with self.assertRaisesRegex(nyux.NyuxError, message):
                        nyux02.Nyux02VM(runtime).compile(program)
                finally:
                    runtime.close_extensions()

    def test_static_constructor_types_enable_chained_method_checks(self) -> None:
        source = """
fn main() {
    let os = XanOS::new("Nyx")
    os.set_theme(.LiquidGlass)
    let window = Window::new("Login", 320, 340)
    window.set_glass(true)
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(source)).parse()
        try:
            nyux02.Nyux02VM(runtime).compile(program)
        finally:
            runtime.close_extensions()

    def test_optimizer_folds_constants_without_changing_output(self) -> None:
        source = """
fn main() {
    print((3 + 4) * 5)
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(source)).parse()
        vm = nyux02.Nyux02VM(runtime)
        try:
            bytecode = vm.compile(program)
            function = bytecode.functions["main"]
            self.assertFalse(any(item.opcode == "BINARY" for item in function.instructions))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                vm.execute_program(bytecode)
            self.assertEqual(output.getvalue(), "35\n")
        finally:
            runtime.close_extensions()

    def test_game_world_simulation_tracks_entities_and_score(self) -> None:
        output = self.run_source(
            """
fn main() {
    let game = Game::world("Test", 320, 240)
    game.spawn("player", 20, 40)
    game.move("player", 100, 120)
    game.add_score(3)
    print(game.state_json())
}
"""
        )
        state = json.loads(output)
        self.assertEqual(state["score"], 3)
        self.assertEqual(state["entities"][0], {"name": "player", "x": 100.0, "y": 120.0})

    def test_linux_hardware_read_is_restricted_to_proc_and_sys(self) -> None:
        if platform.system() != "Linux":
            self.skipTest("la lectura de /proc solo está implementada en Linux")
        source = """
fn main() {
    print(Hardware::read_text("/proc/cpuinfo") != "")
}
"""
        self.assertIn("true", self.run_source(source))
        runtime = nyux02.Nyux02Evaluator(ROOT)
        with self.assertRaisesRegex(nyux.NyuxError, "solo se permite lectura"):
            runtime.call_static_values(
                "Hardware", "read_text", [str(ROOT / "README.md")], 1
            )
        runtime.close_extensions()

    def test_web_app_serves_assets_dynamic_html_and_json_handlers(self) -> None:
        source = """
fn home(path, body) {
    return Web::html("<h1>Nyux dinámico</h1>")
}
fn echo(path, body) {
    return Web::json(Json::parse(body))
}
fn main() {
    let app = Web::app(0, "examples/v0.2/site/public")
    app.route("GET", "/hello", "home")
    app.route("POST", "/api/echo", "echo")
    app.start()
}
"""
        runtime = nyux02.Nyux02Evaluator(ROOT)
        program = nyux.Parser(nyux.lex(source)).parse()
        vm = nyux02.Nyux02VM(runtime)
        output = io.StringIO()
        try:
            bytecode = vm.compile(program)
            with contextlib.redirect_stdout(output):
                vm.execute_program(bytecode)
            app = runtime.web_apps[0]
            with urlopen(f"http://127.0.0.1:{app.port}/", timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b"<html", response.read())
            with urlopen(f"http://127.0.0.1:{app.port}/hello", timeout=3) as response:
                self.assertEqual(response.headers.get_content_type(), "text/html")
                self.assertEqual(response.read(), b"<h1>Nyux din\xc3\xa1mico</h1>")
            request = Request(
                f"http://127.0.0.1:{app.port}/api/echo",
                data=b'{"ok":true}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.headers.get_content_type(), "application/json")
                self.assertEqual(json.loads(response.read()), {"ok": True})
            with self.assertRaises(HTTPError) as raised:
                urlopen(f"http://127.0.0.1:{app.port}/%2e%2e/README.md", timeout=3)
            self.assertEqual(raised.exception.code, 403)
        finally:
            runtime.close_extensions()

    def test_cli_executes_both_version_02_examples(self) -> None:
        for path, output in (
            ("examples/v0.2/sql-json.nyux", "JSON data:"),
            ("examples/v0.2/concurrency.nyux", "12 al cuadrado: 144"),
        ):
            with self.subTest(path=path):
                result = subprocess.run(
                    [str(ROOT / "nyux2"), "run", path],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(output, result.stdout)


if __name__ == "__main__":
    unittest.main()
