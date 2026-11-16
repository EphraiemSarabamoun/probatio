"""The registry contract, loader and compatibility tables, using synthetic callbacks without agent CLIs or network."""
import ast
import contextlib
import errno
import io
import math
import os
import py_compile
import shutil
import stat
import sys
import subprocess
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import agent_registry as A  # noqa: E402
import probatio as P  # noqa: E402


def callback(*args, **kwargs):
    """Contract fixtures must never need to execute an external process."""
    return None


def agent(**changes):
    """A complete synthetic contract, varied one fact or callback at a time."""
    fields = dict(id="sample", label="Sample", call=callback, options=callback, memory_files=callback,
                  config_dir=callback, version=callback, setup_run=callback, identity=callback,
                  rates_profile=callback, families=("v4",), classes=("I",), memory=("on",), efforts=(), models=())
    fields.update(changes)
    return A.AgentSpec(**fields)


def look(path, follow=True):
    """Test equivalents of the runner's stat-based look policy, preserving filesystem errors."""
    try:
        return os.stat(path, follow_symlinks=follow)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        raise


def there(path):
    return look(path) is not None


def file_there(path):
    found = look(path)
    return found is not None and stat.S_ISREG(found.st_mode)


def link_there(path):
    found = look(path, follow=False)
    return found is not None and stat.S_ISLNK(found.st_mode)


def display(text):
    """Visible escaped controls stand in for the runner's injected display policy."""
    return str(text).encode("unicode_escape").decode("ascii")


EXTENSION = '''from agent_registry import AgentSpec
def callback(*args, **kwargs):
    return None
def register(registry):
    registry.register(AgentSpec(id="sample", label="Sample", call=callback, options=callback,
        memory_files=callback, config_dir=callback, version=callback, setup_run=callback,
        identity=callback, rates_profile=callback, families=("v4",), classes=("I",),
        memory=("on",), efforts=(), models=()))
'''


class Contract(unittest.TestCase):
    def test_complete_spec_and_optional_hooks(self):
        spec = agent(families=A.FAMILIES, classes=A.CLASSES, memory=A.MEMORY, efforts=A.EFFORTS,
                     models=("one", "two"), config_variable="SAMPLE_CONFIG_DIR", report_leg=True)
        self.assertEqual(spec.families, A.FAMILIES)
        self.assertEqual(spec.memory_candidates(in_use=Path("unused")), [])
        self.assertEqual(spec.memory_candidates(), [])
        self.assertEqual(spec.preflight_lines(opts=None), [])
        self.assertEqual(spec.report_lines(meta={}), [])
        self.assertEqual(spec.extra_requests(calls=[]), 0)
        # A default hook returns a fresh collection on every invocation.
        spec.preflight_lines(None).append("discarded")
        self.assertEqual(spec.preflight_lines(None), [])

    def test_executable_fields_are_required(self):
        with self.assertRaises(TypeError):
            A.AgentSpec(id="sample", label="Sample")

    def test_every_executable_field_must_be_callable(self):
        for field in ("call", "options", "memory_files", "config_dir", "version", "setup_run", "identity",
                      "rates_profile", "memory_candidates", "preflight_lines", "report_lines", "extra_requests"):
            with self.subTest(field=field), self.assertRaisesRegex(A.RegistryError, field + " must be callable"):
                agent(**{field: None})

    def test_validation_does_not_execute_callbacks(self):
        def forbidden(*args, **kwargs):
            self.fail("registration executed agent code")
        A.Registry().register(agent(call=forbidden, options=forbidden, identity=forbidden))

    def test_every_callback_signature_binds_without_execution(self):
        for name in A.CALLBACK_FORMS:
            wrong = (lambda required: None) if name in ("version", "config_dir") else (lambda: None)
            with self.subTest(name=name), self.assertRaisesRegex(A.RegistryError, name):
                agent(**{name: wrong})

    def test_call_and_optional_hooks_accept_positional_and_keyword_contract_forms(self):
        def no_server(prompt, condition, opts):
            pass
        def positional_server(prompt, condition, opts, server=None, /):
            pass
        def keyword_server(prompt, condition, opts, *, server=None):
            pass
        for wrong in (no_server, positional_server, keyword_server):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(A.RegistryError, "call"):
                agent(call=wrong)
        for name in ("memory_candidates", "preflight_lines", "report_lines", "extra_requests"):
            with self.subTest(name=name), self.assertRaisesRegex(A.RegistryError, name):
                agent(**{name: lambda other: []})
        def proper(prompt, condition, opts, server=None):
            self.fail("signature binding executed callback")
        self.assertIs(agent(call=proper).call, proper)

    def test_ids_are_simple_lowercase_identifiers(self):
        for value in (None, 3, "", "Upper", "two words", "../sample", "x\n", "x" * 65):
            with self.subTest(value=value), self.assertRaises(A.RegistryError):
                agent(id=value)
        self.assertEqual(agent(id="sample-2_a").id, "sample-2_a")

    def test_labels_are_bounded_display_safe_text(self):
        for value in (None, 2, "", " Sample", "Sample ", "x\ny", "x\x1by", "x\u202ey", "x\u00a0y", "x\u115fy",
                      "x" * 81):
            with self.subTest(value=value), self.assertRaises(A.RegistryError):
                agent(label=value)

    def test_capability_facts_are_known_nonempty_unique_tuples(self):
        for field, bad in (("families", "other"), ("classes", "IV"), ("memory", "sometimes")):
            for value in ((), [], [bad], (bad,), (None,), ("v4", "v4")):
                with self.subTest(field=field, value=value), self.assertRaises(A.RegistryError):
                    agent(**{field: value})

    def test_efforts_and_models_allow_empty_but_validate_their_contents(self):
        self.assertEqual(agent().efforts, ())
        self.assertEqual(agent().models, ())
        for field, value in (("efforts", ("other",)), ("efforts", ("low", "low")), ("models", ("",)),
                             ("models", ("one\ntwo",)), ("models", ("one", "one")), ("models", []),
                             ("models", ("-flag",)), ("models", ("two words",)), ("models", ("x\u115f",))):
            with self.subTest(field=field, value=value), self.assertRaises(A.RegistryError):
                agent(**{field: value})

    def test_config_variable_and_report_fact_have_declared_types(self):
        for value in ("", "lowercase", "TWO WORDS", 1):
            with self.subTest(value=value), self.assertRaises(A.RegistryError):
                agent(config_variable=value)
        for value in (None, "yes", 1):
            with self.subTest(value=value), self.assertRaises(A.RegistryError):
                agent(report_leg=value)

    def test_config_requirement_is_an_explicit_boolean_fact(self):
        self.assertFalse(agent().config_required)
        self.assertTrue(agent(config_required=True).config_required)
        for value in (None, "required", 1):
            with self.subTest(value=value), self.assertRaises(A.RegistryError):
                agent(config_required=value)

    def test_timing_facts_are_optional_finite_and_copied(self):
        data = {"sample": 2.5, "other": None}
        spec = agent(legacy_seconds=data)
        data["sample"] = 99
        self.assertEqual(spec.legacy_seconds["sample"], 2.5)
        with self.assertRaises(TypeError):
            spec.legacy_seconds["sample"] = 10
        for value in ([], {"bad key": 1}, {"sample": -1}, {"sample": True}, {"sample": math.inf},
                      {"sample": math.nan}, {"sample": "one"}, {"sample": 10 ** 400}):
            with self.subTest(value=value), self.assertRaises(A.RegistryError):
                agent(legacy_seconds=value)

    def test_reference_features_are_immutable_example_facts(self):
        self.assertEqual(agent().reference_features, ())
        self.assertEqual(agent(reference_features=("apps",)).reference_features, ("apps",))
        for value in ([], ("apps", "apps"), ("",), ("line\nbreak",), (None,)):
            with self.subTest(value=value), self.assertRaises(A.RegistryError):
                agent(reference_features=value)

    def test_presentation_facts_have_generic_defaults_and_safe_bounded_text(self):
        self.assertEqual(agent().default_model_label, "default")
        self.assertEqual(agent().time_note, "Time estimate unknown")
        self.assertEqual(agent(default_model_label="default (settings)").default_model_label, "default (settings)")
        for field, maximum in (("default_model_label", 80), ("time_note", 512)):
            for value in (None, "", "two\nlines", "hidden\u115f", "x" * (maximum + 1)):
                with self.subTest(field=field, value=value), self.assertRaises(A.RegistryError):
                    agent(**{field: value})

    def test_spec_is_frozen_and_fact_tuples_cannot_be_mutated(self):
        spec = agent()
        with self.assertRaises(FrozenInstanceError):
            spec.label = "Changed"
        with self.assertRaises(TypeError):
            spec.classes[0] = "II"


class Registration(unittest.TestCase):
    def test_lookup_and_values_keep_registration_order(self):
        registry = A.Registry()
        first, second = agent(id="first"), agent(id="second")
        self.assertIs(registry.register(first), first)
        registry.register(second)
        self.assertEqual(list(registry), ["first", "second"])
        self.assertEqual(len(registry), 2)
        self.assertIs(registry["first"], first)
        self.assertIs(registry.get("second"), second)
        self.assertIsNone(registry.get("missing"))
        self.assertEqual(registry.get("missing", "fallback"), "fallback")
        self.assertEqual(registry.values(), (first, second))
        with self.assertRaises(KeyError):
            registry["missing"]

    def test_values_are_an_immutable_snapshot(self):
        registry = A.Registry()
        registry.register(agent(id="first"))
        values = registry.values()
        registry.register(agent(id="second"))
        self.assertEqual(len(values), 1)
        self.assertIsInstance(values, tuple)

    def test_builtins_and_other_ids_cannot_be_replaced(self):
        for name in ("claude", "codex", "sample"):
            with self.subTest(name=name):
                registry = A.Registry()
                original = registry.register(agent(id=name))
                with self.assertRaisesRegex(A.RegistryError, "already registered"):
                    registry.register(agent(id=name, label="Other"))
                self.assertIs(registry[name], original)

    def test_wrong_objects_and_corrupted_specs_are_refused(self):
        registry = A.Registry()
        with self.assertRaises(A.RegistryError):
            registry.register({"id": "sample"})
        broken = agent()
        object.__setattr__(broken, "classes", ("IV",))
        with self.assertRaises(A.RegistryError):
            registry.register(broken)
        self.assertEqual(len(registry), 0)


class Loading(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="registry-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = self.tmp / A.EXTENSION_FILE
        self.registry = A.Registry()
        self.registry.register(agent(id="first"))

    def plant(self, text):
        self.path.write_text(text, encoding="utf-8")

    def load(self, **hooks):
        fields = dict(there=there, display=display, file_there=file_there, link_there=link_there)
        fields.update(hooks)
        return A.load_extension(self.registry, self.tmp, **fields)

    def refused(self, text=None):
        if text is not None:
            self.plant(text)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(A.RegistryError) as e:
            self.load()
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        self.assertEqual(e.exception.path, self.path)
        self.assertEqual(e.exception.reason, str(e.exception))
        self.assertLessEqual(len(str(e.exception)), A.ERROR_CHARS)
        self.assertNotIn("\n", str(e.exception))
        self.assertNotIn("\r", str(e.exception))
        return str(e.exception)

    def test_absence_returns_the_same_registry_and_checks_only_the_named_path(self):
        looked = []
        self.assertIs(self.load(there=lambda path: looked.append(path) or False,
                                link_there=lambda path: looked.append(path) or False), self.registry)
        self.assertEqual(looked, [self.path, self.path])
        self.assertEqual(list(self.registry), ["first"])

    def test_no_working_directory_or_search_path_fallback(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        (other / A.EXTENSION_FILE).write_text('raise AssertionError("wrong extension")\n')
        before = list(sys.path)
        previous = Path.cwd()
        try:
            os.chdir(other)
            sys.path.insert(0, str(other))
            self.assertIs(self.load(), self.registry)
        finally:
            os.chdir(previous)
            sys.path[:] = before
        self.assertEqual(list(self.registry), ["first"])

    def test_path_loader_registers_through_the_same_interface(self):
        self.plant(EXTENSION)
        before = list(sys.path)
        self.assertIs(self.load(), self.registry)
        self.assertEqual(list(self.registry), ["first", "sample"])
        self.assertEqual(self.registry["sample"].classes, ("I",))
        self.assertEqual(sys.path, before)

    def test_regular_and_dangling_links_are_refused_without_reading_the_target(self):
        source = self.tmp / "source.py"
        source.write_text(EXTENSION)
        self.path.symlink_to(source)
        self.assertEqual(self.refused(), "symbolic links are not allowed")
        self.assertNotIn("sample", self.registry)
        self.path.unlink()
        self.path.symlink_to(self.tmp / "missing.py")
        self.assertEqual(self.refused(), "symbolic links are not allowed")

    def test_unchecked_hash_bytecode_is_ignored_and_never_rewritten(self):
        # The source and planted bytecode deliberately disagree; the source alone must execute.
        self.plant('raise AssertionError("planted bytecode ran")\n')
        cache = Path(A.importlib.util.cache_from_source(str(self.path)))
        py_compile.compile(str(self.path), cfile=str(cache), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        planted = cache.read_bytes()
        self.plant(EXTENSION)
        self.load()
        self.assertIn("sample", self.registry)
        self.assertEqual(cache.read_bytes(), planted)
        self.assertEqual(list(cache.parent.iterdir()), [cache])

    def test_source_load_never_creates_bytecode(self):
        self.plant(EXTENSION)
        with mock.patch.object(sys, "dont_write_bytecode", False):
            self.load()
        self.assertEqual({p.name for p in self.tmp.iterdir()}, {A.EXTENSION_FILE})

    def test_directory_and_fifo_are_refused_before_the_loader_can_open_them(self):
        self.path.mkdir()
        with mock.patch.object(A.importlib.util, "spec_from_file_location", side_effect=AssertionError("opened")):
            self.assertEqual(self.refused(), "not a regular file")
        self.path.rmdir()
        os.mkfifo(self.path)
        with mock.patch.object(A.importlib.util, "spec_from_file_location", side_effect=AssertionError("opened")):
            self.assertEqual(self.refused(), "not a regular file")

    def test_path_look_errors_are_closed_plain_refusals(self):
        error = PermissionError(errno.EACCES, "cannot search")
        for hook in ("there", "link_there", "file_there"):
            with self.subTest(hook=hook):
                self.plant(EXTENSION)
                fields = {hook: mock.Mock(side_effect=error)}
                if hook == "link_there":
                    fields["there"] = lambda _: False
                with self.assertRaises(A.RegistryError) as caught:
                    self.load(**fields)
                self.assertEqual(caught.exception.path, self.path)
                self.assertIn("PermissionError", str(caught.exception))

    def test_import_syntax_and_runtime_errors_are_refused(self):
        self.assertIn("SyntaxError", self.refused("def broken(:\n"))
        self.assertEqual(self.refused('raise ValueError("broken")\n'), "ValueError: broken")

    def test_missing_or_noncallable_register_is_refused(self):
        for source in ("value = 1\n", "register = 3\n"):
            with self.subTest(source=source):
                self.assertEqual(self.refused(source), "extension must define callable register(registry)")

    def test_registration_exception_and_contract_failure_are_refused(self):
        self.assertEqual(self.refused('def register(registry):\n    raise ValueError("bad setup")\n'),
                         "ValueError: bad setup")
        self.assertIn("id must be", self.refused(EXTENSION.replace('id="sample"', 'id="bad id"')))
        self.assertIn("already registered", self.refused(EXTENSION.replace('id="sample"', 'id="first"')))

    def test_registered_specs_are_rechecked_after_register_returns(self):
        source = EXTENSION + '    object.__setattr__(registry["sample"], "classes", ("IV",))\n'
        self.assertIn("classes contains an unsupported value", self.refused(source))

    def test_existing_entries_cannot_be_removed_replaced_or_mutated(self):
        for statement in ('registry._specs.pop("first")',
                          'registry._specs["first"] = registry["sample"]',
                          'object.__setattr__(registry["first"], "label", "Changed")'):
            with self.subTest(statement=statement):
                # Each failure is a failed startup, not a registry the caller may fall back to using.
                self.registry = A.Registry()
                self.registry.register(agent(id="first"))
                self.assertEqual(self.refused(EXTENSION + "    " + statement + "\n"),
                                 "existing agent 'first' cannot be replaced")

    def test_later_mapping_facts_are_frozen_after_setup(self):
        source = EXTENSION + '    object.__setattr__(registry["sample"], "legacy_seconds", {"sample": 1})\n'
        self.plant(source)
        self.load()
        with self.assertRaises(TypeError):
            self.registry["sample"].legacy_seconds["sample"] = 3

    def test_prints_are_suppressed_on_import_registration_and_success(self):
        source = 'import sys\nprint("import output")\nprint("error output", file=sys.stderr)\n' + EXTENSION
        source = source.replace('def register(registry):', 'def register(registry):\n    print("setup output")')
        self.plant(source)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.load()
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        self.assertIn("already registered", self.refused())

    def test_system_exit_is_a_refusal_but_keyboard_interrupt_propagates(self):
        self.assertEqual(self.refused('raise SystemExit("stop")\n'), "SystemExit: stop")
        self.plant("raise KeyboardInterrupt\n")
        with self.assertRaises(KeyboardInterrupt):
            self.load()

    def test_custom_base_exceptions_at_import_and_register_are_refused(self):
        for end in ('raise Stop("import failed")\n',
                    'def register(registry):\n    raise Stop("register failed")\n'):
            with self.subTest(end=end):
                reason = self.refused('class Stop(BaseException): pass\n' + end)
                self.assertIn("Stop:", reason)
        self.plant('def register(registry):\n    raise KeyboardInterrupt\n')
        with self.assertRaises(KeyboardInterrupt):
            self.load()

    def test_wrong_callback_signature_is_a_loading_refusal(self):
        self.assertIn("options must accept", self.refused(EXTENSION.replace('options=callback', 'options=lambda: None')))

    def test_exception_reason_uses_display_and_is_bounded_on_one_line(self):
        source = 'raise ValueError("line1\\nline2\\x1b" + "x" * 2000)\n'
        self.plant(source)
        shown = mock.Mock(side_effect=display)
        with self.assertRaises(A.RegistryError) as caught:
            self.load(display=shown)
        shown.assert_called_once()
        self.assertIn("\\n", str(caught.exception))
        self.assertIn("\\x1b", str(caught.exception))
        self.assertLessEqual(len(str(caught.exception)), A.ERROR_CHARS)
        self.assertNotIn("\n", str(caught.exception))

    def test_missing_import_spec_is_refused(self):
        self.plant(EXTENSION)
        with mock.patch.object(A.importlib.util, "spec_from_file_location", return_value=None):
            self.assertEqual(self.refused(), "cannot load the extension file")

    def test_decorators_work_and_existing_module_entry_is_restored(self):
        source = ('from dataclasses import dataclass\n@dataclass\nclass Value:\n    text: str = "ok"\n'
                  + EXTENSION)
        sentinel = object()
        with mock.patch.dict(sys.modules, {"_probatio_extension": sentinel}):
            self.plant(source)
            self.load()
            self.assertIs(sys.modules["_probatio_extension"], sentinel)


class Invocation(unittest.TestCase):
    """Every callback boundary validates returns and keeps failures out of runner tracebacks."""
    def invoke_value(self, name, value):
        return A.invoke(agent(), name, function=lambda *args, **kwargs: value)

    def test_valid_results_and_path_normalization(self):
        options = SimpleNamespace(model=None, effort=None, workdir="work", config_dir=Path("config"),
                                  config_source="default", features=["one"])
        values = {"call": ("reply", None, "model"), "options": options, "version": "sample version",
                  "memory_files": ["one", Path("two")], "config_dir": "config", "identity": {"nested": [1, True]},
                  "setup_run": None, "rates_profile": (None, "unknown"),
                  "memory_candidates": [{"path": "one", "claude_md": None}],
                  "preflight_lines": ["line"], "report_lines": [], "extra_requests": 0}
        for name, value in values.items():
            with self.subTest(name=name):
                actual = self.invoke_value(name, value)
                expected = [Path("one"), Path("two")] if name == "memory_files" else (
                    Path("config") if name == "config_dir" else value)
                self.assertEqual(actual, expected)
        self.assertIsNone(self.invoke_value("config_dir", None))
        self.assertEqual(self.invoke_value("call", [None, "failed", None]), (None, "failed", None))
        self.assertEqual(self.invoke_value("memory_files", ("one",)), [Path("one")])

    def test_invalid_returns_name_the_callback_and_shape_kind(self):
        values = {"call": (None, [], "model"), "options": {}, "version": None,
                  "memory_files": iter(["one"]), "config_dir": 3, "identity": {"value": float("nan")},
                  "setup_run": "done", "rates_profile": (None, None), "memory_candidates": [{} , "bad"],
                  "preflight_lines": ("line",), "report_lines": [None], "extra_requests": True}
        for name, value in values.items():
            with self.subTest(name=name), self.assertRaises(A.CallbackError) as caught:
                self.invoke_value(name, value)
            error = caught.exception
            self.assertEqual((error.agent_id, error.agent_label, error.callback, error.kind),
                             ("sample", "Sample", name, "shape"))
            self.assertEqual(error.reason, str(error))

    def test_candidate_metadata_requires_the_fields_consumers_read(self):
        bad = ({}, {"path": "one"}, {"claude_md": None}, {"path": 3, "claude_md": None},
               {"path": "one", "claude_md": {}}, {"path": "one", "claude_md": "text"},
               {"path": "one", "claude_md": {"bytes": 0}},
               {"path": "one", "claude_md": {"title": None}})
        for candidate in bad:
            with self.subTest(candidate=candidate), self.assertRaises(A.CallbackError) as caught:
                self.invoke_value("memory_candidates", [candidate])
            self.assertEqual((caught.exception.callback, caught.exception.kind), ("memory_candidates", "shape"))
        for size in (-1, True, None, "one", 1.0, math.inf, math.nan, 10 ** 400):
            with self.subTest(size=type(size)), self.assertRaises(A.CallbackError):
                self.invoke_value("memory_candidates", [{"path": "one", "claude_md": {"bytes": size, "title": None}}])
        with self.assertRaises(A.CallbackError):
            self.invoke_value("memory_candidates", [{"path": "one", "claude_md": {"bytes": 0, "title": 3}}])

    def test_candidate_validation_copies_containers_and_preserves_extra_fields(self):
        values = [{"path": "one", "claude_md": None, "extra": object()},
                  {"path": "two", "claude_md": {"bytes": 0, "title": None, "extra": object()}},
                  {"path": "three", "claude_md": {"bytes": 1234, "title": "Heading"}}]
        copied = self.invoke_value("memory_candidates", values)
        self.assertEqual(copied, values)
        self.assertIsNot(copied, values)
        self.assertTrue(all(a is not b for a, b in zip(copied, values)))
        self.assertIsNot(copied[1]["claude_md"], values[1]["claude_md"])

    def test_options_require_all_documented_fields_and_types(self):
        good = dict(model=None, effort=None, workdir="work", config_dir=None, config_source="default", features=[])
        for field in good:
            missing = dict(good)
            del missing[field]
            with self.subTest(missing=field), self.assertRaises(A.CallbackError):
                self.invoke_value("options", SimpleNamespace(**missing))
        for field, bad in (("model", 3), ("effort", 3), ("workdir", Path("work")),
                           ("config_dir", b"folder"), ("config_source", None), ("features", "one"),
                           ("features", [3])):
            with self.subTest(field=field), self.assertRaises(A.CallbackError):
                self.invoke_value("options", SimpleNamespace(**dict(good, **{field: bad})))

    def test_json_identity_rejects_non_json_objects_cycles_and_infinite_numbers(self):
        cycle = {}
        cycle["cycle"] = cycle
        for bad in ([], {1: "value"}, {"value": (1,)}, {"value": object()}, {"value": math.inf}, cycle):
            with self.subTest(value=type(bad)), self.assertRaises(A.CallbackError) as caught:
                self.invoke_value("identity", bad)
            self.assertEqual(caught.exception.kind, "shape")

    def test_callback_exceptions_are_named_and_bounded(self):
        class Stop(BaseException):
            pass
        for name in A.CALLBACK_FORMS:
            def broken(*args, **kwargs):
                raise Stop("first\nsecond\x1b" + "x" * 2000)
            with self.subTest(name=name), self.assertRaises(A.CallbackError) as caught:
                A.invoke(agent(), name, function=broken)
            self.assertEqual(caught.exception.callback, name)
            self.assertEqual(caught.exception.kind, "exception")
            self.assertIn("Stop", caught.exception.reason)
            self.assertLessEqual(len(caught.exception.reason), A.ERROR_CHARS)
            self.assertNotIn("\n", caught.exception.reason)

    def test_native_error_preservation_is_explicit_and_does_not_bypass_return_validation(self):
        for error in (SystemExit("native refusal"), OSError("native filesystem failure"), BaseException("native")):
            def broken(*args, **kwargs):
                raise error
            for name in A.CALLBACK_FORMS:
                with self.subTest(name=name, error=type(error)), self.assertRaises(type(error)) as caught:
                    A.invoke(agent(), name, function=broken, preserve_errors=True)
                self.assertIs(caught.exception, error)
                with self.subTest(name=name, wrapped=type(error)), self.assertRaises(A.CallbackError) as caught:
                    A.invoke(agent(), name, function=broken)
                self.assertEqual(caught.exception.kind, "exception")
        with self.assertRaises(A.CallbackError) as caught:
            A.invoke(agent(), "version", function=lambda: None, preserve_errors=True)
        self.assertEqual(caught.exception.kind, "shape")

    def test_keyboard_interrupt_and_call_timeout_keep_their_original_type(self):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt
        for name in A.CALLBACK_FORMS:
            with self.subTest(name=name), self.assertRaises(KeyboardInterrupt):
                A.invoke(agent(), name, function=interrupt)
        timeout = subprocess.TimeoutExpired("fixture", 4)
        def expired(*args, **kwargs):
            raise timeout
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            A.invoke(agent(), "call", function=expired)
        self.assertIs(caught.exception, timeout)
        with self.assertRaises(A.CallbackError):
            A.invoke(agent(), "version", function=expired)

    def test_function_override_and_keyword_arguments_reach_the_hook(self):
        function = mock.Mock(return_value=("A", None, None))
        opts, server = object(), object()
        A.invoke(agent(), "call", "prompt", "with", opts, server=server, function=function)
        function.assert_called_once_with("prompt", "with", opts, server=server)
        spec = agent(version=lambda: "sample")
        self.assertEqual(A.invoke(spec, "version"), "sample")

    def test_invocation_never_replaces_process_output_streams(self):
        """Parallel calls must not redirect or restore each other's process-global output streams."""
        before = (sys.stdout, sys.stderr)
        def version():
            self.assertIs(sys.stdout, before[0])
            self.assertIs(sys.stderr, before[1])
            return "sample"
        self.assertEqual(A.invoke(agent(version=version), "version"), "sample")
        self.assertEqual((sys.stdout, sys.stderr), before)


class AgentTableCompatibility(unittest.TestCase):
    """Legacy table patches must restore live registry defaults and preserve intentional overrides."""
    TABLES = ("CALLS", "SECONDS_PER_CALL", "CONFIG_VARIABLE", "AGENT_NAMES")

    class IdentityOnlyValue:
        """An extension override can be any object; restoration must never ask it to compare equal."""
        def __eq__(self, other):
            raise AssertionError("compatibility tables must not compare extension values with ==")

    def setUp(self):
        self.tables = {name: getattr(P, name) for name in self.TABLES}
        saved = {name: (dict(table.overrides), set(table.removed)) for name, table in self.tables.items()}

        def restore():
            # Restore the backing containers directly: using patch.dict here would depend on the behavior under test.
            for name, table in self.tables.items():
                overrides, removed = saved[name]
                table.overrides.clear()
                table.overrides.update(overrides)
                table.removed.clear()
                table.removed.update(removed)

        self.addCleanup(restore)
        for table in self.tables.values():
            table.overrides.clear()
            table.removed.clear()
        self.registry = A.Registry()
        for spec in P.builtin_agents():
            self.registry.register(spec)
        self.registry.register(agent(config_variable="SAMPLE_CONFIG_DIR", legacy_seconds={"sample": 1.0}))
        registry_patch = mock.patch.object(P, "agents", side_effect=lambda: self.registry)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)

    def assert_table_state(self, table, overrides, removed):
        """Compare keys normally and values by identity, including values whose equality is forbidden."""
        self.assertEqual(set(table.overrides), set(overrides))
        for key, value in overrides.items():
            self.assertIs(table.overrides[key], value)
        self.assertEqual(table.removed, removed)

    def replace_call(self, function):
        """Swap the registered sample callback while keeping the same registry facts and shipped agents."""
        registry = A.Registry()
        for spec in self.registry.values():
            registry.register(replace(spec, call=function) if spec.id == "sample" else spec)
        self.registry = registry

    def test_patch_dict_round_trip_preserves_all_four_tables_without_equality(self):
        for name, table in self.tables.items():
            with self.subTest(table=name):
                del table["codex"]
                before = (dict(table.overrides), set(table.removed))
                temporary = {key: self.IdentityOnlyValue() for key in ("claude", "sample", "temporary")}
                with mock.patch.dict(table, temporary):
                    for key, value in temporary.items():
                        self.assertIs(table[key], value)
                self.assert_table_state(table, *before)
                for key in ("claude", "sample"):
                    self.assertIs(table[key], table.field(self.registry[key]))

    def test_registry_call_replacement_is_used_after_patch_dict_restoration(self):
        old_calls, new_calls = [], []

        def old_call(*args, **kwargs):
            old_calls.append(args)
            return "B", None, "sample-model"

        def new_call(*args, **kwargs):
            new_calls.append(args)
            return "A", None, "sample-model"

        self.replace_call(old_call)
        with mock.patch.dict(P.CALLS, {"sample": self.IdentityOnlyValue(), "temporary": self.IdentityOnlyValue()}):
            pass
        self.replace_call(new_call)
        self.assertIs(P.CALLS["sample"], new_call)
        row = {"answer_format": "bare_letter", "choices": [{"label": "A", "maps_to": "chosen"},
                                                            {"label": "B", "maps_to": "other"}]}
        call = dict(condition="with", dataset="sacramentum", config="fixture", id="q", prompt="fixture", row=row)
        with tempfile.TemporaryDirectory(prefix="agent-table-") as folder, contextlib.redirect_stderr(io.StringIO()):
            done = P.run_calls([call], "sample", None, Path(folder), 1)
        self.assertEqual(old_calls, [])
        self.assertEqual(len(new_calls), 1)
        self.assertEqual(done[P.key(call)]["reply"], "A")
        self.assertIsNone(done[P.key(call)]["error"])

    def test_preexisting_overrides_survive_patch_dict_round_trip(self):
        for name, table in self.tables.items():
            with self.subTest(table=name):
                original = self.IdentityOnlyValue()
                table["sample"] = original
                table["before"] = self.IdentityOnlyValue()
                before = (dict(table.overrides), set(table.removed))
                with mock.patch.dict(table, {"sample": self.IdentityOnlyValue(), "temporary": self.IdentityOnlyValue()}):
                    self.assertIsNot(table["sample"], original)
                self.assert_table_state(table, *before)
                self.assertIs(table["sample"], original)

    def test_deleting_registered_overrides_hides_the_agent_until_set_again(self):
        """Deleting a registered override hides the key until an explicit assignment restores it."""
        for name, table in self.tables.items():
            with self.subTest(table=name):
                value = self.IdentityOnlyValue()
                table["sample"] = value
                self.assertIs(table["sample"], value)
                del table["sample"]
                with self.assertRaises(KeyError):
                    table["sample"]
                self.assertNotIn("sample", list(table))
                table["sample"] = value
                self.assertIs(table["sample"], value)
                self.assertIn("sample", list(table))

    def test_deleting_override_only_keys_does_not_hide_later_registered_agents(self):
        for name, table in self.tables.items():
            with self.subTest(table=name):
                key = "later-" + name.lower()
                table[key] = self.IdentityOnlyValue()
                del table[key]
                self.assertNotIn(key, table.overrides)
                self.assertNotIn(key, table.removed)
                with self.assertRaises(KeyError):
                    table[key]
                spec = agent(id=key, label="Later", config_variable="LATER_CONFIG_DIR", legacy_seconds={"sample": 2.0})
                self.registry.register(spec)
                self.assertIn(key, table)
                self.assertIs(table[key], table.field(spec))


class SourcePolicy(unittest.TestCase):
    def test_invisible_policy_includes_the_shared_boundaries(self):
        for low, high in A.INVISIBLE:
            self.assertTrue(A.invisible(chr(low)))
            self.assertTrue(A.invisible(chr(high)))
        self.assertFalse(A.invisible("a"))

    def test_module_does_not_import_runner_or_use_path_boolean_methods(self):
        tree = ast.parse((REPO / "agent_registry.py").read_text())
        forbidden = {"exists", "is_file", "is_dir", "is_symlink", "is_socket", "is_fifo", "is_block_device",
                     "is_char_device", "is_mount"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("probatio", [name.name for name in node.names])
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "probatio")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, forbidden)


if __name__ == "__main__":
    unittest.main()
