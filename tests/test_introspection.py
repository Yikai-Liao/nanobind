import gc
import inspect
import subprocess
import sys
import sysconfig
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import MethodType

import pytest
import test_classes_ext as c
import test_functions_ext as f
import test_introspection_ext as t

P = inspect.Parameter


def check_annotations(fn, expected):
    assert fn.__annotations__ == inspect.get_annotations(fn) == expected
    signature = inspect.signature(fn)
    for name, annotation in expected.items():
        actual = signature.return_annotation if name == "return" else signature.parameters[name].annotation
        assert actual == annotation


@pytest.mark.parametrize("fn,kinds", [
    (f.test_kw_only_all, [P.KEYWORD_ONLY] * 2),
    (f.test_kw_only_with_defaults, [P.POSITIONAL_ONLY, P.POSITIONAL_OR_KEYWORD] + [P.KEYWORD_ONLY] * 2),
    (f.test_args_kwonly_kwargs, [P.POSITIONAL_OR_KEYWORD] * 2 +
     [P.VAR_POSITIONAL, P.KEYWORD_ONLY, P.VAR_KEYWORD]),
])
def test_native_parameters(fn, kinds):
    assert [p.kind for p in inspect.signature(fn).parameters.values()] == kinds


def test_bind_matches_calls():
    fn = f.test_kw_only_with_defaults
    signature = inspect.signature(fn)
    bound = signature.bind(z=6)
    bound.apply_defaults()
    assert fn(*bound.args, **bound.kwargs) == (3, 4, 5, 6)
    for args, kwargs in [((3, 4, 5), {"z": 6}), ((), {})]:
        for call in [signature.bind, fn]:
            with pytest.raises(TypeError):
                call(*args, **kwargs)


@pytest.mark.parametrize("display", [False, True])
def test_default_identity_freshness_and_release(display):
    class Default:
        calls = 0
        def __repr__(self):
            self.calls += 1
            raise AssertionError("introspection must not render a default")

    value = Default()
    ref = weakref.ref(value)
    fn = t.make_default(value, display)
    signature = inspect.signature(fn)
    assert signature.parameters["x"].default is value and fn() is value
    check_annotations(fn, {"x": "object", "return": "object"})
    fn.__annotations__["x"] = "changed"
    assert fn.__annotations__["x"] == "object"
    assert signature is not inspect.signature(fn) and value.calls == 0
    if not sysconfig.get_config_var("Py_GIL_DISABLED"):  # FT functions are immortal.
        del signature, fn, value
        gc.collect()
        assert ref() is None


@pytest.mark.parametrize("name,x,kind", [
    ("unnamed_metadata", 3, P.POSITIONAL_ONLY),
    ("unnamed_optional", None, P.POSITIONAL_ONLY),
    ("named", 3.5, P.POSITIONAL_OR_KEYWORD),
])
def test_native_receiver(name, x, kind):
    track = t.Track()
    fn = getattr(t.Track, name)
    signature = inspect.signature(fn)
    assert signature.parameters["self"].kind is kind
    bound = signature.bind(self=track, x=x) if kind is P.POSITIONAL_OR_KEYWORD else signature.bind(track, x)
    assert fn(*bound.args, **bound.kwargs) == getattr(track, name)(x) == x
    if kind is P.POSITIONAL_ONLY:
        for call in [signature.bind, fn]:
            with pytest.raises(TypeError):
                call(self=track, arg=x)


def test_native_keyword_receiver_and_constructor():
    value = f.kw_only_methods.__new__(f.kw_only_methods)
    f.kw_only_methods.__init__(**inspect.signature(f.kw_only_methods.__init__).bind(self=value, v=3).arguments)
    fn = f.kw_only_methods.method_2k
    assert fn(**inspect.signature(fn).bind(self=value, i=2, j=3).arguments) == (2, 3)
    assert value.v == 3 and list(inspect.signature(value.method_2k).parameters) == ["i", "j"]


@pytest.mark.parametrize("fn", [t.Track.unnamed_metadata, t.Track.native_args_first,
    t.Track.native_empty] + [
    t.make_method(f"def example({params})") for params in
    ["self, x", "self, /, x", "*args", "", "*, self", "**kwargs", "x, x"]])
def test_bound_rules(fn):
    track = t.Track()
    bound = fn.__get__(track, t.Track)
    try:
        expected = inspect.signature(MethodType(fn, track))
    except ValueError:
        with pytest.raises(ValueError):
            inspect.signature(bound)
    else:
        assert inspect.signature(bound) == expected
    assert bound.__annotations__ == fn.__annotations__


def test_overloads():
    track = t.Track()
    t.extend(7, False)
    t.extend(7, False)
    custom = t.reentrant
    del t.reentrant
    for fn in [f.test_05, f.test_07, c.Struct.__init__, t.Track.common_overloaded,
               track.common_overloaded, custom]:
        with pytest.raises(ValueError, match="overloaded"):
            inspect.signature(fn)
        assert fn.__annotations__ == {} and len(fn.__nb_signature__) > 1
    assert f.test_05(1) == 1 and f.test_05(1.5) == 2


def test_variadic_receiver():
    track = t.Track()
    assert track.native_args_first(*inspect.signature(track.native_args_first).bind(1, 2).args) == 3


def test_custom_ast_and_failure_boundaries():
    value = object()
    fn = t.make_signature("@missing()\ndef example(x: typing.Literal['->'] = missing()) -> Unknown", value)
    check_annotations(fn, {"x": "typing.Literal['->']", "return": "Unknown"})
    assert inspect.signature(fn).parameters["x"].default is value and fn() is value
    fn = t.make_signature("def example(x, /, *args: int, y: str = 'yes', **kwargs: bool) -> None", value)
    check_annotations(fn, {"args": "int", "y": "str", "kwargs": "bool", "return": "None"})
    assert [p.kind for p in fn.__signature__.parameters.values()] == [0, 2, 3, 4]
    assert fn.__signature__.parameters["y"].default == "yes"
    fn = t.make_signature("def example(x:)", 7)
    assert fn() == 7
    for attribute in ["__signature__", "__annotations__"]:
        with pytest.raises(SyntaxError):
            getattr(fn, attribute)
    fn = t.make_signature("def example(x, y: int = missing()) -> None", 7)
    assert fn.__annotations__ == {"y": "int", "return": "None"}
    with pytest.raises(ValueError):
        inspect.signature(fn)


@pytest.mark.parametrize("annotation", ["", "nonascii_你好\0tail"])
def test_annotation_text(annotation):
    check_annotations(t.make_signature(f"def example(x: {annotation!r}) -> {annotation!r}", 7),
                      {"x": annotation, "return": annotation})


def test_native_annotation_boundaries():
    check_annotations(t.arrow_nested, {"first": "typing.Literal['->']",
        "values": "collections.abc.Sequence[typing.Literal['->']]", "return": "list[typing.Literal['->']]"})
    assert t.arrow_nested("->", ["->"]) == ["->"]
    assert t.Track.unnamed_optional.__annotations__["arg"] == "object | None"
    with pytest.raises(ValueError):
        inspect.signature(t.unnamed_after_named)
    assert t.unnamed_after_named.__annotations__["return"] == "int"


@pytest.mark.parametrize("attribute", ["__annotations__", "__signature__"])
@pytest.mark.parametrize("callback", ["ast_import", "native"])
def test_metadata_survives_reentrant_binding(attribute, callback):
    subprocess.run([sys.executable, "-c", """
import gc, sys, weakref
import test_introspection_ext as t
assert 'inspect' not in sys.modules and 'ast' not in sys.modules
assert t.make_default(7)() == 7
assert t.make_default(7).__annotations__ == {'x': 'object', 'return': 'object'}
assert 'inspect' not in sys.modules and 'ast' not in sys.modules
class Default:
    pass
value = Default()
ref = weakref.ref(value)
native = sys.argv[2] == 'native'
t.extend(value, native)
fn = t.reentrant
del value
def extend():
    t.extend(object(), native)
    del t.reentrant
    if native:
        del t.ReentrantType
    gc.collect()
if sys.argv[2] == 'ast_import':
    class Hook:
        def find_spec(self, fullname, path, target=None):
            if fullname == 'ast':
                sys.meta_path.remove(self)
                extend()
            return None
    sys.meta_path.insert(0, Hook())
else:
    class ModuleDescriptor:
        def __get__(self, instance, owner):
            extend()
            return 'test_introspection_ext'
    t.ReentrantType.__module__ = ModuleDescriptor()
result = getattr(fn, sys.argv[1])
assert ('inspect' in sys.modules) == (sys.argv[1] == '__signature__')
expected = ({'value': 'test_introspection_ext.ReentrantType', 'x': 'object', 'return': 'object'}
            if native else {'x': 'int', 'return': 'int'})
if sys.argv[1] == '__annotations__':
    assert result == expected
else:
    for name, annotation in expected.items():
        actual = result.return_annotation if name == 'return' else result.parameters[name].annotation
        assert actual == annotation
    assert ref() is not None and result.parameters['x'].default is ref()
assert fn.__nb_signature__ == () and fn.__annotations__ == {}
try:
    fn.__signature__
except ValueError:
    pass
else:
    raise AssertionError('superseded callable has no signature')
""", attribute, callback], check=True)


def test_concurrent_introspection():
    def check(_):
        assert inspect.signature(f.test_02).parameters["up"].default == 8
        assert inspect.signature(t.make_signature("def example(x: int = 1) -> int", 7)).parameters["x"].default == 7
        assert f.test_07.__annotations__ == {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(check, range(16)))
