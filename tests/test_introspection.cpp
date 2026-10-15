#include <nanobind/nanobind.h>
#include <nanobind/stl/vector.h>

namespace nb = nanobind;
using namespace nb::literals;

struct Track {};
struct Arrow {};
struct ReentrantType {};

namespace nanobind::detail {
template <> struct type_caster<Arrow> {
    static constexpr auto Name = const_name("typing.Literal['->']");
};
}
using ArrowObject = nb::typed<nb::object, Arrow>;

NB_MODULE(test_introspection_ext, m) {
    nb::class_<ReentrantType>(m, "ReentrantType");
    m.def("extend", [scope = m.ptr()](nb::object value, bool native) {
        if (native)
            nb::borrow<nb::module_>(scope).def("reentrant", [](ReentrantType, nb::object x) { return x; },
                       "value"_a, "x"_a = value);
        else
            nb::borrow<nb::module_>(scope).def("reentrant", [](nb::object x) { return x; },
                       nb::sig("def reentrant(x: int = 1) -> int"), "x"_a = value);
    });
    m.def("arrow_nested", [](ArrowObject, std::vector<ArrowObject> values) { return values; },
          "first"_a, "values"_a);
    m.def("make_default", [](nb::object value, bool display) {
        return nb::cpp_function([](nb::object x) { return x; },
                                nb::arg("x").sig(display ? "display_only()" : nullptr) = value);
    }, "value"_a, "display"_a = false);
    m.def("make_signature", [](const char *signature, nb::object value) {
        return nb::cpp_function([](nb::object x) { return x; },
                                nb::sig(signature), "x"_a = value);
    });
    m.def("make_method", [](const char *signature) {
        return nb::cpp_function([](nb::object, nb::args) {},
                                nb::is_method(), nb::sig(signature));
    });
    m.def("unnamed_after_named", [](int x, int y) { return x + y; }, "x"_a, nb::arg());
    nb::class_<Track>(m, "Track")
        .def(nb::init<>())
        .def("unnamed_metadata", [](Track &, int x) { return x; }, nb::arg())
        .def("unnamed_optional", [](Track &, nb::object x) { return x; }, nb::arg().none())
        .def("named", [](Track &, double x) { return x; }, "x"_a)
        .def("native_args_first", [](nb::args args) { return args.size(); })
        .def("native_empty", [] { return 7; })
        .def("common_overloaded", [](Track &, int x) { return x; }, "x"_a)
        .def("common_overloaded", [](Track &, double x) { return x; }, "x"_a);
}
