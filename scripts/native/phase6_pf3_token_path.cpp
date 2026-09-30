// T534 test-only offline PF3 token-path proof child. No provider or inference entrypoint.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#ifndef PF3_SYNTHETIC
#include "llama.h"
#include "common/json.h"
#include "common/json-schema-to-grammar.h"
#include "phase6_pf3_member_abi.h"
#endif

#ifndef PF3_SYNTHETIC
using pf3_json_parse_fn = common_json (__cdecl *)(const std::string &);
using pf3_json_destroy_fn = void (__cdecl *)(common_json *);
static pf3_json_parse_fn g_common_json_parse = nullptr;
static pf3_member_abi::dump_member_fn<common_json> g_common_json_dump{};
static pf3_json_destroy_fn g_common_json_destroy = nullptr;
static pf3_member_abi::Checks g_member_abi_checks{};
static std::size_t g_json_destroy_calls = 0;
static const char * g_member_abi_failure = nullptr;

common_json::~common_json() {
    if (!g_common_json_destroy) std::terminate();
    g_common_json_destroy(this);
    ++g_json_destroy_calls;
}
#endif

namespace {
constexpr std::size_t kSchemaMax = 2u * 1024u * 1024u;
constexpr std::size_t kRawMax = 16u * 1024u;
constexpr std::size_t kGrammarMax = 8u * 1024u * 1024u;
constexpr std::size_t kTokenMax = 4096u;
constexpr std::size_t kVocabMax = 262144u;
constexpr std::size_t kEogMax = 256u;
constexpr std::size_t kDecodeMax = 64u * 1024u;

struct EnvelopeHeader {
    char magic[8];
    std::uint32_t nonce_size;
    std::uint32_t schema_size;
    std::uint32_t raw_size;
    std::uint32_t sampler_size;
};

struct Input {
    std::string nonce;
    std::string schema;
    std::string raw;
    std::string sampler;
};

void fail(const char * text) { throw std::runtime_error(text); }

#ifndef PF3_SYNTHETIC
std::wstring utf8_to_wide(const std::string & value) {
    if (value.empty()) return {};
    const int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                                          static_cast<int>(value.size()), nullptr, 0);
    if (count <= 0) fail("utf8 path");
    std::wstring result(static_cast<std::size_t>(count), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                            static_cast<int>(value.size()), result.data(), count) != count) {
        fail("utf8 path");
    }
    return result;
}
#endif

std::string read_exact(HANDLE handle, std::size_t size) {
    std::string result(size, '\0');
    std::size_t offset = 0;
    while (offset < size) {
        const DWORD request = static_cast<DWORD>(std::min<std::size_t>(size - offset, 1u << 20));
        DWORD read = 0;
        if (!ReadFile(handle, result.data() + offset, request, &read, nullptr) || read == 0) fail("input read");
        offset += read;
    }
    return result;
}

Input read_input(HANDLE handle) {
    EnvelopeHeader header{};
    DWORD read = 0;
    if (!ReadFile(handle, &header, sizeof(header), &read, nullptr) || read != sizeof(header)) fail("input header");
    if (std::memcmp(header.magic, "PF3PATH1", 8) != 0 || header.nonce_size != 32 ||
        header.schema_size > kSchemaMax || header.raw_size > kRawMax || header.sampler_size > 65536) fail("input bounds");
    Input result{read_exact(handle, header.nonce_size), read_exact(handle, header.schema_size),
                 read_exact(handle, header.raw_size), read_exact(handle, header.sampler_size)};
    char extra = 0;
    if (ReadFile(handle, &extra, 1, &read, nullptr) && read != 0) fail("input trailing");
    return result;
}

void verify_input_identity(HANDLE handle, std::uint64_t volume, std::uint64_t file_id, std::uint64_t size) {
    BY_HANDLE_FILE_INFORMATION info{};
    if (!GetFileInformationByHandle(handle, &info)) fail("input identity");
    const std::uint64_t observed_id = (static_cast<std::uint64_t>(info.nFileIndexHigh) << 32) | info.nFileIndexLow;
    const std::uint64_t observed_size = (static_cast<std::uint64_t>(info.nFileSizeHigh) << 32) | info.nFileSizeLow;
    if (info.dwVolumeSerialNumber != volume || observed_id != file_id || observed_size != size) fail("input identity");
}

void ready(const char * phase, const std::string & nonce) {
    std::cout << "READY_" << phase << " " << nonce << std::endl;
    std::string line;
    if (!std::getline(std::cin, line) || line != std::string("CONTINUE_") + phase + " " + nonce) fail("control");
}

std::string json_escape(std::string_view value) {
    static constexpr char hex[] = "0123456789abcdef";
    std::string out = "\"";
    for (unsigned char c : value) {
        switch (c) {
        case '\"': out += "\\\""; break; case '\\': out += "\\\\"; break;
        case '\b': out += "\\b"; break; case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break; case '\r': out += "\\r"; break; case '\t': out += "\\t"; break;
        default:
            if (c < 0x20) { out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
            else out.push_back(static_cast<char>(c));
        }
    }
    out.push_back('\"');
    return out;
}

template<class T> void json_numbers(std::ostream & out, const std::vector<T> & values) {
    out << '[';
    for (std::size_t i = 0; i < values.size(); ++i) { if (i) out << ','; out << values[i]; }
    out << ']';
}

void json_booleans(std::ostream & out, const std::vector<bool> & values) {
    out << '[';
    for (std::size_t i = 0; i < values.size(); ++i) { if (i) out << ','; out << (values[i] ? "true" : "false"); }
    out << ']';
}

std::string base64_encode(std::string_view input) {
    static constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string output;
    if (input.size() > (std::numeric_limits<std::size_t>::max)() / 4 * 3) fail("base64 size");
    output.reserve(((input.size() + 2) / 3) * 4);
    for (std::size_t i = 0; i < input.size(); i += 3) {
        const std::uint32_t a = static_cast<unsigned char>(input[i]);
        const std::uint32_t b = i + 1 < input.size() ? static_cast<unsigned char>(input[i + 1]) : 0;
        const std::uint32_t c = i + 2 < input.size() ? static_cast<unsigned char>(input[i + 2]) : 0;
        const std::uint32_t value = (a << 16) | (b << 8) | c;
        output.push_back(alphabet[(value >> 18) & 63]); output.push_back(alphabet[(value >> 12) & 63]);
        output.push_back(i + 1 < input.size() ? alphabet[(value >> 6) & 63] : '=');
        output.push_back(i + 2 < input.size() ? alphabet[value & 63] : '=');
    }
    return output;
}

void emit_result(const Input & input, const std::string & grammar, const std::vector<std::int32_t> & tokens,
                 const std::vector<std::int32_t> & prefix_after, const std::vector<bool> & prefix_allowed,
                 const std::string & decoded, std::size_t vocab_size, const std::vector<std::int32_t> & eog,
                 const std::vector<std::int32_t> & eog_after, const std::vector<bool> & eog_allowed,
                 std::int32_t accepted_eog, bool roundtrip, std::size_t constant_calls,
                 const std::string & abi_json, const std::string & member_abi_json) {
    std::cout << "{\"nonce\":" << json_escape(input.nonce)
              << ",\"grammar_size\":" << grammar.size()
              << ",\"grammar_b64\":" << json_escape(base64_encode(grammar)) << ",\"token_ids\":";
    json_numbers(std::cout, tokens);
    std::cout << ",\"prefix_before_ids\":"; json_numbers(std::cout, tokens);
    std::cout << ",\"prefix_after_ids\":"; json_numbers(std::cout, prefix_after);
    std::cout << ",\"prefix_allowed\":"; json_booleans(std::cout, prefix_allowed);
    std::cout << ",\"roundtrip_exact\":" << (roundtrip ? "true" : "false")
              << ",\"decoder_b64\":" << json_escape(base64_encode(decoded))
              << ",\"vocab_size\":" << vocab_size << ",\"eog_ids\":";
    json_numbers(std::cout, eog);
    std::cout << ",\"eog_before_ids\":"; json_numbers(std::cout, eog);
    std::cout << ",\"eog_after_ids\":"; json_numbers(std::cout, eog_after);
    std::cout << ",\"eog_allowed\":"; json_booleans(std::cout, eog_allowed);
    std::cout << ",\"accepted_eog\":" << accepted_eog
              << ",\"accounted_generated_tokens\":" << tokens.size() + 1
              << ",\"calls\":{\"emitter\":1,\"tokenize\":2,\"detokenize\":2,"
                 "\"prefix_apply\":" << tokens.size() << ",\"prefix_accept\":" << tokens.size()
              << ",\"vocab_is_eog\":" << vocab_size << ",\"eog_apply\":" << eog.size()
              << ",\"eog_accept\":1,\"token_to_piece\":0,\"provider\":0,\"inference\":0,"
                 "\"server\":0,\"gpu\":0,\"game\":0,\"actions\":0,\"constant_native\":" << constant_calls << "}"
              << ",\"sampler_record\":" << input.sampler
              << ",\"abi\":" << abi_json
              << ",\"member_abi\":" << member_abi_json
              << ",\"status\":\"PROOF_COMPLETE\"}" << std::endl;
}

#ifdef PF3_SYNTHETIC
void prove(const Input & input) {
    if (input.schema.empty() || input.raw.empty()) fail("synthetic input");
    ready("PRE", input.nonce);
    std::vector<std::int32_t> tokens;
    tokens.reserve(input.raw.size());
    for (unsigned char c : input.raw) tokens.push_back(static_cast<std::int32_t>(c));
    if (tokens.size() > kTokenMax) fail("synthetic token bound");
    const std::vector<std::int32_t> eog{256};
    emit_result(input, input.schema, tokens, tokens, std::vector<bool>(tokens.size(), true), input.raw,
        257, eog, eog, std::vector<bool>{true}, 256, true, 8,
        "{\"llama_model_params_size\":0,\"llama_model_params_vocab_only_offset\":0,"
        "\"llama_model_params_n_gpu_layers_offset\":0,\"llama_model_params_devices_offset\":0,"
        "\"llama_token_data_size\":0,\"llama_token_data_id_offset\":0,"
        "\"llama_token_data_logit_offset\":0,\"llama_token_data_p_offset\":0,"
        "\"llama_token_data_array_size\":0,\"llama_token_data_array_data_offset\":0,"
        "\"llama_token_data_array_count_offset\":0,\"llama_token_data_array_selected_offset\":0,"
        "\"llama_token_data_array_sorted_offset\":0}",
        "{\"schema_version\":\"aiwolf.pf3-dump-member-abi-runtime.v1\",\"synthetic\":true}");
    ready("POST", input.nonce);
}
#else
template<class T> T load_proc(HMODULE module, const char * name) {
    const auto address = GetProcAddress(module, name);
    if (!address) fail(name);
    return reinterpret_cast<T>(address);
}

using grammar_emitter_fn = std::string (__cdecl *)(const common_json &, bool);
grammar_emitter_fn g_grammar_emitter = nullptr;

std::string member_abi_runtime_json(const std::string & symbol) {
    const bool symbol_match = symbol == PF3_MEMBER_ABI_EXPECTED_SYMBOL;
    const bool shape = sizeof(pf3_member_abi::dump_member_fn<common_json>) == 8
        && sizeof(FARPROC) == 8 && sizeof(void *) == 8
        && std::is_trivially_copyable_v<pf3_member_abi::dump_member_fn<common_json>>;
    const bool compile_match = _MSC_VER == PF3_MEMBER_ABI_EXPECTED_MSC_VER
        && _MSC_FULL_VER == PF3_MEMBER_ABI_EXPECTED_MSC_FULL_VER
        && _MSVC_LANG == PF3_MEMBER_ABI_EXPECTED_MSVC_LANG
        && _ITERATOR_DEBUG_LEVEL == PF3_MEMBER_ABI_EXPECTED_ITERATOR_DEBUG_LEVEL
        && alignof(pf3_member_abi::dump_member_fn<common_json>) == PF3_MEMBER_ABI_EXPECTED_PMF_ALIGNMENT
#ifdef _DLL
        && true;
#else
        && false;
#endif
    const std::string compile_sha = PF3_MEMBER_ABI_COMPILE_RECORD_SHA;
    auto reject = [](const char * check) -> void { g_member_abi_failure = check; fail("member ABI"); };
    if (compile_sha.size() != 64 || !std::all_of(compile_sha.begin(), compile_sha.end(), [](char value) {
            return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f'); })) reject("COMPILE_RECORD");
    if (!compile_match) reject("COMPILER_MACROS");
    if (!shape) reject("PMF_SHAPE");
    if (!symbol_match) reject("SYMBOL");
    if (!g_member_abi_checks.export_nonforwarded) reject("EXPORT_DIRECT");
    if (!g_member_abi_checks.address_in_pinned_module) reject("ADDRESS_MODULE");
    if (!g_member_abi_checks.address_in_executable_section) reject("ADDRESS_SECTION");
    if (!g_member_abi_checks.getmodule_owner_matches) reject("GETMODULE_OWNER");
    if (!g_member_abi_checks.virtualquery_allocation_base_matches) reject("VIRTUALQUERY_BASE");
    if (!g_member_abi_checks.pmf_roundtrip_bytes_match) reject("ROUNDTRIP_BYTES");
    if (!g_member_abi_checks.pmf_roundtrip_pointer_match) reject("ROUNDTRIP_POINTER");
    if (!g_member_abi_checks.binding_enabled || !pf3_member_abi::all(g_member_abi_checks)) reject("PMF_MODE");
    return std::string("{\"_MSC_FULL_VER\":") + std::to_string(_MSC_FULL_VER)
        + ",\"_MSC_VER\":" + std::to_string(_MSC_VER)
        + ",\"_MSVC_LANG\":" + std::to_string(_MSVC_LANG)
        + ",\"address_in_executable_section\":true,\"address_in_pinned_module\":true"
          ",\"binding_enabled\":true,\"compile_record_sha256\":\"" PF3_MEMBER_ABI_COMPILE_RECORD_SHA "\""
          ",\"decorated_symbol\":" + json_escape(symbol)
        + ",\"dynamic_crt\":true,\"export_kind\":\"DIRECT_EXECUTABLE\",\"export_nonforwarded\":true"
          ",\"farproc_size\":8,\"getmodule_owner_matches\":true,\"iterator_debug_level\":"
        + std::to_string(_ITERATOR_DEBUG_LEVEL)
        + ",\"pmf_alignment\":" + std::to_string(alignof(pf3_member_abi::dump_member_fn<common_json>))
        + ",\"pmf_mode\":\"MSVC_DEFAULT_BEST_CASE_NO_BASE\",\"pmf_roundtrip_bytes_match\":true"
          ",\"pmf_roundtrip_pointer_match\":true,\"pmf_size\":8,\"pmf_trivially_copyable\":true"
          ",\"pointer_size\":8,\"schema_version\":\"aiwolf.pf3-dump-member-abi-runtime.v1\""
          ",\"virtualquery_allocation_base_matches\":true}";
}

struct pf3_buffer_v1 { std::uint8_t * data; std::size_t capacity; std::size_t size; };
struct pf3_error_v1 { std::uint32_t code; std::uint32_t reserved; };

extern "C" __declspec(noinline) std::int32_t pf3_emit_grammar_v1(
    const std::uint8_t * schema_utf8, std::size_t schema_size,
    pf3_buffer_v1 * output, pf3_error_v1 * error) noexcept {
    if (!schema_utf8 || !output || !error || schema_size > kSchemaMax || output->capacity > kGrammarMax) return -1;
    try {
        const std::string input_text(reinterpret_cast<const char *>(schema_utf8), schema_size);
        if (!g_common_json_parse || !g_common_json_dump) { error->code = 5; return -5; }
        const auto parsed = g_common_json_parse(input_text);
        // The runner supplies compact canonical JSON in the product insertion order.  Requiring the same dump
        // rejects whitespace, non-canonical spellings, and duplicate keys collapsed by
        // the DOM parser before the actual server emitter is called.
        if ((parsed.*g_common_json_dump)(-1) != input_text) { error->code = 4; return -4; }
        if (!g_grammar_emitter) { error->code = 5; return -5; }
        const std::string grammar = g_grammar_emitter(parsed, false);
        if (grammar.empty() || grammar.size() > output->capacity) { output->size = grammar.size(); error->code = 2; return -2; }
        std::memcpy(output->data, grammar.data(), grammar.size()); output->size = grammar.size(); error->code = 0; return 0;
    } catch (...) { error->code = 3; return -3; }
}

void prove(const Input & input, const std::string & model_path, const std::string & native_dir,
           const std::string & emitter_symbol, const std::string & json_parse_symbol,
           const std::string & json_dump_symbol, const std::string & json_destroy_symbol) {
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    const std::wstring native_dir_w = utf8_to_wide(native_dir);
    const std::wstring model_path_w = utf8_to_wide(model_path);
    const auto cookie = AddDllDirectory(native_dir_w.c_str());
    if (!cookie) fail("dll directory");
    const auto llama = LoadLibraryExW((native_dir_w + L"\\llama.dll").c_str(), nullptr,
                                      LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    if (!llama) fail("llama load");
    const auto common = LoadLibraryExW((native_dir_w + L"\\llama-common.dll").c_str(), nullptr,
                                       LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    if (!common) fail("common load");
    g_grammar_emitter = load_proc<grammar_emitter_fn>(common, emitter_symbol.c_str());
    g_common_json_parse = load_proc<pf3_json_parse_fn>(common, json_parse_symbol.c_str());
    g_common_json_dump = pf3_member_abi::bind<common_json>(common, json_dump_symbol.c_str(), g_member_abi_checks);
    g_common_json_destroy = load_proc<pf3_json_destroy_fn>(common, json_destroy_symbol.c_str());
    const std::string member_abi = member_abi_runtime_json(json_dump_symbol);
    auto model_default = load_proc<decltype(&llama_model_default_params)>(llama, "llama_model_default_params");
    auto model_load = load_proc<decltype(&llama_model_load_from_file)>(llama, "llama_model_load_from_file");
    auto model_vocab = load_proc<decltype(&llama_model_get_vocab)>(llama, "llama_model_get_vocab");
    auto model_free = load_proc<decltype(&llama_model_free)>(llama, "llama_model_free");
    auto tokenize = load_proc<decltype(&llama_tokenize)>(llama, "llama_tokenize");
    auto detokenize = load_proc<decltype(&llama_detokenize)>(llama, "llama_detokenize");
    auto vocab_n = load_proc<decltype(&llama_vocab_n_tokens)>(llama, "llama_vocab_n_tokens");
    auto is_eog = load_proc<decltype(&llama_vocab_is_eog)>(llama, "llama_vocab_is_eog");
    auto grammar_init = load_proc<decltype(&llama_sampler_init_grammar)>(llama, "llama_sampler_init_grammar");
    auto sampler_apply = load_proc<decltype(&llama_sampler_apply)>(llama, "llama_sampler_apply");
    auto sampler_accept = load_proc<decltype(&llama_sampler_accept)>(llama, "llama_sampler_accept");
    auto sampler_free = load_proc<decltype(&llama_sampler_free)>(llama, "llama_sampler_free");
    llama_model_params params = model_default(); params.vocab_only = true; params.n_gpu_layers = 0; params.devices = nullptr;
    // The frozen API takes UTF-8 paths on Windows.  utf8_to_wide above first proves
    // the argument is valid UTF-8; the exact original UTF-8 bytes are passed here.
    (void) model_path_w;
    llama_model * model = model_load(model_path.c_str(), params); if (!model) fail("model load");
    const llama_vocab * vocab = model_vocab(model); if (!vocab) fail("vocab");
    ready("PRE", input.nonce);
    std::vector<std::uint8_t> grammar_buffer(kGrammarMax); pf3_buffer_v1 buffer{grammar_buffer.data(), grammar_buffer.size(), 0};
    pf3_error_v1 error{}; if (pf3_emit_grammar_v1(reinterpret_cast<const std::uint8_t *>(input.schema.data()), input.schema.size(), &buffer, &error) != 0 || buffer.size == 0) fail("grammar");
    std::string grammar(reinterpret_cast<char *>(buffer.data), buffer.size);
    if (input.raw.size() > static_cast<std::size_t>((std::numeric_limits<std::int32_t>::max)())) fail("raw bound");
    const int32_t raw_size = static_cast<std::int32_t>(input.raw.size());
    const int32_t needed = tokenize(vocab, input.raw.data(), raw_size, nullptr, 0, false, false);
    if (needed >= 0 || -static_cast<std::int64_t>(needed) > static_cast<std::int64_t>(kTokenMax)) fail("tokenize size");
    std::vector<llama_token> tokens(static_cast<std::size_t>(-needed));
    const int32_t token_capacity = static_cast<std::int32_t>(tokens.size());
    if (tokenize(vocab, input.raw.data(), raw_size, tokens.data(), token_capacity, false, false) != token_capacity) fail("tokenize");
    llama_sampler * sampler = grammar_init(vocab, grammar.c_str(), "root"); if (!sampler) fail("sampler");
    std::vector<llama_token> prefix_after;
    std::vector<bool> prefix_allowed;
    prefix_after.reserve(tokens.size()); prefix_allowed.reserve(tokens.size());
    for (llama_token token : tokens) {
        llama_token_data data{token, 0.0f, 0.0f}; llama_token_data_array array{&data, 1, -1, false};
        sampler_apply(sampler, &array);
        prefix_after.push_back(data.id);
        prefix_allowed.push_back(std::isfinite(data.logit) && data.logit == 0.0f && data.id == token);
        if (!prefix_allowed.back()) fail("prefix");
        sampler_accept(sampler, token);
    }
    const int32_t decoded_needed = detokenize(vocab, tokens.data(), token_capacity, nullptr, 0, false, false);
    if (decoded_needed >= 0 || -static_cast<std::int64_t>(decoded_needed) > static_cast<std::int64_t>(kDecodeMax)) fail("decode size");
    std::string decoded(static_cast<std::size_t>(-decoded_needed), '\0');
    const int32_t decoded_capacity = static_cast<std::int32_t>(decoded.size());
    const int32_t decoded_size = detokenize(vocab, tokens.data(), token_capacity, decoded.data(), decoded_capacity, false, false);
    if (decoded_size < 0 || decoded_size > decoded_capacity) fail("decode"); decoded.resize(static_cast<std::size_t>(decoded_size));
    const int32_t n_vocab = vocab_n(vocab); if (n_vocab <= 0 || n_vocab > static_cast<int32_t>(kVocabMax)) fail("vocab bound");
    std::vector<llama_token> eog;
    for (llama_token id = 0; id < n_vocab; ++id) if (is_eog(vocab, id)) { if (eog.size() == kEogMax) fail("eog bound"); eog.push_back(id); }
    const std::size_t required_non_eog = std::min<std::size_t>(512, tokens.size());
    for (std::size_t i = 0; i < required_non_eog; ++i) {
        if (std::find(eog.begin(), eog.end(), tokens[i]) != eog.end()) fail("prefix eog");
    }
    llama_token accepted = -1;
    std::vector<llama_token> eog_after;
    std::vector<bool> eog_allowed;
    eog_after.reserve(eog.size()); eog_allowed.reserve(eog.size());
    for (llama_token id : eog) {
        llama_token_data data{id, 0.0f, 0.0f};
        llama_token_data_array array{&data,1,-1,false};
        sampler_apply(sampler,&array);
        eog_after.push_back(data.id);
        const bool allowed = std::isfinite(data.logit) && data.logit == 0.0f && data.id == id;
        eog_allowed.push_back(allowed);
        if (accepted < 0 && allowed) accepted = id;
    }
    if (accepted < 0) fail("eog"); sampler_accept(sampler, accepted);
    std::vector<std::int32_t> out_tokens(tokens.begin(), tokens.end()), out_eog(eog.begin(), eog.end());
    const std::string abi =
        "{\"llama_model_params_size\":" + std::to_string(sizeof(llama_model_params)) +
        ",\"llama_model_params_vocab_only_offset\":" + std::to_string(offsetof(llama_model_params, vocab_only)) +
        ",\"llama_model_params_n_gpu_layers_offset\":" + std::to_string(offsetof(llama_model_params, n_gpu_layers)) +
        ",\"llama_model_params_devices_offset\":" + std::to_string(offsetof(llama_model_params, devices)) +
        ",\"llama_token_data_size\":" + std::to_string(sizeof(llama_token_data)) +
        ",\"llama_token_data_id_offset\":" + std::to_string(offsetof(llama_token_data, id)) +
        ",\"llama_token_data_logit_offset\":" + std::to_string(offsetof(llama_token_data, logit)) +
        ",\"llama_token_data_p_offset\":" + std::to_string(offsetof(llama_token_data, p)) +
        ",\"llama_token_data_array_size\":" + std::to_string(sizeof(llama_token_data_array)) +
        ",\"llama_token_data_array_data_offset\":" + std::to_string(offsetof(llama_token_data_array, data)) +
        ",\"llama_token_data_array_count_offset\":" + std::to_string(offsetof(llama_token_data_array, size)) +
        ",\"llama_token_data_array_selected_offset\":" + std::to_string(offsetof(llama_token_data_array, selected)) +
        ",\"llama_token_data_array_sorted_offset\":" + std::to_string(offsetof(llama_token_data_array, sorted)) + "}";
    std::vector<std::int32_t> out_prefix_after(prefix_after.begin(), prefix_after.end());
    std::vector<std::int32_t> out_eog_after(eog_after.begin(), eog_after.end());
    emit_result(input, grammar, out_tokens, out_prefix_after, prefix_allowed, decoded, n_vocab, out_eog,
                out_eog_after, eog_allowed, accepted, decoded == input.raw, 14, abi, member_abi);
    ready("POST", input.nonce);
    sampler_free(sampler); model_free(model); g_grammar_emitter = nullptr;
    g_common_json_parse = nullptr; g_common_json_dump = nullptr; g_common_json_destroy = nullptr;
    FreeLibrary(common); FreeLibrary(llama); RemoveDllDirectory(cookie);
}

void prove_abi_smoke(const Input & input, const std::string & native_dir,
                     const std::string & json_parse_symbol, const std::string & json_dump_symbol,
                     const std::string & json_destroy_symbol) {
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    const std::wstring native_dir_w = utf8_to_wide(native_dir);
    const auto cookie = AddDllDirectory(native_dir_w.c_str());
    if (!cookie) fail("dll directory");
    const auto common = LoadLibraryExW((native_dir_w + L"\\llama-common.dll").c_str(), nullptr,
                                       LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    if (!common) fail("common load");
    g_common_json_parse = load_proc<pf3_json_parse_fn>(common, json_parse_symbol.c_str());
    g_common_json_dump = pf3_member_abi::bind<common_json>(common, json_dump_symbol.c_str(), g_member_abi_checks);
    g_common_json_destroy = load_proc<pf3_json_destroy_fn>(common, json_destroy_symbol.c_str());
    const std::string runtime = member_abi_runtime_json(json_dump_symbol);
    ready("PRE", input.nonce);
    constexpr std::string_view fixed = "{\"pf3\":1}";
    std::string returned;
    {
        const auto parsed = g_common_json_parse(std::string(fixed));
        returned = (parsed.*g_common_json_dump)(-1);
    }
    const bool exact = returned == fixed && g_json_destroy_calls == 1;
    std::cout << "{\"cleanup\":\"PENDING_POST\",\"compile_record_sha256\":\"" PF3_MEMBER_ABI_COMPILE_RECORD_SHA "\""
              << ",\"destroy_calls\":" << g_json_destroy_calls
              << ",\"dump_calls\":1,\"exception\":false,\"exit_code\":0"
              << ",\"input_utf8\":\"{\\\"pf3\\\":1}\",\"output_exact_match\":" << (exact ? "true" : "false")
              << ",\"output_sha256\":\"67eab7d64b4b478e4079fce23f1d287a0798a77a3b76d91ba92ac6a2e15c4590\""
              << ",\"output_size\":" << returned.size() << ",\"output_utf8\":\"{\\\"pf3\\\":1}\""
              << ",\"parse_calls\":1,\"runtime_record\":" << runtime
              << ",\"schema_version\":\"aiwolf.pf3-dump-actual-abi-smoke.v1\""
              << ",\"seh\":false,\"status\":\"" << (exact ? "ABI_COMPATIBLE" : "UNKNOWN_ABI_IDENTITY")
              << "\",\"timed_out\":false}" << std::endl;
    if (!exact) fail("ABI smoke mismatch");
    ready("POST", input.nonce);
    g_common_json_parse = nullptr; g_common_json_dump = {}; g_common_json_destroy = nullptr;
    FreeLibrary(common); RemoveDllDirectory(cookie);
}
#endif
} // namespace

int main(int argc, char ** argv) {
    try {
        HANDLE input = nullptr;
        std::uint64_t input_volume = 0, input_file_id = 0, input_size = 0;
        std::string model_path, native_dir, emitter_symbol, json_parse_symbol, json_dump_symbol, json_destroy_symbol;
        for (int i = 1; i < argc;) {
            if (i + 1 >= argc) fail("argument");
            const std::string key = argv[i];
            const std::string value = argv[i + 1];
            if (key == "--input-handle") {
                char * end = nullptr;
                const auto parsed = std::strtoull(value.c_str(), &end, 10);
                if (!end || *end != '\0') fail("input handle");
                input = reinterpret_cast<HANDLE>(parsed);
            }
            else if (key == "--input-volume" || key == "--input-file-id" || key == "--input-size") {
                char * end = nullptr;
                const auto parsed = std::strtoull(value.c_str(), &end, 10);
                if (!end || *end != '\0') fail("input identity argument");
                if (key == "--input-volume") input_volume = parsed;
                else if (key == "--input-file-id") input_file_id = parsed;
                else input_size = parsed;
            }
            else if (key == "--model") model_path = value;
            else if (key == "--native-dir") native_dir = value;
            else if (key == "--emitter-symbol") emitter_symbol = value;
            else if (key == "--json-parse-symbol") json_parse_symbol = value;
            else if (key == "--json-dump-symbol") json_dump_symbol = value;
            else if (key == "--json-destroy-symbol") json_destroy_symbol = value;
            else fail("argument");
            i += 2;
        }
        if (!input || input == INVALID_HANDLE_VALUE) fail("input handle");
        verify_input_identity(input, input_volume, input_file_id, input_size);
        const Input values = read_input(input);
#ifdef PF3_SYNTHETIC
        (void) model_path; (void) native_dir; (void) emitter_symbol;
        (void) json_parse_symbol; (void) json_dump_symbol; (void) json_destroy_symbol;
        prove(values);
#else
#ifdef PF3_ABI_SMOKE_ONLY
        if (native_dir.empty() || json_parse_symbol.empty() || json_dump_symbol.empty() || json_destroy_symbol.empty())
            fail("native paths");
        prove_abi_smoke(values, native_dir, json_parse_symbol, json_dump_symbol, json_destroy_symbol);
#else
        if (model_path.empty() || native_dir.empty() || emitter_symbol.empty() || json_parse_symbol.empty()
                || json_dump_symbol.empty() || json_destroy_symbol.empty()) fail("native paths");
        prove(values, model_path, native_dir, emitter_symbol, json_parse_symbol, json_dump_symbol,
              json_destroy_symbol);
#endif
#endif
        return 0;
    } catch (const std::exception & error) {
#ifndef PF3_SYNTHETIC
        if (g_member_abi_failure) {
            std::cout << "{\"dump_calls\":0,\"emitter_calls\":0,\"failing_check\":"
                      << json_escape(g_member_abi_failure)
                      << ",\"parse_calls\":0,\"schema_version\":\"aiwolf.pf3-dump-member-abi-runtime-failure.v1\""
                         ",\"status\":\"UNKNOWN_ABI_IDENTITY\"}" << std::endl;
        }
#endif
        std::cerr << "PF3_CHILD_FAILED " << error.what() << std::endl;
        return 70;
    } catch (...) {
        return 71;
    }
}
