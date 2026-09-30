#include <windows.h>
#include <string>

class __declspec(dllexport) Pf3MockJson {
public:
    explicit Pf3MockJson(int value) : receiver(value) {}
    ~Pf3MockJson();
    std::string dump(int indent) const;
private:
    int receiver;
};

static int g_dump_count = 0;
static int g_destructor_count = 0;
static int g_receiver = 0;
static int g_indent = 0;

std::string Pf3MockJson::dump(int indent) const {
    ++g_dump_count; g_receiver = receiver; g_indent = indent;
    return "{\"receiver\":" + std::to_string(receiver) + ",\"indent\":" + std::to_string(indent) + "}";
}

Pf3MockJson::~Pf3MockJson() { ++g_destructor_count; }

extern "C" __declspec(dllexport) Pf3MockJson * pf3_mock_create() { return new Pf3MockJson(51); }
extern "C" __declspec(dllexport) void pf3_mock_destroy(Pf3MockJson * value) { delete value; }
extern "C" __declspec(dllexport) int pf3_mock_dump_count() { return g_dump_count; }
extern "C" __declspec(dllexport) int pf3_mock_destructor_count() { return g_destructor_count; }
extern "C" __declspec(dllexport) int pf3_mock_receiver() { return g_receiver; }
extern "C" __declspec(dllexport) int pf3_mock_indent() { return g_indent; }
