#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <type_traits>

#ifndef PF3_MEMBER_ABI_COMPILE_RECORD_SHA
#define PF3_MEMBER_ABI_COMPILE_RECORD_SHA "synthetic"
#endif
#ifndef PF3_MEMBER_ABI_EXPECTED_SYMBOL
#define PF3_MEMBER_ABI_EXPECTED_SYMBOL "synthetic"
#endif
#ifndef PF3_MEMBER_ABI_EXPECTED_MSC_VER
#define PF3_MEMBER_ABI_EXPECTED_MSC_VER -1
#define PF3_MEMBER_ABI_EXPECTED_MSC_FULL_VER -1
#define PF3_MEMBER_ABI_EXPECTED_MSVC_LANG -1
#define PF3_MEMBER_ABI_EXPECTED_ITERATOR_DEBUG_LEVEL -1
#define PF3_MEMBER_ABI_EXPECTED_PMF_ALIGNMENT -1
#endif

namespace pf3_member_abi {

template<class Owner>
using dump_member_fn = std::string (Owner::*)(int) const;

struct Checks {
    bool export_nonforwarded = false;
    bool address_in_pinned_module = false;
    bool address_in_executable_section = false;
    bool getmodule_owner_matches = false;
    bool virtualquery_allocation_base_matches = false;
    bool pmf_roundtrip_bytes_match = false;
    bool pmf_roundtrip_pointer_match = false;
    bool binding_enabled = false;
};

inline bool executable_address(HMODULE module, FARPROC address) noexcept {
    if (!module || !address) return false;
    const auto * base = reinterpret_cast<const std::uint8_t *>(module);
    const auto * dos = reinterpret_cast<const IMAGE_DOS_HEADER *>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0) return false;
    const auto * nt = reinterpret_cast<const IMAGE_NT_HEADERS64 *>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) return false;
    const auto target = reinterpret_cast<const std::uint8_t *>(address);
    const auto * section = IMAGE_FIRST_SECTION(nt);
    for (unsigned index = 0; index < nt->FileHeader.NumberOfSections; ++index, ++section) {
        const std::size_t size = (std::max)(section->Misc.VirtualSize, section->SizeOfRawData);
        const auto * first = base + section->VirtualAddress;
        if (target >= first && target < first + size) {
            return (section->Characteristics & IMAGE_SCN_MEM_EXECUTE) != 0;
        }
    }
    return false;
}

template<class Owner>
dump_member_fn<Owner> bind(HMODULE module, const char * symbol, Checks & checks) noexcept {
    static_assert(sizeof(void *) == 8);
    static_assert(std::is_trivially_copyable_v<dump_member_fn<Owner>>);
    static_assert(sizeof(dump_member_fn<Owner>) == sizeof(FARPROC));
    dump_member_fn<Owner> member{};
    const FARPROC address = module && symbol ? GetProcAddress(module, symbol) : nullptr;
    if (!address) return member;
    checks.export_nonforwarded = true;
    MEMORY_BASIC_INFORMATION memory{};
    checks.virtualquery_allocation_base_matches =
        VirtualQuery(reinterpret_cast<const void *>(address), &memory, sizeof(memory)) == sizeof(memory)
        && memory.AllocationBase == module;
    HMODULE owner = nullptr;
    checks.getmodule_owner_matches =
        GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(address), &owner) != 0 && owner == module;
    checks.address_in_executable_section = executable_address(module, address);
    checks.address_in_pinned_module = checks.virtualquery_allocation_base_matches && checks.getmodule_owner_matches;
    std::memcpy(&member, &address, sizeof(member));
    FARPROC roundtrip = nullptr;
    std::memcpy(&roundtrip, &member, sizeof(roundtrip));
    checks.pmf_roundtrip_bytes_match = std::memcmp(&roundtrip, &address, sizeof(address)) == 0;
    checks.pmf_roundtrip_pointer_match = roundtrip == address;
    checks.binding_enabled = checks.export_nonforwarded && checks.address_in_pinned_module
        && checks.address_in_executable_section && checks.pmf_roundtrip_bytes_match
        && checks.pmf_roundtrip_pointer_match;
    if (!checks.binding_enabled) member = {};
    return member;
}

inline bool all(const Checks & value) noexcept {
    return value.export_nonforwarded && value.address_in_pinned_module
        && value.address_in_executable_section && value.getmodule_owner_matches
        && value.virtualquery_allocation_base_matches && value.pmf_roundtrip_bytes_match
        && value.pmf_roundtrip_pointer_match && value.binding_enabled;
}

} // namespace pf3_member_abi
