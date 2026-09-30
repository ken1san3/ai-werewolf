#include "phase6_pf3_member_abi.h"

#include <iostream>
#include <stdexcept>
#include <string>

class Pf3MockJson {
public:
    explicit Pf3MockJson(int value) : receiver(value) {}
    ~Pf3MockJson();
    std::string dump(int) const;
private:
    int receiver;
};

template<class T> T proc(HMODULE module, const char * name) {
    const auto value = GetProcAddress(module, name);
    if (!value) throw std::runtime_error("mock export");
    return reinterpret_cast<T>(value);
}

int main(int argc, char ** argv) {
    try {
        if (argc != 3) return 60;
        const HMODULE module = LoadLibraryExA(argv[1], nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!module) return 61;
        using create_fn = Pf3MockJson * (__cdecl *)();
        using destroy_fn = void (__cdecl *)(Pf3MockJson *);
        using count_fn = int (__cdecl *)();
        const auto create = proc<create_fn>(module, "pf3_mock_create");
        const auto destroy = proc<destroy_fn>(module, "pf3_mock_destroy");
        const auto dump_count = proc<count_fn>(module, "pf3_mock_dump_count");
        const auto destructor_count = proc<count_fn>(module, "pf3_mock_destructor_count");
        const auto receiver = proc<count_fn>(module, "pf3_mock_receiver");
        const auto indent = proc<count_fn>(module, "pf3_mock_indent");
        pf3_member_abi::Checks checks{};
        const auto member = pf3_member_abi::bind<Pf3MockJson>(module, argv[2], checks);
        if (!pf3_member_abi::all(checks)) return 62;
        Pf3MockJson * object = create();
        if (!object) return 63;
        const std::string returned = (object->*member)(-1);
        destroy(object);
        const std::string expected = "{\"receiver\":51,\"indent\":-1}";
        const bool match = returned == expected;
        std::cout << "{\"call_count\":" << dump_count()
                  << ",\"cpp_exception\":false,\"destructor_count\":" << destructor_count()
                  << ",\"exit_code\":0,\"indent_expected\":-1,\"indent_observed\":" << indent()
                  << ",\"mock_caller_exe_sha256\":\"PARENT_VALIDATES\""
                  << ",\"mock_dll_sha256\":\"PARENT_VALIDATES\""
                  << ",\"receiver_expected\":51,\"receiver_observed\":" << receiver()
                  << ",\"returned_exact_match\":" << (match ? "true" : "false")
                  << ",\"returned_sha256\":\"b48cc5aca2fed16b571abc808e9b3d0de5bd7e36ab53a9359fa22b33ef55e7c7\""
                  << ",\"returned_size\":" << returned.size() << ",\"returned_utf8\":\"{\\\"receiver\\\":51,\\\"indent\\\":-1}\""
                  << ",\"schema_version\":\"aiwolf.pf3-dump-member-abi-mock.v1\""
                  << ",\"seh\":false,\"status\":\"PASS\",\"timed_out\":false}" << std::endl;
        const bool valid = match && dump_count() == 1 && destructor_count() == 1 && receiver() == 51 && indent() == -1;
        FreeLibrary(module);
        return valid ? 0 : 64;
    } catch (...) {
        return 65;
    }
}
