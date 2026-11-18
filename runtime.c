// runtime.c — ECL 运行时库（无 CRT，支持 BigInt，COW 值语义）
// 编译: clang-cl /c /Od /GS- /Gy /nologo /Zl /utf-8 -fno-builtin runtime.c

#include "ecl_stdint.h"

// ============================================================
// Windows API
// ============================================================
typedef void* HANDLE;
typedef unsigned long DWORD;
typedef int BOOL;
typedef uint16_t wchar_t;

#define STD_OUTPUT_HANDLE ((DWORD)-11)
#define STD_INPUT_HANDLE  ((DWORD)-10)
#define CP_UTF8  65001
#define CP_GBK   936

__declspec(dllimport) HANDLE __stdcall GetStdHandle(DWORD nStdHandle);
__declspec(dllimport) BOOL __stdcall ReadFile(HANDLE hFile, void* lpBuffer, DWORD nNumberOfBytesToRead, DWORD* lpNumberOfBytesRead, void* lpOverlapped);
__declspec(dllimport) BOOL __stdcall WriteFile(HANDLE hFile, const void* lpBuffer, DWORD nNumberOfBytesToWrite, DWORD* lpNumberOfBytesWritten, void* lpOverlapped);
__declspec(dllimport) BOOL __stdcall WriteConsoleW(HANDLE hConsoleOutput, const wchar_t* lpBuffer, DWORD nNumberOfCharsToWrite, DWORD* lpNumberOfCharsWritten, void* lpReserved);
__declspec(dllimport) __declspec(noreturn) void __stdcall ExitProcess(DWORD uExitCode);
__declspec(dllimport) void* __stdcall VirtualAlloc(void* lpAddress, uint64_t dwSize, DWORD flAllocationType, DWORD flProtect);
__declspec(dllimport) BOOL __stdcall VirtualFree(void* lpAddress, uint64_t dwSize, DWORD dwFreeType);
__declspec(dllimport) int __stdcall MultiByteToWideChar(unsigned int CodePage, DWORD dwFlags, const char* lpMultiByteStr, int cbMultiByte, wchar_t* lpWideCharStr, int cchWideChar);__declspec(dllimport) int __stdcall WideCharToMultiByte(unsigned int CodePage, DWORD dwFlags, const wchar_t* lpWideCharStr, int cchWideChar, char* lpMultiByteStr, int cbMultiByte, const char* lpDefaultChar, BOOL* lpUsedDefaultChar);
__declspec(dllimport) BOOL __stdcall GetConsoleMode(HANDLE hConsoleHandle, DWORD* lpMode);
__declspec(dllimport) HANDLE __stdcall LoadLibraryA(const char* lpLibFileName);
__declspec(dllimport) HANDLE __stdcall LoadLibraryW(const wchar_t* lpLibFileName);
__declspec(dllimport) void* __stdcall GetProcAddress(HANDLE hModule, const char* lpProcName);

#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define MEM_RELEASE 0x8000
#define PAGE_READWRITE 0x04

int _fltused = 0;

// ============================================================
// 手写 CRT 基础函数
// ============================================================
void* memcpy(void* dst, const void* src, uint64_t n) {
    unsigned char* d = (unsigned char*)dst;
    const unsigned char* s = (const unsigned char*)src;
    while (n--) *d++ = *s++;
    return dst;
}
void* memset(void* dst, int c, uint64_t n) {
    unsigned char* d = (unsigned char*)dst;
    while (n--) *d++ = (unsigned char)c;
    return dst;
}

#define MAX_TRACKED_ALLOCS 65536
static void* g_allocs[MAX_TRACKED_ALLOCS];
static int g_alloc_count = 0;

static void* no_crt_alloc(size_t size) {
    void* p = VirtualAlloc(0, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (p && g_alloc_count < MAX_TRACKED_ALLOCS) {
        g_allocs[g_alloc_count++] = p;
    }
    return p;
}
static void no_crt_free(void* p) {
    if (!p) return;
    for (int i = 0; i < g_alloc_count; i++) {
        if (g_allocs[i] == p) {
            g_allocs[i] = g_allocs[--g_alloc_count];
            VirtualFree(p, 0, MEM_RELEASE);
            return;
        }
    }
}
static void* no_crt_realloc(void* ptr, size_t old_size, size_t new_size) {
    void* new_ptr = no_crt_alloc(new_size);
    if (ptr && old_size > 0) {
        memcpy(new_ptr, ptr, old_size < new_size ? old_size : new_size);
    }
    return new_ptr;
}

static int my_strcmp(const char* a, const char* b) {
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}
static char* my_strcpy(char* dst, const char* src) {
    char* d = dst;
    while ((*d++ = *src++)) {}
    return dst;
}
static int64_t my_strlen(const char* s) {
    int64_t n = 0;
    while (*s++) n++;
    return n;
}
static char* my_strdup(const char* s) {
    int64_t len = my_strlen(s);
    char* p = (char*)no_crt_alloc(len + 1);
    if (!p) return 0;
    my_strcpy(p, s);
    return p;
}

static int itoa10(int64_t v, char* buf) {
    char tmp[32]; int n = 0, neg = 0;
    uint64_t mag;
    if (v < 0) { neg = 1; mag = (uint64_t)(-(v + 1)) + 1; }
    else mag = (uint64_t)v;
    if (mag == 0) tmp[n++] = '0';
    while (mag > 0) { tmp[n++] = '0' + (int)(mag % 10); mag /= 10; }
    int k = 0; if (neg) buf[k++] = '-';
    for (int i = n - 1; i >= 0; i--) buf[k++] = tmp[i];
    buf[k] = '\0';
    return k;
}
static char* my_itoa(int64_t v) {
    char buf[32]; int n = itoa10(v, buf); buf[n] = 0;
    return my_strdup(buf);
}

static int dtoa_fallback(double f, char* buf) {
    int n = 0, neg = 0;
    if (f < 0) { neg = 1; f = -f; }
    int64_t ip = (int64_t)f; double fp = f - (double)ip;
    if (neg) buf[n++] = '-';
    n += itoa10(ip, buf + n);
    if (fp > 1e-12) {
        buf[n++] = '.'; int digits = 15;
        for (int i = 0; i < digits; i++) {
            fp *= 10.0; int d = (int)fp; if (d > 9) d = 9;
            buf[n++] = '0' + d; fp -= d; if (fp < 1e-12) break;
        }
        while (n > 0 && buf[n - 1] == '0') n--;
        if (n > 0 && buf[n - 1] == '.') n++;
    } else { buf[n++] = '.'; buf[n++] = '0'; }
    buf[n] = '\0';
    return n;
}

// ---------- 最短 round-trip 浮点转字符串 ----------
static void write_str(const char* s, int64_t len);
static int64_t my_strlen(const char* s);
typedef char* (*gcvt_fn)(double, int, char*);
typedef double (*strtod_fn)(const char*, char**);
typedef double (*sqrt_fn_t)(double);
static gcvt_fn g_gcvt = 0;
static strtod_fn g_strtod = 0;
static sqrt_fn_t g_sqrt_fn = 0;
static int g_crt_state = 0;

static int load_ucrt(void) {
    if (g_crt_state != 0) return g_crt_state > 0;
    g_crt_state = -1;
    HANDLE h = LoadLibraryA("ucrtbase.dll");
    if (h) {
        g_gcvt = (gcvt_fn)GetProcAddress(h, "_gcvt");
        g_strtod = (strtod_fn)GetProcAddress(h, "strtod");
        g_sqrt_fn = (sqrt_fn_t)GetProcAddress(h, "sqrt");
        if (g_gcvt && g_strtod) g_crt_state = 1;
    }
    return g_crt_state > 0;
}

static uint64_t double_bits(double d) {
    union { double d; uint64_t u; } v;
    v.d = d;
    return v.u;
}

static int build_decimal(char* out, const int* digits, int n, int exp10,
                         int neg) {
    int k = 0;
    if (neg) out[k++] = '-';
    int pos = n + exp10;   // 小数点位置
    if (pos <= 0) {
        out[k++] = '0';
        out[k++] = '.';
        for (int i = 0; i < -pos; i++) out[k++] = '0';
        for (int i = 0; i < n; i++) out[k++] = (char)('0' + digits[i]);
    } else if (pos >= n) {
        for (int i = 0; i < n; i++) out[k++] = (char)('0' + digits[i]);
        for (int i = 0; i < pos - n; i++) out[k++] = '0';
        out[k++] = '.';
        out[k++] = '0';
    } else {
        for (int i = 0; i < pos; i++) out[k++] = (char)('0' + digits[i]);
        out[k++] = '.';
        for (int i = pos; i < n; i++) out[k++] = (char)('0' + digits[i]);
    }
    out[k] = 0;
    return k;
}

static int dtoa(double f, char* buf) {
    if (!load_ucrt()) return dtoa_fallback(f, buf);    if (f != f) { buf[0] = 'n'; buf[1] = 'a'; buf[2] = 'n'; buf[3] = 0; return 3; }
    if (f == 0.0) {
        int n = 0;
        if (double_bits(f) != 0) buf[n++] = '-';
        buf[n++] = '0'; buf[n++] = '.'; buf[n++] = '0'; buf[n] = 0;
        return n;
    }
    char s17[64];
    s17[0] = 0;
    g_gcvt(f, 17, s17);
    if (!s17[0]) return dtoa_fallback(f, buf);
    // 解析成 digits + 10 的指数
    int neg = 0, nd = 0, decpos = -1, eexp = 0, has_exp = 0;
    int digs[36];
    const char* p = s17;
    if (*p == '-') { neg = 1; p++; }
    for (; *p; p++) {
        if (*p == '.') { decpos = nd; continue; }
        if (*p >= '0' && *p <= '9') {
            if (nd < 34) digs[nd++] = *p - '0';
        } else if (*p == 'e' || *p == 'E') {
            has_exp = 1;
            p++;
            int esign = 1;
            if (*p == '+') p++;
            else if (*p == '-') { esign = -1; p++; }
            int ev = 0;
            while (*p >= '0' && *p <= '9') { ev = ev * 10 + (*p - '0'); p++; }
            eexp = esign * ev;
            break;
        }
    }
    if (has_exp || nd == 0) return dtoa_fallback(f, buf);
    if (decpos < 0) decpos = nd;
    // 去掉前导 0（"0.3" 这类），同时修正指数
    int zeros = 0;
    while (zeros < nd && digs[zeros] == 0) zeros++;
    if (zeros > 0) {
        for (int i = 0; i < nd - zeros; i++) digs[i] = digs[i + zeros];
        nd -= zeros;
    }
    if (nd == 0) return dtoa_fallback(f, buf);
    int e10 = decpos - zeros - nd;
    for (int k = 1; k <= 17 && k <= nd; k++) {
        int rd[34];
        for (int i = 0; i < k; i++) rd[i] = digs[i];
        int carry = (k < nd && digs[k] >= 5);
        for (int i = k - 1; i >= 0 && carry; i--) {
            if (rd[i] == 9) rd[i] = 0;
            else { rd[i]++; carry = 0; }
        }
        int expk = e10 + nd - k + (carry ? 1 : 0);
        if (carry) { rd[0] = 1; for (int i = 1; i < k; i++) rd[i] = 0; }
        char cand[64];
        build_decimal(cand, rd, k, expk, neg);
        if (double_bits(g_strtod(cand, 0)) == double_bits(f)) {
            int n = (int)my_strlen(cand);
            for (int i = 0; i <= n; i++) buf[i] = cand[i];
            return n;
        }
    }
    return dtoa_fallback(f, buf);
}
static char* my_dtoa(double f) {
    char buf[64]; int n = dtoa(f, buf); buf[n] = 0;
    return my_strdup(buf);
}

// ============================================================
// BigInt（带 shared）
// ============================================================
#define LIMB_BITS 32
#define LIMB_MASK 0xFFFFFFFFu

typedef struct {
    int64_t shared;          // ★ COW 共享计数
    int sign;
    uint32_t *limbs;
    size_t len;
    size_t cap;
} EclBigInt;

static void bi_init(EclBigInt *a) {
    a->shared = 1;
    a->sign = 0; a->limbs = NULL; a->len = 0; a->cap = 0;
}
static void bi_reserve(EclBigInt *a, size_t need) {
    if (a->cap >= need) return;
    size_t new_cap = a->cap ? a->cap : 4;
    while (new_cap < need) new_cap *= 2;
    a->limbs = (uint32_t*)no_crt_realloc(a->limbs, a->cap * sizeof(uint32_t), new_cap * sizeof(uint32_t));
    a->cap = new_cap;
}
static void bi_normalize(EclBigInt *a) {
    while (a->len > 0 && a->limbs[a->len - 1] == 0) a->len--;
    if (a->len == 0) a->sign = 0;
}
static void bi_set_u64(EclBigInt *a, uint64_t v) {
    if (v == 0) { a->len = 0; a->sign = 0; return; }
    a->len = 0;
    while (v) {
        bi_reserve(a, a->len + 1);
        a->limbs[a->len++] = (uint32_t)(v & LIMB_MASK);
        v >>= LIMB_BITS;
    }
    a->sign = 1;
}
static void bi_set_i64(EclBigInt *a, int64_t v) {
    if (v == 0) { bi_set_u64(a, 0); return; }
    if (v > 0) bi_set_u64(a, (uint64_t)v);
    else {
        bi_set_u64(a, (uint64_t)(-(v + 1)) + 1);
        a->sign = -1;
    }
}
static int cmp_mag(const uint32_t *a, size_t an, const uint32_t *b, size_t bn) {
    if (an != bn) return an < bn ? -1 : 1;
    for (size_t i = an; i-- > 0; ) {
        if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    }
    return 0;
}
static void add_mag(const uint32_t *a, size_t an, const uint32_t *b, size_t bn, EclBigInt *out) {
    const uint32_t *lo = an >= bn ? a : b;
    const uint32_t *sh = an >= bn ? b : a;
    size_t ln = an >= bn ? an : bn;
    size_t sn = an >= bn ? bn : an;
    bi_reserve(out, ln + 1);
    uint64_t carry = 0;
    for (size_t i = 0; i < ln; i++) {
        uint64_t x = lo[i];
        uint64_t y = i < sn ? sh[i] : 0;
        uint64_t s = x + y + carry;
        out->limbs[i] = (uint32_t)(s & LIMB_MASK);
        carry = s >> LIMB_BITS;
    }
    out->len = ln;
    if (carry) out->limbs[out->len++] = (uint32_t)carry;
    out->sign = 1;
}
static void sub_mag(const uint32_t *a, size_t an, const uint32_t *b, size_t bn, EclBigInt *out) {
    bi_reserve(out, an);
    uint64_t borrow = 0;
    for (size_t i = 0; i < an; i++) {
        uint64_t x = a[i];
        uint64_t y = i < bn ? b[i] : 0;
        uint64_t d = x - y - borrow;
        out->limbs[i] = (uint32_t)(d & LIMB_MASK);
        borrow = (d >> 63) & 1;
    }
    out->len = an;
    out->sign = 1;
}
static void bi_add(EclBigInt *out, const EclBigInt *a, const EclBigInt *b) {
    if (a->sign == 0) { *out = *b; return; }
    if (b->sign == 0) { *out = *a; return; }
    if (a->sign == b->sign) {
        add_mag(a->limbs, a->len, b->limbs, b->len, out);
        out->sign = a->sign;
    } else {
        int c = cmp_mag(a->limbs, a->len, b->limbs, b->len);
        if (c == 0) { out->len = 0; out->sign = 0; }
        else if (c > 0) { sub_mag(a->limbs, a->len, b->limbs, b->len, out); out->sign = a->sign; }
        else { sub_mag(b->limbs, b->len, a->limbs, a->len, out); out->sign = b->sign; }
    }
    bi_normalize(out);
}
static void bi_mul(EclBigInt *out, const EclBigInt *a, const EclBigInt *b) {
    if (a->sign == 0 || b->sign == 0) { out->len = 0; out->sign = 0; return; }
    size_t rn = a->len + b->len;
    bi_reserve(out, rn + 2);
    memset(out->limbs, 0, (rn + 2) * sizeof(uint32_t));
    for (size_t i = 0; i < a->len; i++) {
        uint64_t carry = 0;
        for (size_t j = 0; j < b->len; j++) {
            uint64_t cur = (uint64_t)a->limbs[i] * b->limbs[j] + out->limbs[i + j] + carry;
            out->limbs[i + j] = (uint32_t)(cur & LIMB_MASK);
            carry = cur >> LIMB_BITS;
        }
        size_t k = i + b->len;
        while (carry) {
            uint64_t s = (uint64_t)out->limbs[k] + carry;
            out->limbs[k] = (uint32_t)(s & LIMB_MASK);
            carry = s >> LIMB_BITS;
            k++;
        }
    }
    out->len = rn + 1;
    out->sign = a->sign * b->sign;
    bi_normalize(out);
}

static char* bi_to_dec(const EclBigInt *a) {
    if (a->sign == 0) return my_strdup("0");
    EclBigInt tmp;
    bi_init(&tmp);
    bi_reserve(&tmp, a->len);
    for (size_t i = 0; i < a->len; i++) {
        tmp.limbs[i] = a->limbs[i];
    }
    tmp.len = a->len;
    tmp.sign = 1;

    size_t cap = 8, n = 0;
    uint32_t* chunks = (uint32_t*)no_crt_alloc(cap * sizeof(uint32_t));
    while (tmp.len > 0) {
        uint64_t rem = 0;
        for (size_t i = tmp.len; i-- > 0; ) {
            uint64_t cur = (rem << LIMB_BITS) | tmp.limbs[i];
            tmp.limbs[i] = (uint32_t)(cur / 1000000000ULL);
            rem = cur % 1000000000ULL;
        }
        bi_normalize(&tmp);
        if (n == cap) {
            cap *= 2;
            uint32_t* new_chunks = (uint32_t*)no_crt_alloc(cap * sizeof(uint32_t));
            memcpy(new_chunks, chunks, n * sizeof(uint32_t));
            chunks = new_chunks;
        }
        chunks[n++] = (uint32_t)rem;
    }

    char buf[32];
    int hi_len = itoa10(chunks[n - 1], buf);
    buf[hi_len] = '\0';
    size_t total = (a->sign < 0 ? 1 : 0) + (size_t)hi_len + (n - 1) * 9;
    char* out = (char*)no_crt_alloc(total + 1);
    size_t pos = 0;
    if (a->sign < 0) out[pos++] = '-';
    pos += (size_t)itoa10(chunks[n - 1], out + pos);
    for (size_t i = n - 1; i-- > 0; ) {
        int wlen = itoa10(chunks[i], buf);
        buf[wlen] = '\0';
        for (int j = 0; j < 9 - wlen; j++) out[pos++] = '0';
        my_strcpy(out + pos, buf);
        pos += (size_t)wlen;
    }
    out[pos] = '\0';
    return out;
}

// ============================================================
// Value 类型
// ============================================================
typedef enum {
    VAL_INT      = 0,
    VAL_FLOAT    = 1,
    VAL_STR      = 2,
    VAL_NULL     = 3,
    VAL_NONE     = 4,
    VAL_LIST     = 5,
    VAL_DICT     = 6,
    VAL_RANGE    = 7,
    VAL_CODE     = 8,
    VAL_FUNCTION = 9,
    VAL_CLASS    = 10,
    VAL_INSTANCE = 11,
    VAL_BOUND    = 12,
    VAL_BIGINT   = 13,
    VAL_TUPLE    = 14,
    VAL_UNKNOWN  = 15,
    VAL_EXC      = 16,
    VAL_BASEPROXY = 17,
    VAL_EXCLASS  = 18,
    VAL_BOOL     = 19,
    VAL_I64      = 20,
    VAL_DOUBLE   = 21,
    VAL_I32      = 22,
    VAL_REF      = 23,
    VAL_BUFFER   = 24,
    VAL_STRUCT   = 25,
    VAL_WSTR     = 26,
} ValTag;

#define NORMALIZE_VALUE(v) do { \
    if ((v)->tag == VAL_BOOL || (v)->tag == VAL_I64 || (v)->tag == VAL_I32) (v)->tag = VAL_INT; \
    else if ((v)->tag == VAL_DOUBLE) (v)->tag = VAL_FLOAT; \
} while (0)

typedef struct {
    int64_t tag;
    union {
        int64_t i;
        double  f;
        char*   s;
        void*   ptr;
    };
} Value;

typedef struct EclRef { Value* target; } EclRef;
typedef struct EclBuffer { unsigned char* data; int64_t size; } EclBuffer;

typedef struct EclStruct {
    char* type_name;
    unsigned char* data;
    int64_t size;
    int owned;
} EclStruct;

static void print_value_inner(Value* v, int quote_str);
static void write_str(const char* s, int64_t len);
void runtime_raise_error(const char* msg);
void runtime_convert(Value* out, Value* v, const char* type);
void runtime_len(Value* result, Value* v);
void runtime_new_buffer(Value* result, Value* size);
void runtime_buffer_address(Value* result, Value* buf);
void runtime_struct_new(Value* result, const char* name);
void runtime_struct_get_field(Value* out, Value* st, const char* name);
void runtime_struct_set_field(Value* st, const char* name, Value* v);
void runtime_struct_address(Value* result, Value* st);
void runtime_call(Value* result, Value* f, Value** args, int argc);
void runtime_call_kw(Value* result, Value* f, Value** args, int64_t* pair);
void runtime_get_attr(Value* out, Value* obj, const char* name);
void runtime_str(Value* result, Value* v);
void runtime_int(Value* result, Value* v);
void runtime_float(Value* result, Value* v);
void runtime_lt(Value* result, Value* a, Value* b);
void runtime_add(Value* result, Value* a, Value* b);
void runtime_pow(Value* result, Value* a, Value* b);
void runtime_to_tuple(Value* out, Value* src);
static double my_sqrt(double x);

// ============================================================
// 变量表
// ============================================================
#define HASH_SIZE 256
#define VALUE_POOL_SIZE 1048576
struct VarEntry { char* name; Value* val; struct VarEntry* next; };
static struct VarEntry* hash_table[HASH_SIZE];
static Value value_pool[VALUE_POOL_SIZE];
static int value_pool_count = 0;

static Value* alloc_value(void) {
    if (value_pool_count >= VALUE_POOL_SIZE) ExitProcess(1);
    return &value_pool[value_pool_count++];
}
static unsigned int hash_str(const char* s) {
    unsigned int h = 5381; while (*s) h = ((h << 5) + h) + (unsigned char)*s++;
    return h % HASH_SIZE;
}
Value* get_var_slot(const char* name) {
    unsigned int h = hash_str(name);
    struct VarEntry* e = hash_table[h];
    while (e) { if (my_strcmp(e->name, name) == 0) return e->val; e = e->next; }
    // 内置异常类型
    {
        static const char* exc_names[] = {
            "Exception", "ValueError", "TypeError", "IndexError", "KeyError",
            "ZeroDivisionError", "RuntimeError", "NameError", "AttributeError",
            "ConstChangingError", "SyntaxError", 0
        };
        for (int i = 0; exc_names[i]; i++) {
            if (my_strcmp(name, exc_names[i]) == 0) {
                Value* v = alloc_value();
                v->tag = VAL_EXCLASS;
                v->s = my_strdup(name);
                return v;
            }
        }
    }
    struct VarEntry* ne = (struct VarEntry*)no_crt_alloc(sizeof(struct VarEntry));
    ne->name = my_strdup(name); ne->val = alloc_value();
    ne->val->tag = VAL_UNKNOWN; ne->val->i = 0;
    ne->next = hash_table[h]; hash_table[h] = ne;
    return ne->val;
}

static Value* deref_ptr(Value* p) {
    for (int guard = 0; p && p->tag == VAL_REF && guard < 100000; guard++) {
        p = ((EclRef*)p->ptr)->target;
    }
    return p;
}

Value* get_var(const char* name) {
    return deref_ptr(get_var_slot(name));
}

void runtime_make_ref_slot(Value* alias_slot, Value* target_slot) {
    EclRef* r = (EclRef*)no_crt_alloc(sizeof(EclRef));
    r->target = target_slot;
    alias_slot->tag = VAL_REF;
    alias_slot->ptr = r;
}

void runtime_make_ref(const char* alias, const char* src) {
    runtime_make_ref_slot(get_var_slot(alias), get_var_slot(src));
}

Value* runtime_deref(Value* slot) {
    return deref_ptr(slot);
}

// ============================================================
// 类 / 容器（带 shared）
// ============================================================
typedef struct EclSig {
    int64_t count;
    const char** names;
    const char** types;
    int64_t varargs_index;
    int64_t kwargs_index;
} EclSig;
typedef struct EclMethod { char* name; void* code; int nparams; EclSig* sig; struct EclMethod* next; } EclMethod;
typedef struct EclClass { char* name; EclMethod* methods; struct EclClass** bases; int base_count; } EclClass;
typedef struct EclAttr { char* name; Value value; struct EclAttr* next; } EclAttr;
typedef struct EclInstance { int64_t shared; EclClass* cls; EclAttr* attrs; } EclInstance;
typedef struct EclBoundMethod { EclInstance* self; EclMethod* m; void* builtin; Value self_val; } EclBoundMethod;
typedef struct EclExc { char* type; char* msg; } EclExc;
typedef struct EclBaseProxy { EclInstance* inst; EclClass* base; } EclBaseProxy;
typedef struct EclCode { void* code; } EclCode;
typedef void (*EclFuncPtr)(Value* result, Value** args, int argc);

typedef struct EclList { int64_t shared; int64_t len, cap; Value* items; } EclList;
typedef struct EclDictEntry { Value key; Value val; struct EclDictEntry* next; struct EclDictEntry* order_next; } EclDictEntry;
typedef struct EclDict { int64_t shared; int64_t size; int64_t bucket_count; EclDictEntry** buckets; EclDictEntry* head; EclDictEntry* tail; } EclDict;

// ============================================================
// 前置声明
// ============================================================
static void value_share(Value* v);
static void value_release(Value* v);
static int64_t dll_to_int(Value* v);
static double dll_to_double(Value* v);
static char* dll_utf16_to_utf8(const wchar_t* w);
static wchar_t* dll_utf8_to_utf16(const char* s);
void runtime_shutdown(void);

// ============================================================
// 哈希表
// ============================================================
#define DICT_INIT_BUCKETS 8

static unsigned int value_hash(Value v) {
    if (v.tag == VAL_BOOL || v.tag == VAL_I64 || v.tag == VAL_I32) v.tag = VAL_INT;
    if (v.tag == VAL_DOUBLE) v.tag = VAL_FLOAT;
    switch (v.tag) {
        case VAL_INT: return (unsigned int)(v.i ^ (v.i >> 32));
        case VAL_FLOAT: {
            uint64_t bits; memcpy(&bits, &v.f, 8);
            return (unsigned int)(bits ^ (bits >> 32));
        }
        case VAL_STR: return hash_str(v.s);
        case VAL_NULL: case VAL_NONE: case VAL_UNKNOWN: return 0;        case VAL_TUPLE: {
            EclList* l = (EclList*)v.ptr;
            unsigned int h = 2166136261u;
            for (int64_t i = 0; i < l->len; i++) {
                h = (h ^ value_hash(l->items[i])) * 16777619u;
            }
            return h;
        }
        default: return (unsigned int)(((uintptr_t)v.ptr) >> 3);
    }
}
static int value_eq_raw(Value a, Value b) {
    if (a.tag == VAL_BOOL || a.tag == VAL_I64 || a.tag == VAL_I32) a.tag = VAL_INT;
    if (b.tag == VAL_BOOL || b.tag == VAL_I64 || b.tag == VAL_I32) b.tag = VAL_INT;
    if (a.tag == VAL_DOUBLE) a.tag = VAL_FLOAT;
    if (b.tag == VAL_DOUBLE) b.tag = VAL_FLOAT;
    if (a.tag != b.tag) return 0;
    switch (a.tag) {
        case VAL_INT: return a.i == b.i;
        case VAL_FLOAT: return a.f == b.f;
        case VAL_STR: return my_strcmp(a.s, b.s) == 0;
        case VAL_NULL: case VAL_NONE: case VAL_UNKNOWN: return 1;
        case VAL_TUPLE: {
            EclList* x = (EclList*)a.ptr;
            EclList* y = (EclList*)b.ptr;
            if (x->len != y->len) return 0;
            for (int64_t i = 0; i < x->len; i++) {
                if (!value_eq_raw(x->items[i], y->items[i])) return 0;
            }
            return 1;
        }
        default: return a.ptr == b.ptr;
    }
}

static EclDict* dict_new() {
    EclDict* d = (EclDict*)no_crt_alloc(sizeof(EclDict));
    d->shared = 1;
    d->size = 0;
    d->bucket_count = DICT_INIT_BUCKETS;
    d->buckets = (EclDictEntry**)no_crt_alloc(DICT_INIT_BUCKETS * sizeof(EclDictEntry*));
    memset(d->buckets, 0, DICT_INIT_BUCKETS * sizeof(EclDictEntry*));
    d->head = 0;
    d->tail = 0;
    return d;
}
static EclDictEntry* dict_find(EclDict* d, Value key) {
    unsigned int h = value_hash(key) % (unsigned int)d->bucket_count;
    EclDictEntry* e = d->buckets[h];
    while (e) {
        if (value_eq_raw(e->key, key)) return e;
        e = e->next;
    }
    return 0;
}
static void dict_rehash(EclDict* d, int64_t new_count) {
    EclDictEntry** new_buckets = (EclDictEntry**)no_crt_alloc(new_count * sizeof(EclDictEntry*));
    memset(new_buckets, 0, new_count * sizeof(EclDictEntry*));
    for (int64_t i = 0; i < d->bucket_count; i++) {
        EclDictEntry* e = d->buckets[i];
        while (e) {
            EclDictEntry* next = e->next;
            unsigned int h = value_hash(e->key) % (unsigned int)new_count;
            e->next = new_buckets[h];
            new_buckets[h] = e;
            e = next;
        }
    }
    d->buckets = new_buckets;
    d->bucket_count = new_count;
}
static void dict_put_raw(EclDict* d, Value key, Value val) {
    if (d->size * 4 > d->bucket_count * 3) {
        dict_rehash(d, d->bucket_count * 2);
    }
    unsigned int h = value_hash(key) % (unsigned int)d->bucket_count;
    for (EclDictEntry* e = d->buckets[h]; e; e = e->next) {
        if (value_eq_raw(e->key, key)) {
            value_release(&e->val);
            e->val = val;
            value_share(&val);
            return;
        }
    }
    EclDictEntry* e = (EclDictEntry*)no_crt_alloc(sizeof(EclDictEntry));
    e->key = key; e->val = val;
    value_share(&e->key); value_share(&e->val);
    e->next = d->buckets[h];
    d->buckets[h] = e;
    e->order_next = 0;
    if (d->tail) d->tail->order_next = e; else d->head = e;
    d->tail = e;
    d->size++;
}

static EclList* list_new() {
    EclList* l = (EclList*)no_crt_alloc(sizeof(EclList));
    l->shared = 1; l->len = 0; l->cap = 0; l->items = 0;
    return l;
}
static void list_reserve(EclList* l, int64_t need) {
    if (need <= l->cap) return;
    int64_t nc = l->cap ? l->cap * 2 : 8;
    while (nc < need) nc *= 2;
    Value* ni = (Value*)no_crt_alloc(nc * sizeof(Value));
    for (int64_t i = 0; i < l->len; i++) ni[i] = l->items[i];
    l->items = ni; l->cap = nc;
}
static void list_push_raw(EclList* l, Value v) {
    list_reserve(l, l->len + 1);
    l->items[l->len++] = v;
    value_share(&v);
}

// ============================================================
// value_share / value_release / COW
// ============================================================
static void value_share(Value* v) {
    // 无引用计数：所有容器都直接共享/由程序员负责释放
    (void)v;
}

static void value_release(Value* v) {
    // 无 GC、无引用计数：释放由退出时的 runtime_shutdown 统一处理
    (void)v;
}

static void list_cow(EclList** lp) {
    EclList* l = *lp;
    if (l->shared <= 1) return;
    EclList* copy = (EclList*)no_crt_alloc(sizeof(EclList));
    copy->shared = 1;
    copy->len = l->len;
    copy->cap = l->len;
    copy->items = (Value*)no_crt_alloc(l->len * sizeof(Value));
    for (int64_t i = 0; i < l->len; i++) {
        copy->items[i] = l->items[i];
        value_share(&copy->items[i]);
    }
    l->shared--;
    *lp = copy;
}

static void dict_cow(EclDict** dp) {
    EclDict* d = *dp;
    if (d->shared <= 1) return;
    EclDict* copy = (EclDict*)no_crt_alloc(sizeof(EclDict));
    copy->shared = 1;
    copy->size = 0;
    copy->bucket_count = d->bucket_count;
    copy->buckets = (EclDictEntry**)no_crt_alloc(d->bucket_count * sizeof(EclDictEntry*));
    memset(copy->buckets, 0, d->bucket_count * sizeof(EclDictEntry*));
    copy->head = 0;
    copy->tail = 0;
    for (int64_t b = 0; b < d->bucket_count; b++) {
        for (EclDictEntry* e = d->buckets[b]; e; e = e->next) {
            EclDictEntry* ne = (EclDictEntry*)no_crt_alloc(sizeof(EclDictEntry));
            ne->key = e->key; ne->val = e->val;
            value_share(&ne->key); value_share(&ne->val);
            unsigned int h = value_hash(e->key) % (unsigned int)copy->bucket_count;
            ne->next = copy->buckets[h];
            copy->buckets[h] = ne;
            ne->order_next = 0;
            if (copy->tail) copy->tail->order_next = ne; else copy->head = ne;
            copy->tail = ne;
            copy->size++;
        }
    }
    d->shared--;
    *dp = copy;
}

static void instance_cow(EclInstance** ip) {
    EclInstance* inst = *ip;
    if (inst->shared <= 1) return;
    EclInstance* copy = (EclInstance*)no_crt_alloc(sizeof(EclInstance));
    copy->shared = 1;
    copy->cls = inst->cls;
    copy->attrs = 0;
    for (EclAttr* a = inst->attrs; a; a = a->next) {
        EclAttr* na = (EclAttr*)no_crt_alloc(sizeof(EclAttr));
        na->name = a->name;
        na->value = a->value;
        value_share(&na->value);
        na->next = copy->attrs;
        copy->attrs = na;
    }
    inst->shared--;
    *ip = copy;
}

// 深拷贝：a <- b 语义
static Value value_clone(Value* src) {
    Value v = *src;
    if (v.tag == VAL_REF) {
        Value* tail = deref_ptr(src);
        return value_clone(tail);
    }
    if (v.tag == VAL_LIST || v.tag == VAL_TUPLE) {
        EclList* old = (EclList*)v.ptr;
        EclList* out = list_new();
        for (int64_t i = 0; i < old->len; i++) {
            Value item = value_clone(&old->items[i]);
            list_push_raw(out, item);
        }
        v.ptr = out;
    } else if (v.tag == VAL_DICT) {
        EclDict* old = (EclDict*)v.ptr;
        EclDict* out = dict_new();
        for (EclDictEntry* e = old->head; e; e = e->order_next) {
            Value key = value_clone(&e->key);
            Value val = value_clone(&e->val);
            dict_put_raw(out, key, val);
        }
        v.ptr = out;
    } else if (v.tag == VAL_INSTANCE) {
        EclInstance* old = (EclInstance*)v.ptr;
        EclInstance* out = (EclInstance*)no_crt_alloc(sizeof(EclInstance));
        out->shared = 1;
        out->cls = old->cls;
        out->attrs = 0;
        for (EclAttr* a = old->attrs; a; a = a->next) {
            EclAttr* na = (EclAttr*)no_crt_alloc(sizeof(EclAttr));
            na->name = a->name;
            na->value = value_clone(&a->value);
            na->next = out->attrs;
            out->attrs = na;
        }
        v.ptr = out;
    } else if (v.tag == VAL_BIGINT) {
        EclBigInt* old = (EclBigInt*)v.ptr;
        EclBigInt* out = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
        bi_init(out);
        out->sign = old->sign;
        out->len = old->len;
        bi_reserve(out, old->len);
        for (size_t i = 0; i < old->len; i++) out->limbs[i] = old->limbs[i];
        out->cap = old->len;
        v.ptr = out;
    } else if (v.tag == VAL_STR) {
        v.s = my_strdup(v.s);
    } else if (v.tag == VAL_BUFFER) {
        EclBuffer* old = (EclBuffer*)v.ptr;
        EclBuffer* out = (EclBuffer*)no_crt_alloc(sizeof(EclBuffer));
        out->size = old->size;
        out->data = (unsigned char*)no_crt_alloc((size_t)old->size);
        memcpy(out->data, old->data, (uint64_t)old->size);
        v.ptr = out;
    } else if (v.tag == VAL_STRUCT) {
        EclStruct* old = (EclStruct*)v.ptr;
        EclStruct* out = (EclStruct*)no_crt_alloc(sizeof(EclStruct));
        out->type_name = my_strdup(old->type_name);
        out->size = old->size;
        out->data = (unsigned char*)no_crt_alloc((size_t)old->size);
        memcpy(out->data, old->data, (uint64_t)old->size);
        out->owned = 1;
        v.ptr = out;
    } else if (v.tag == VAL_WSTR) {
        wchar_t* old = (wchar_t*)v.ptr;
        int64_t n = 0;
        while (old && old[n]) n++;
        wchar_t* out = (wchar_t*)no_crt_alloc((size_t)(n + 1) * 2);
        for (int64_t i = 0; i <= n; i++) out[i] = old[i];
        v.ptr = out;
    }
    return v;
}

// free(): 物理释放 + 逻辑置空（无 GC/引用计数）
static void value_destroy(Value* v) {
    if (!v) return;
    if (v->tag == VAL_REF) {
        no_crt_free(v->ptr);
    } else if (v->tag == VAL_LIST || v->tag == VAL_TUPLE) {
        EclList* l = (EclList*)v->ptr;
        if (!l) return;
        for (int64_t i = 0; i < l->len; i++) value_destroy(&l->items[i]);
        no_crt_free(l->items);
        no_crt_free(l);
    } else if (v->tag == VAL_DICT) {
        EclDict* d = (EclDict*)v->ptr;
        if (!d) return;
        for (int64_t b = 0; b < d->bucket_count; b++) {
            EclDictEntry* e = d->buckets[b];
            while (e) {
                EclDictEntry* next = e->next;
                value_destroy(&e->key);
                value_destroy(&e->val);
                no_crt_free(e);
                e = next;
            }
        }
        no_crt_free(d->buckets);
        no_crt_free(d);
    } else if (v->tag == VAL_INSTANCE) {
        EclInstance* inst = (EclInstance*)v->ptr;
        if (!inst) return;
        EclAttr* a = inst->attrs;
        while (a) {
            EclAttr* next = a->next;
            value_destroy(&a->value);
            no_crt_free(a);
            a = next;
        }
        no_crt_free(inst);
    } else if (v->tag == VAL_BIGINT) {
        EclBigInt* bi = (EclBigInt*)v->ptr;
        if (!bi) return;
        no_crt_free(bi->limbs);
        no_crt_free(bi);
    } else if (v->tag == VAL_STR) {
        no_crt_free(v->s);
    } else if (v->tag == VAL_BUFFER) {
        EclBuffer* b = (EclBuffer*)v->ptr;
        if (b) {
            no_crt_free(b->data);
            no_crt_free(b);
        }
    } else if (v->tag == VAL_STRUCT) {
        EclStruct* s = (EclStruct*)v->ptr;
        if (s) {
            if (s->owned) no_crt_free(s->data);
            no_crt_free(s->type_name);
            no_crt_free(s);
        }
    } else if (v->tag == VAL_WSTR) {
        no_crt_free(v->ptr);
    }
}

// ============================================================
// 输出
// ============================================================
static void write_handle(HANDLE h, const char* s, int64_t len) {
    if (len <= 0) return;
    DWORD written = 0;
    DWORD mode = 0;
    // 控制台走 WriteConsoleW，避免依赖控制台代码页（解决中文乱码）；
    // 重定向到文件/管道时保持 UTF-8 字节输出，和解释器逐字节一致。
    if (GetConsoleMode(h, &mode)) {
        int wlen = MultiByteToWideChar(CP_UTF8, 0, s, (int)len, 0, 0);
        if (wlen > 0) {
            wchar_t* w = (wchar_t*)no_crt_alloc((size_t)wlen * 2);
            if (w) {
                MultiByteToWideChar(CP_UTF8, 0, s, (int)len, w, wlen);
                WriteConsoleW(h, w, (DWORD)wlen, &written, 0);
                return;
            }
        }
    }
    WriteFile(h, s, (DWORD)len, &written, 0);
}
static void write_str(const char* s, int64_t len) {
    write_handle(GetStdHandle(STD_OUTPUT_HANDLE), s, len);
}
static void write_utf8_as_gbk(const char* s, int64_t len) {
    write_str(s, len);
}

static int is_dunder(const char* s) {
    int64_t n = my_strlen(s);
    return n >= 4 && s[0] == '_' && s[1] == '_' && s[n-1] == '_' && s[n-2] == '_';
}
static EclMethod* find_method(EclClass* cls, const char* name) {
    if (!cls) return 0;
    for (EclMethod* m = cls->methods; m; m = m->next)
        if (my_strcmp(m->name, name) == 0) return m;
    for (int i = 0; i < cls->base_count; i++) {
        EclMethod* found = find_method(cls->bases[i], name);
        if (found) return found;
    }
    return 0;
}
static EclAttr* find_attr(EclInstance* inst, const char* name) {
    for (EclAttr* a = inst->attrs; a; a = a->next) if (my_strcmp(a->name, name) == 0) return a;
    return 0;
}
static EclBoundMethod* make_bound(EclInstance* self, EclMethod* m) {
    EclBoundMethod* bm = (EclBoundMethod*)no_crt_alloc(sizeof(EclBoundMethod));
    bm->self = self; bm->m = m; bm->builtin = 0;
    return bm;
}

static void print_value_inner(Value* v, int quote_str) {
    char buf[64]; int n = 0;
    switch (v->tag) {
        case VAL_INT: n = itoa10(v->i, buf); write_str(buf, n); break;
        case VAL_I64: case VAL_I32: n = itoa10(v->i, buf); write_str(buf, n); break;
        case VAL_BOOL: if (v->i) write_str("True", 4); else write_str("False", 5); break;
        case VAL_FLOAT: n = dtoa(v->f, buf); write_str(buf, n); break;
        case VAL_DOUBLE:
            if (v->f == (double)(int64_t)v->f) {
                n = itoa10((int64_t)v->f, buf);
            } else {
                n = dtoa(v->f, buf);
            }
            write_str(buf, n); break;
        case VAL_STR:
            if (quote_str) write_str("'", 1);
            write_utf8_as_gbk(v->s, my_strlen(v->s));
            if (quote_str) write_str("'", 1);
            break;
        case VAL_BUFFER: {
            char b[40]; int k = itoa10(((EclBuffer*)v->ptr)->size, b);
            write_str("<buffer ", 8); write_str(b, k); write_str(">", 1); break;
        }
        case VAL_STRUCT: {
            const char* n = ((EclStruct*)v->ptr)->type_name;
            write_str("<", 1); write_str(n, my_strlen(n)); write_str(">", 1); break;
        }
        case VAL_WSTR: {
            char* s = dll_utf16_to_utf8((wchar_t*)v->ptr);
            write_utf8_as_gbk(s, my_strlen(s));
            no_crt_free(s);
            break;
        }
        case VAL_NULL: write_str("null", 4); break;
        case VAL_NONE: write_str("none", 4); break;
        case VAL_UNKNOWN: write_str("unknown", 7); break;
        case VAL_CODE: write_str("<code>", 6); break;
        case VAL_EXC: {
            EclExc* e = (EclExc*)v->ptr;
            write_str(e->msg, my_strlen(e->msg)); break;
        }
        case VAL_BASEPROXY: {
            EclBaseProxy* p = (EclBaseProxy*)v->ptr;
            write_str("<base ", 6); write_str(p->base->name, my_strlen(p->base->name));
            write_str(">", 1); break;
        }
        case VAL_EXCLASS: {
            write_str("<class ", 7); write_str(v->s, my_strlen(v->s));
            write_str(">", 1); break;
        }
        case VAL_FUNCTION: write_str("<function>", 10); break;
        case VAL_BIGINT: {
            char* s = bi_to_dec((EclBigInt*)v->ptr);
            write_str(s, my_strlen(s));
            break;
        }
        case VAL_CLASS: {
            EclClass* c = (EclClass*)v->ptr;
            write_str("<class ", 7); write_str(c->name, my_strlen(c->name)); write_str(">", 1); break;
        }
        case VAL_INSTANCE: {
            EclInstance* inst = (EclInstance*)v->ptr;
            EclMethod* m = find_method(inst->cls, "__str__");
            if (m) {
                Value r; Value self; self.tag = VAL_INSTANCE; self.ptr = inst;
                Value* args[1]; args[0] = &self;
                ((EclFuncPtr)m->code)(&r, args, 1);
                print_value_inner(&r, quote_str); return;
            }
            write_str("<", 1); write_str(inst->cls->name, my_strlen(inst->cls->name)); write_str(" object>", 8); break;
        }
        case VAL_BOUND: write_str("<bound method>", 14); break;
        case VAL_LIST: {
            EclList* l = (EclList*)v->ptr;
            write_str("[", 1);
            for (int64_t i = 0; i < l->len; i++) {
                if (i) write_str(", ", 2);
                print_value_inner(&l->items[i], 1);
            }
            write_str("]", 1); break;
        }
        case VAL_TUPLE: {
            EclList* l = (EclList*)v->ptr;
            write_str("(", 1);
            for (int64_t i = 0; i < l->len; i++) {
                if (i) write_str(", ", 2);
                print_value_inner(&l->items[i], 1);
            }
            if (l->len == 1) write_str(",", 1);
            write_str(")", 1); break;
        }
        case VAL_DICT: {
            EclDict* d = (EclDict*)v->ptr;
            write_str("{", 1); int first = 1;
            for (EclDictEntry* e = d->head; e; e = e->order_next) {
                if (!first) write_str(", ", 2); first = 0;
                print_value_inner(&e->key, 1);
                write_str(": ", 2);
                print_value_inner(&e->val, 1);
            }
            write_str("}", 1); break;
        }
        default: write_str("<value>", 7); break;
    }
}

void runtime_print(Value* v) { print_value_inner(v, 0); write_str("\n", 1); }
void runtime_write(Value* v) { print_value_inner(v, 0); }
void runtime_write_space(void) { write_str(" ", 1); }

int64_t runtime_truthy(Value* v) {
    switch (v->tag) {
        case VAL_INT: return v->i != 0;
        case VAL_I64: case VAL_I32: return v->i != 0;
        case VAL_BOOL: return v->i != 0;
        case VAL_FLOAT: return v->f != 0.0;
        case VAL_DOUBLE: return v->f != 0.0;
        case VAL_STR: return v->s && v->s[0] != '\0';
        case VAL_BUFFER: return ((EclBuffer*)v->ptr)->size != 0;
        case VAL_STRUCT: return 1;
        case VAL_WSTR: return v->ptr && ((wchar_t*)v->ptr)[0] != 0;
        case VAL_NULL: return 0; case VAL_NONE: return 0; case VAL_UNKNOWN: return 0;
        case VAL_LIST: return ((EclList*)v->ptr)->len != 0;
        case VAL_TUPLE: return ((EclList*)v->ptr)->len != 0;
        case VAL_DICT: return ((EclDict*)v->ptr)->size != 0;
        case VAL_BIGINT: return ((EclBigInt*)v->ptr)->len != 0;
        default: return 1;
    }
}

// ============================================================
// BigInt 工具
// ============================================================
static EclBigInt* make_big_i64(int64_t v) {
    EclBigInt* b = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
    bi_init(b); bi_set_i64(b, v);
    return b;
}

void runtime_bigint_from_dec(Value* out, const char* digits) {
    int neg = 0;
    if (*digits == '-') { neg = 1; digits++; }
    EclBigInt* acc = make_big_i64(0);
    EclBigInt* ten = make_big_i64(10);
    while (*digits) {
        int d = *digits - '0';
        if (d < 0 || d > 9) { digits++; continue; }
        EclBigInt* t1 = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
        bi_init(t1);
        bi_mul(t1, acc, ten);
        EclBigInt* dv = make_big_i64(d);
        EclBigInt* t2 = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
        bi_init(t2);
        bi_add(t2, t1, dv);
        acc = t2;
        digits++;
    }
    if (neg && acc->len > 0) acc->sign = -1;
    out->tag = VAL_BIGINT;
    out->ptr = acc;
}
static EclBigInt* make_big_from_val(Value* v) {
    EclBigInt* b = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
    bi_init(b);
    if (v->tag == VAL_BIGINT) {
        EclBigInt* src = (EclBigInt*)v->ptr;
        bi_reserve(b, src->len);
        for (size_t i = 0; i < src->len; i++) b->limbs[i] = src->limbs[i];
        b->len = src->len;
        b->sign = src->sign;
    } else if (v->tag == VAL_INT) {
        bi_set_i64(b, v->i);
    } else {
        bi_set_i64(b, 0);
    }
    return b;
}

void runtime_to_bi(Value* result, Value* v) {
    if (v->tag == VAL_BIGINT) {
        *result = *v;
        value_share(result);
        return;
    }
    if (v->tag == VAL_INT) {
        EclBigInt* b = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
        bi_init(b); bi_set_i64(b, v->i);
        result->tag = VAL_BIGINT; result->ptr = b;
        return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

// ============================================================
// 算术运算
// ============================================================
static double val_double(Value* v) {
    if (v->tag == VAL_FLOAT || v->tag == VAL_DOUBLE) return v->f;
    if (v->tag == VAL_INT || v->tag == VAL_I64 || v->tag == VAL_I32 || v->tag == VAL_BOOL)
        return (double)v->i;
    if (v->tag == VAL_BIGINT) {
        EclBigInt* b = (EclBigInt*)v->ptr;
        double r = 0.0;
        for (size_t i = b->len; i-- > 0;)
            r = r * 4294967296.0 + b->limbs[i];
        return b->sign < 0 ? -r : r;
    }
    return 0.0;
}

static void reject_unknown(Value* a, Value* b) {
    if (a->tag == VAL_UNKNOWN || b->tag == VAL_UNKNOWN) {
        runtime_raise_error("TypeError: unknown 不能参与运算");
    }
}

static void i32_finish(Value* result, int is32) {
    if (is32 && result->tag == VAL_INT) {
        result->tag = VAL_I32;
        result->i = (int32_t)result->i;
    }
}

void runtime_i32_pair(Value* result, Value* a, Value* b) {
    if (result->tag == VAL_INT &&
        (a->tag == VAL_I32 || b->tag == VAL_I32)) {
        result->tag = VAL_I32;
        result->i = (int32_t)result->i;
    }
}

void runtime_i32_unary(Value* result, Value* a) {
    if (result->tag == VAL_INT && a->tag == VAL_I32) {
        result->tag = VAL_I32;
        result->i = (int32_t)result->i;
    }
}

void runtime_neg(Value* result, Value* a) {
    if (a->tag == VAL_UNKNOWN) {
        runtime_raise_error("TypeError: unknown 不能参与运算");
    }
    switch (a->tag) {
        case VAL_INT: case VAL_I64: case VAL_I32: case VAL_BOOL:
            result->tag = VAL_INT; result->i = -a->i; return;
        case VAL_FLOAT:
            result->tag = VAL_FLOAT; result->f = -a->f; return;
        case VAL_DOUBLE:
            result->tag = VAL_DOUBLE; result->f = -a->f; return;
        case VAL_BIGINT: {
            EclBigInt* src = (EclBigInt*)a->ptr;
            EclBigInt* b = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
            bi_init(b);
            bi_reserve(b, src->len);
            for (size_t i = 0; i < src->len; i++) b->limbs[i] = src->limbs[i];
            b->len = src->len;
            b->sign = -src->sign;
            result->tag = VAL_BIGINT; result->ptr = b;
            return;
        }
        default:
            result->tag = VAL_NONE; result->i = 0;
    }
}

void runtime_invert(Value* result, Value* a) {
    if (a->tag == VAL_UNKNOWN) {
        runtime_raise_error("TypeError: unknown 不能参与运算");
    }
    if (a->tag == VAL_INT || a->tag == VAL_I64 || a->tag == VAL_I32 || a->tag == VAL_BOOL) {
        result->tag = VAL_INT; result->i = ~a->i;
    } else {
        result->tag = VAL_NONE; result->i = 0;
    }
}

void runtime_add(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    int dbl = (a->tag == VAL_DOUBLE || b->tag == VAL_DOUBLE);
    int is32 = (a->tag == VAL_I32 || b->tag == VAL_I32);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        result->tag = dbl ? VAL_DOUBLE : VAL_FLOAT;
        result->f = val_double(a) + val_double(b);
        return;
    }
    if (a->tag == VAL_INT && b->tag == VAL_INT) {
        result->tag = VAL_INT; result->i = a->i + b->i; i32_finish(result, is32); return;
    }
    if (a->tag == VAL_STR && b->tag == VAL_STR) {
        int64_t la = my_strlen(a->s), lb = my_strlen(b->s);
        char* s = (char*)no_crt_alloc(la + lb + 1);
        if (!s) { result->tag = VAL_NONE; result->i = 0; return; }
        memcpy(s, a->s, la);
        memcpy(s + la, b->s, lb);
        s[la + lb] = '\0';
        result->tag = VAL_STR; result->s = s;
        return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* bx = make_big_from_val(a);
        EclBigInt* by = make_big_from_val(b);
        EclBigInt* br = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
        bi_init(br); bi_add(br, bx, by);
        result->tag = VAL_BIGINT; result->ptr = br;
        return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

void runtime_sub(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    int dbl = (a->tag == VAL_DOUBLE || b->tag == VAL_DOUBLE);
    int is32 = (a->tag == VAL_I32 || b->tag == VAL_I32);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        result->tag = dbl ? VAL_DOUBLE : VAL_FLOAT;
        result->f = val_double(a) - val_double(b);
        return;
    }
    if (a->tag == VAL_INT && b->tag == VAL_INT) {
        result->tag = VAL_INT; result->i = a->i - b->i; i32_finish(result, is32); return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* bx = make_big_from_val(a);
        EclBigInt* by = make_big_from_val(b);
        EclBigInt* br = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
        bi_init(br);
        by->sign = -by->sign;
        bi_add(br, bx, by);
        result->tag = VAL_BIGINT; result->ptr = br;
        return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

void runtime_mul(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    int dbl = (a->tag == VAL_DOUBLE || b->tag == VAL_DOUBLE);
    int is32 = (a->tag == VAL_I32 || b->tag == VAL_I32);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        result->tag = dbl ? VAL_DOUBLE : VAL_FLOAT;
        result->f = val_double(a) * val_double(b);
        return;
    }
    if (a->tag == VAL_INT && b->tag == VAL_INT) {
        result->tag = VAL_INT; result->i = a->i * b->i; i32_finish(result, is32); return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* bx = make_big_from_val(a);
        EclBigInt* by = make_big_from_val(b);
        EclBigInt* br = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
        bi_init(br); bi_mul(br, bx, by);
        result->tag = VAL_BIGINT; result->ptr = br;
        return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

void runtime_div(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    int dbl = (a->tag == VAL_DOUBLE || b->tag == VAL_DOUBLE);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    if (b->tag == VAL_INT && b->i == 0) { runtime_raise_error("ZeroDivisionError"); }
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double bf = val_double(b);
        if (bf == 0.0) { runtime_raise_error("ZeroDivisionError"); }
        result->tag = dbl ? VAL_DOUBLE : VAL_FLOAT;
        result->f = val_double(a) / bf;
        return;
    }
    if (a->tag == VAL_INT && b->tag == VAL_INT) {
        result->tag = VAL_FLOAT; result->f = (double)a->i / (double)b->i; return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        double bf = val_double(b);
        if (bf == 0.0) { runtime_raise_error("ZeroDivisionError"); }
        result->tag = VAL_FLOAT;
        result->f = val_double(a) / bf;
        return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

void runtime_floordiv(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    int dbl = (a->tag == VAL_DOUBLE || b->tag == VAL_DOUBLE);
    int is32 = (a->tag == VAL_I32 || b->tag == VAL_I32);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    if (b->tag == VAL_INT && b->i == 0) { runtime_raise_error("ZeroDivisionError"); }
    if (a->tag == VAL_INT && b->tag == VAL_INT) {
        result->tag = VAL_INT; result->i = a->i / b->i; i32_finish(result, is32); return;
    }
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double af = val_double(a);
        double bf = val_double(b);
        if (bf == 0.0) { runtime_raise_error("ZeroDivisionError"); }
        double q = af / bf;
        int64_t iq = (int64_t)q;
        if ((double)iq > q) iq--;
        result->tag = dbl ? VAL_DOUBLE : VAL_FLOAT;
        result->f = (double)iq;
        return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        runtime_raise_error("NotImplementedError"); return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

void runtime_mod(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    int dbl = (a->tag == VAL_DOUBLE || b->tag == VAL_DOUBLE);
    int is32 = (a->tag == VAL_I32 || b->tag == VAL_I32);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    if (b->tag == VAL_INT && b->i == 0) { runtime_raise_error("ZeroDivisionError"); }
    if (a->tag == VAL_INT && b->tag == VAL_INT) {
        result->tag = VAL_INT; result->i = a->i % b->i; i32_finish(result, is32); return;
    }
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double af = val_double(a);
        double bf = val_double(b);
        if (bf == 0.0) { runtime_raise_error("ZeroDivisionError"); }
        double q = af / bf;
        int64_t iq = (int64_t)q;
        if ((double)iq > q) iq--;
        result->tag = dbl ? VAL_DOUBLE : VAL_FLOAT;
        result->f = af - (double)iq * bf;
        return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        runtime_raise_error("NotImplementedError"); return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

void runtime_pow(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    int dbl = (a->tag == VAL_DOUBLE || b->tag == VAL_DOUBLE);
    int is32 = (a->tag == VAL_I32 || b->tag == VAL_I32);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double base = val_double(a);
        double exp = val_double(b);
        if (exp == 0.5) {
            result->tag = dbl ? VAL_DOUBLE : VAL_FLOAT;
            result->f = my_sqrt(base);
            return;
        }
        int64_t iexp = (int64_t)exp;
        double r = 1.0;
        int neg = iexp < 0;
        if (neg) iexp = -iexp;
        while (iexp > 0) {
            if (iexp & 1) r *= base;
            base *= base;
            iexp >>= 1;
        }
        result->tag = dbl ? VAL_DOUBLE : VAL_FLOAT;
        result->f = neg ? 1.0 / r : r;
        return;
    }
    if (a->tag == VAL_INT && b->tag == VAL_INT) {
        int64_t base = a->i, exp = b->i;
        if (exp < 0) { result->tag = VAL_FLOAT; result->f = 1.0; return; }
        int64_t r = 1;
        while (exp > 0) {
            if (exp & 1) r *= base;
            base *= base;
            exp >>= 1;
        }
        result->tag = VAL_INT; result->i = r; i32_finish(result, is32);
        return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* bi_base = make_big_from_val(a);
        EclBigInt* bi_exp = make_big_from_val(b);
        if (bi_exp->sign <= 0) { result->tag = VAL_FLOAT; result->f = 1.0; return; }
        if (bi_exp->len > 2) { runtime_raise_error("OverflowError"); }
        int64_t exp = bi_exp->limbs[0];
        if (bi_exp->len >= 2) exp |= ((int64_t)bi_exp->limbs[1] << LIMB_BITS);
        EclBigInt* bi_res = make_big_i64(1);
        while (exp > 0) {
            if (exp & 1) {
                EclBigInt* new_res = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
                bi_init(new_res);
                bi_mul(new_res, bi_res, bi_base);
                bi_res = new_res;
            }
            exp >>= 1;
            if (exp > 0) {
                EclBigInt* new_base = (EclBigInt*)no_crt_alloc(sizeof(EclBigInt));
                bi_init(new_base);
                bi_mul(new_base, bi_base, bi_base);
                bi_base = new_base;
            }
        }
        result->tag = VAL_BIGINT;
        result->ptr = bi_res;
        return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

// ============================================================
// 比较运算
// ============================================================
void runtime_eq(Value* result, Value* a, Value* b) {
    if (a->tag == VAL_UNKNOWN || b->tag == VAL_UNKNOWN) {
        result->tag = VAL_BOOL;
        result->i = (a->tag == VAL_UNKNOWN && b->tag == VAL_UNKNOWN);
        return;
    }
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv);
    a = &av; b = &bv;
    int eq = 0;
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* x = make_big_from_val(a);
        EclBigInt* y = make_big_from_val(b);
        eq = (x->sign == y->sign) && cmp_mag(x->limbs, x->len, y->limbs, y->len) == 0;
        result->tag = VAL_BOOL; result->i = eq;
        return;
    }
    if (a->tag == VAL_INT && b->tag == VAL_INT) eq = a->i == b->i;
    else if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double af = (a->tag == VAL_FLOAT) ? a->f : (double)a->i;
        double bf = (b->tag == VAL_FLOAT) ? b->f : (double)b->i;
        eq = af == bf;
    } else if (a->tag == b->tag) {
        switch (a->tag) {
            case VAL_STR: eq = my_strcmp(a->s, b->s) == 0; break;
            case VAL_NULL: eq = 1; break; case VAL_NONE: eq = 1; break;
            case VAL_INSTANCE: eq = a->ptr == b->ptr; break;
            default: eq = 0;
        }
    }
    result->tag = VAL_BOOL; result->i = eq;
}
void runtime_ne(Value* result, Value* a, Value* b) {
    runtime_eq(result, a, b); result->i = !result->i;
}
void runtime_lt(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv);
    a = &av; b = &bv;
    result->tag = VAL_BOOL;
    if (a->tag == VAL_STR && b->tag == VAL_STR) { result->i = my_strcmp(a->s, b->s) < 0; return; }
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double af = (a->tag == VAL_FLOAT) ? a->f : (double)a->i;
        double bf = (b->tag == VAL_FLOAT) ? b->f : (double)b->i;
        result->i = af < bf; return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* x = make_big_from_val(a);
        EclBigInt* y = make_big_from_val(b);
        if (x->sign != y->sign) result->i = x->sign < y->sign;
        else if (x->sign == 0) result->i = 0;
        else result->i = cmp_mag(x->limbs, x->len, y->limbs, y->len) < 0;
        return;
    }
    result->i = a->i < b->i;
}
void runtime_gt(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    result->tag = VAL_BOOL;
    if (a->tag == VAL_STR && b->tag == VAL_STR) { result->i = my_strcmp(a->s, b->s) > 0; return; }
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double af = (a->tag == VAL_FLOAT) ? a->f : (double)a->i;
        double bf = (b->tag == VAL_FLOAT) ? b->f : (double)b->i;
        result->i = af > bf; return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* x = make_big_from_val(a);
        EclBigInt* y = make_big_from_val(b);
        if (x->sign != y->sign) result->i = x->sign > y->sign;
        else if (x->sign == 0) result->i = 0;
        else result->i = cmp_mag(x->limbs, x->len, y->limbs, y->len) > 0;
        return;
    }
    result->i = a->i > b->i;
}
void runtime_le(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    result->tag = VAL_BOOL;
    if (a->tag == VAL_STR && b->tag == VAL_STR) { result->i = my_strcmp(a->s, b->s) <= 0; return; }
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double af = (a->tag == VAL_FLOAT) ? a->f : (double)a->i;
        double bf = (b->tag == VAL_FLOAT) ? b->f : (double)b->i;
        result->i = af <= bf; return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* x = make_big_from_val(a);
        EclBigInt* y = make_big_from_val(b);
        if (x->sign != y->sign) result->i = x->sign < y->sign;
        else if (x->sign == 0) result->i = 1;
        else result->i = cmp_mag(x->limbs, x->len, y->limbs, y->len) <= 0;
        return;
    }
    result->i = a->i <= b->i;
}
void runtime_ge(Value* result, Value* a, Value* b) {
    reject_unknown(a, b);
    Value av = *a, bv = *b;
    NORMALIZE_VALUE(&av); NORMALIZE_VALUE(&bv); a = &av; b = &bv;
    result->tag = VAL_BOOL;
    if (a->tag == VAL_STR && b->tag == VAL_STR) { result->i = my_strcmp(a->s, b->s) >= 0; return; }
    if (a->tag == VAL_FLOAT || b->tag == VAL_FLOAT) {
        double af = (a->tag == VAL_FLOAT) ? a->f : (double)a->i;
        double bf = (b->tag == VAL_FLOAT) ? b->f : (double)b->i;
        result->i = af >= bf; return;
    }
    if (a->tag == VAL_BIGINT || b->tag == VAL_BIGINT) {
        EclBigInt* x = make_big_from_val(a);
        EclBigInt* y = make_big_from_val(b);
        if (x->sign != y->sign) result->i = x->sign > y->sign;
        else if (x->sign == 0) result->i = 1;
        else result->i = cmp_mag(x->limbs, x->len, y->limbs, y->len) >= 0;
        return;
    }
    result->i = a->i >= b->i;
}

// ============================================================
// 内置函数（VM 风格 builtin_call）
// ============================================================
static void write_str_err(const char* s, int64_t len) {
    write_handle(GetStdHandle((DWORD)-12), s, len);
}

static char* value_to_cstr(Value* v) {
    Value s;
    runtime_str(&s, v);
    return s.s;
}

static double my_sqrt(double x) {
    if (load_ucrt() && g_sqrt_fn) return g_sqrt_fn(x);
    if (x < 0) return 0.0;
    if (x == 0) return 0.0;
    double g = x > 1.0 ? x : 1.0;
    for (int i = 0; i < 80; i++) g = 0.5 * (g + x / g);
    return g;
}

static Value copy_value(Value v);

static Value copy_value(Value v) {
    if (v.tag == VAL_LIST) {
        EclList* src = (EclList*)v.ptr;
        Value out; out.tag = VAL_LIST; out.ptr = list_new();
        EclList* dst = (EclList*)out.ptr;
        for (int64_t i = 0; i < src->len; i++) {
            Value item = copy_value(src->items[i]);
            list_push_raw(dst, item);
            value_release(&item);
        }
        return out;
    }
    if (v.tag == VAL_TUPLE) {
        EclList* src = (EclList*)v.ptr;
        Value out; out.tag = VAL_TUPLE; out.ptr = list_new();
        EclList* dst = (EclList*)out.ptr;
        for (int64_t i = 0; i < src->len; i++) {
            Value item = copy_value(src->items[i]);
            list_push_raw(dst, item);
            value_release(&item);
        }
        return out;
    }
    if (v.tag == VAL_DICT) {
        EclDict* src = (EclDict*)v.ptr;
        Value out; out.tag = VAL_DICT; out.ptr = dict_new();
        EclDict* dst = (EclDict*)out.ptr;
        for (EclDictEntry* e = src->head; e; e = e->order_next) {
            Value k = e->key; Value val = copy_value(e->val);
            dict_put_raw(dst, k, val);
            value_release(&val);
        }
        return out;
    }
    value_share(&v);
    return v;
}

static void builtin_write(Value* result, Value** args, int argc, int err) {
    for (int i = 0; i < argc; i++) {
        Value s; runtime_str(&s, args[i]);
        if (i) { if (err) write_str_err(" ", 1); else write_str(" ", 1); }
        if (err) write_str_err(s.s, my_strlen(s.s));
        else write_str(s.s, my_strlen(s.s));
    }
    result->tag = VAL_NONE; result->i = 0;
}

static void builtin_list(Value* result, Value* v) {
    EclList* l = list_new();
    if (v->tag == VAL_LIST || v->tag == VAL_TUPLE) {
        EclList* src = (EclList*)v->ptr;
        for (int64_t i = 0; i < src->len; i++) list_push_raw(l, src->items[i]);
    } else if (v->tag == VAL_STR) {
        int64_t n = my_strlen(v->s);
        for (int64_t i = 0; i < n; i++) {
            char* ch = (char*)no_crt_alloc(2); ch[0] = v->s[i]; ch[1] = 0;
            Value item; item.tag = VAL_STR; item.s = ch;
            list_push_raw(l, item);
        }
    } else if (v->tag == VAL_DICT) {
        EclDict* d = (EclDict*)v->ptr;
        for (EclDictEntry* e = d->head; e; e = e->order_next)
            list_push_raw(l, e->key);
    }
    result->tag = VAL_LIST; result->ptr = l;
}

void runtime_builtin_call(Value* result, const char* name, Value** args,
                          int64_t argc) {
    result->tag = VAL_NONE; result->i = 0;
    if (my_strcmp(name, "input") == 0) {
        if (argc >= 1 && args[0]->tag == VAL_STR) {
            write_str(args[0]->s, my_strlen(args[0]->s));
        }
        char* buf = (char*)no_crt_alloc(4096);
        int n = 0;
        HANDLE h = GetStdHandle(STD_INPUT_HANDLE);
        while (n < 4095) {
            char ch = 0; DWORD got = 0;
            if (!ReadFile(h, &ch, 1, &got, 0) || got == 0) break;
            if (ch == '\n') break;
            if (ch == '\r') continue;
            buf[n++] = ch;
        }
        buf[n] = 0;
        result->tag = VAL_STR;
        result->s = my_strdup(buf);
        return;
    }
    if (my_strcmp(name, "write") == 0) { builtin_write(result, args, (int)argc, 0); return; }
    if (my_strcmp(name, "eprint") == 0) { builtin_write(result, args, (int)argc, 1); return; }
    if (my_strcmp(name, "double") == 0) {
        if (argc) {
            runtime_float(result, args[0]);
            result->tag = VAL_DOUBLE;
        }
        return;
    }
    if (my_strcmp(name, "i64") == 0) {
        if (argc) {
            runtime_int(result, args[0]);
            result->tag = VAL_I64;
        }
        return;
    }
    if (my_strcmp(name, "i32") == 0) {
        if (argc) {
            runtime_int(result, args[0]);
            result->tag = VAL_I32;
            result->i = (int32_t)result->i;
        }
        return;
    }
    if (my_strcmp(name, "bi") == 0) {
        if (argc) runtime_int(result, args[0]);
        return;
    }
    if (my_strcmp(name, "list") == 0) {
        if (argc) builtin_list(result, args[0]); else { result->tag = VAL_LIST; result->ptr = list_new(); }
        return;
    }
    if (my_strcmp(name, "tuple") == 0) {
        if (argc) {
            Value l; builtin_list(&l, args[0]);
            runtime_to_tuple(result, &l);
            value_release(&l);
        } else {
            result->tag = VAL_TUPLE; result->ptr = list_new();
        }
        return;
    }
    if (my_strcmp(name, "dict") == 0) {
        if (!argc) { result->tag = VAL_DICT; result->ptr = dict_new(); return; }
        Value* v = args[0];
        if (v->tag == VAL_DICT) {
            EclDict* d = dict_new();
            EclDict* src = (EclDict*)v->ptr;
            for (EclDictEntry* e = src->head; e; e = e->order_next)
                dict_put_raw(d, e->key, e->val);
            result->tag = VAL_DICT; result->ptr = d; return;
        }
        if (v->tag == VAL_LIST || v->tag == VAL_TUPLE) {
            EclDict* d = dict_new();
            EclList* l = (EclList*)v->ptr;
            for (int64_t i = 0; i < l->len; i++) {
                Value pair = l->items[i];
                if (pair.tag != VAL_LIST && pair.tag != VAL_TUPLE) continue;
                EclList* pl = (EclList*)pair.ptr;
                if (pl->len >= 2) dict_put_raw(d, pl->items[0], pl->items[1]);
            }
            result->tag = VAL_DICT; result->ptr = d; return;
        }
        runtime_raise_error("TypeError: 无法转换为 dict"); return;
    }
    if (my_strcmp(name, "slice") == 0 && argc >= 3) {
        builtin_list(result, args[0]);
        EclList* l = (EclList*)result->ptr;
        int64_t start = args[1]->tag == VAL_INT ? args[1]->i : 0;
        int64_t end = args[2]->tag == VAL_INT ? args[2]->i : l->len;
        if (start < 0) start += l->len;
        if (end < 0) end += l->len;
        if (start < 0) start = 0;
        if (end > l->len) end = l->len;
        EclList* out = list_new();
        for (int64_t i = start; i < end; i++) list_push_raw(out, l->items[i]);
        value_release(result);
        result->tag = VAL_LIST; result->ptr = out;
        return;
    }
    if (my_strcmp(name, "abs") == 0 && argc >= 1) {
        if (args[0]->tag == VAL_INT) { result->tag = VAL_INT; result->i = args[0]->i < 0 ? -args[0]->i : args[0]->i; }
        else if (args[0]->tag == VAL_FLOAT) { result->tag = VAL_FLOAT; result->f = args[0]->f < 0 ? -args[0]->f : args[0]->f; }
        return;
    }
    if (my_strcmp(name, "max") == 0 || my_strcmp(name, "min") == 0) {
        int want_max = my_strcmp(name, "max") == 0;
        Value** list = args; int64_t n = argc;
        EclList* l = 0;
        if (argc == 1 && (args[0]->tag == VAL_LIST || args[0]->tag == VAL_TUPLE)) {
            l = (EclList*)args[0]->ptr; n = l->len;
        }
        if (n <= 0) return;
        Value best = l ? l->items[0] : *args[0];
        for (int64_t i = 1; i < n; i++) {
            Value cur = l ? l->items[i] : *args[i];
            Value cmp; runtime_lt(&cmp, &cur, &best);
            int take = want_max ? (cmp.i == 0) : (cmp.i != 0);
            if (take) best = cur;
        }
        *result = best; value_share(result);
        return;
    }
    if (my_strcmp(name, "sum") == 0 && argc >= 1) {
        Value total; total.tag = VAL_INT; total.i = 0;
        if (args[0]->tag == VAL_LIST || args[0]->tag == VAL_TUPLE) {
            EclList* l = (EclList*)args[0]->ptr;
            for (int64_t i = 0; i < l->len; i++) runtime_add(&total, &total, &l->items[i]);
        }
        *result = total; value_share(result);
        return;
    }
    if (my_strcmp(name, "pow") == 0 && argc >= 2) { runtime_pow(result, args[0], args[1]); return; }
    if (my_strcmp(name, "sqrt") == 0 && argc >= 1) {
        if (args[0]->tag == VAL_INT || args[0]->tag == VAL_FLOAT) {
            double x = args[0]->tag == VAL_INT ? (double)args[0]->i : args[0]->f;
            result->tag = VAL_FLOAT; result->f = my_sqrt(x);
        }
        return;
    }
    if (my_strcmp(name, "floor") == 0 && argc >= 1) {
        if (args[0]->tag == VAL_FLOAT) { result->tag = VAL_INT; result->i = (int64_t)args[0]->f; }
        else if (args[0]->tag == VAL_INT) { *result = *args[0]; value_share(result); }
        return;
    }
    if (my_strcmp(name, "ceil") == 0 && argc >= 1) {
        if (args[0]->tag == VAL_FLOAT) {
            double f = args[0]->f;
            int64_t i = (int64_t)f;
            if ((double)i < f) i++;
            result->tag = VAL_INT; result->i = i;
        } else if (args[0]->tag == VAL_INT) { *result = *args[0]; value_share(result); }
        return;
    }
    if (my_strcmp(name, "round") == 0 && argc >= 1) {
        if (args[0]->tag == VAL_FLOAT) {
            double f = args[0]->f;
            int64_t digits = (argc >= 2 && args[1]->tag == VAL_INT) ? args[1]->i : 0;
            double scale = 1.0;
            for (int64_t i = 0; i < digits; i++) scale *= 10.0;
            double x = f * scale;
            int64_t r = (int64_t)(x >= 0 ? x + 0.5 : x - 0.5);
            result->tag = VAL_FLOAT; result->f = (double)r / scale;
        } else if (args[0]->tag == VAL_INT) { *result = *args[0]; value_share(result); }
        return;
    }
    if (my_strcmp(name, "random") == 0) {
        static uint64_t seed = 88172645463325252ull;
        seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
        if (argc >= 1 && args[0]->tag == VAL_INT && args[0]->i > 0) {
            result->tag = VAL_INT; result->i = (int64_t)(seed % (uint64_t)args[0]->i);
        } else {
            result->tag = VAL_FLOAT;
            result->f = (double)(seed >> 11) / 9007199254740992.0;
        }
        return;
    }
    if (my_strcmp(name, "new_buffer") == 0 && argc >= 1) {
        runtime_new_buffer(result, args[0]);
        return;
    }
    if (my_strcmp(name, "sizeof") == 0 && argc >= 1) {
        result->tag = VAL_INT;
        if (args[0]->tag == VAL_BUFFER)
            result->i = ((EclBuffer*)args[0]->ptr)->size;
        else
            runtime_len(result, args[0]);
        return;
    }
    if (my_strcmp(name, "clone") == 0 && argc >= 1) {
        *result = copy_value(*args[0]);
        return;
    }
    if (my_strcmp(name, "id") == 0 && argc >= 1) {
        result->tag = VAL_INT;
        if (args[0]->tag == VAL_INT) result->i = args[0]->i;
        else result->i = (int64_t)(uintptr_t)args[0]->ptr;
        return;
    }
    if (my_strcmp(name, "hash") == 0 && argc >= 1) {
        result->tag = VAL_INT; result->i = (int64_t)value_hash(*args[0]);
        return;
    }
    if (my_strcmp(name, "exit") == 0) {
        int64_t code = (argc >= 1 && args[0]->tag == VAL_INT) ? args[0]->i : 0;
        runtime_shutdown();
        ExitProcess((DWORD)code);
    }
    if (my_strcmp(name, "code") == 0) {
        runtime_raise_error("NotImplementedError: 动态 code() 在 AOT 模式下不支持");
        return;
    }
    runtime_raise_error("RuntimeError: 未知内置函数");
}

// ============================================================
// 函数 / 类
// ============================================================
typedef struct EclFunction {
    char* name; void* code; int param_count; Value* defaults;
    EclSig* sig; struct EclFunction* next;
} EclFunction;

void runtime_new_function(Value* result, const char* name, void* code, EclSig* sig) {
    EclFunction* fn = (EclFunction*)no_crt_alloc(sizeof(EclFunction));
    fn->name = my_strdup(name); fn->code = code;
    fn->param_count = sig ? (int)sig->count : 0;
    fn->defaults = 0; fn->sig = sig; fn->next = 0;
    result->tag = VAL_FUNCTION; result->ptr = fn;
}

void runtime_define_function(Value* fn, const char* name) {
    Value* slot = get_var(name);
    if (slot->tag == VAL_FUNCTION) {
        EclFunction* nf = (EclFunction*)fn->ptr;
        nf->next = (EclFunction*)slot->ptr;
    }
    *slot = *fn;
    value_share(slot);
}

void runtime_func_set_defaults(Value* fn_val, Value* defaults) {
    if (fn_val->tag != VAL_FUNCTION) return;
    EclFunction* fn = (EclFunction*)fn_val->ptr;
    if (fn->param_count <= 0) return;
    fn->defaults = (Value*)no_crt_alloc(fn->param_count * sizeof(Value));
    for (int i = 0; i < fn->param_count; i++) {
        fn->defaults[i] = defaults[i];
        value_share(&fn->defaults[i]);
    }
}

static int kw_lookup(Value* kwd, const char* name, Value* out) {
    if (!kwd || kwd->tag != VAL_DICT) return 0;
    Value key; key.tag = VAL_STR; key.s = (char*)name;
    EclDictEntry* e = dict_find((EclDict*)kwd->ptr, key);
    if (!e) return 0;
    *out = e->val;
    return 1;
}

static int value_is_type(Value* v, const char* t) {
    if (!t || !t[0] || my_strcmp(t, "any") == 0) return 1;
    if (my_strcmp(t, "int") == 0) return v->tag == VAL_INT;
    if (my_strcmp(t, "i64") == 0) return v->tag == VAL_I64 || v->tag == VAL_INT;
    if (my_strcmp(t, "bool") == 0) return v->tag == VAL_BOOL;
    if (my_strcmp(t, "float") == 0) return v->tag == VAL_FLOAT || v->tag == VAL_INT;
    if (my_strcmp(t, "double") == 0) return v->tag == VAL_DOUBLE || v->tag == VAL_INT;
    if (my_strcmp(t, "str") == 0) return v->tag == VAL_STR;
    if (my_strcmp(t, "list") == 0) return v->tag == VAL_LIST;
    if (my_strcmp(t, "tuple") == 0) return v->tag == VAL_TUPLE;
    if (my_strcmp(t, "dict") == 0) return v->tag == VAL_DICT;
    if (my_strcmp(t, "function") == 0)
        return v->tag == VAL_FUNCTION || v->tag == VAL_BOUND;
    if (v->tag == VAL_INSTANCE)
        return my_strcmp(((EclInstance*)v->ptr)->cls->name, t) == 0;
    return 1;
}

static void call_bound(Value* out, void* code, EclSig* sig, Value* defaults,
                       EclInstance* self, Value** args, int argc, Value* kwd) {
    if (!sig) {
        Value self_val; self_val.tag = VAL_INSTANCE; self_val.ptr = self;
        Value* a[34];
        int n = 0;
        if (self) a[n++] = &self_val;
        for (int i = 0; i < argc && n < 34; i++) a[n++] = args[i];
        for (int i = 0; i < n; i++) value_share(a[i]);
        ((EclFuncPtr)code)(out, a, n);
        return;
    }
    int64_t count = sig->count;
    if (count > 32) count = 32;
    Value* vals[32];
    Value owned[64];
    int owned_n = 0;
    int pos = 0;
    int varidx = (int)sig->varargs_index;
    int kwidx = (int)sig->kwargs_index;
    Value self_val; self_val.tag = VAL_INSTANCE; self_val.ptr = self;

    for (int i = 0; i < count; i++) {
        const char* name = sig->names[i];
        if (i == varidx) {
            EclList* l = list_new();
            for (; pos < argc; pos++) list_push_raw(l, *args[pos]);
            Value* sv = &owned[owned_n++];
            sv->tag = VAL_LIST; sv->ptr = l;
            vals[i] = sv;
            continue;
        }
        if (i == kwidx) {
            EclDict* d = dict_new();
            if (kwd && kwd->tag == VAL_DICT) {
                EclDict* src = (EclDict*)kwd->ptr;
                for (EclDictEntry* e = src->head; e; e = e->order_next) {
                    int is_param = 0;
                    for (int j = 0; j < count; j++) {
                        if (e->key.tag == VAL_STR && sig->names[j] &&
                            my_strcmp(e->key.s, sig->names[j]) == 0) {
                            is_param = 1; break;
                        }
                    }
                    if (!is_param) dict_put_raw(d, e->key, e->val);
                }
            }
            Value* sv = &owned[owned_n++];
            sv->tag = VAL_DICT; sv->ptr = d;
            vals[i] = sv;
            continue;
        }
        if (i == 0 && self) {
            vals[i] = &self_val;
            continue;
        }
        Value* chosen = 0;
        int takes_positional = (varidx < 0) || (i < varidx);
        if (takes_positional && pos < argc) {
            chosen = args[pos++];
        } else {
            Value tmp;
            if (kw_lookup(kwd, name, &tmp)) chosen = &tmp;
            else if (defaults && defaults[i].tag != -1) chosen = &defaults[i];
        }
        if (!chosen) {
            runtime_raise_error("TypeError: 函数调用缺少参数");
            vals[i] = &owned[owned_n++];
            vals[i]->tag = VAL_NONE;
            continue;
        }
        Value* conv = &owned[owned_n++];
        const char* t = sig->types ? sig->types[i] : 0;
        if (t && t[0] && my_strcmp(t, "any") != 0) {
            runtime_convert(conv, chosen, t);
        } else {
            *conv = *chosen;
            value_share(conv);
        }
        vals[i] = conv;
    }
    if (pos < argc && varidx < 0) {
        runtime_raise_error("TypeError: 函数参数过多");
    }
    for (int i = 0; i < count; i++) value_share(vals[i]);
    ((EclFuncPtr)code)(out, vals, (int)count);
}

static int sig_min_args(EclSig* sig, Value* defaults) {
    if (!sig) return 0;
    int varidx = (int)sig->varargs_index;
    int kwidx = (int)sig->kwargs_index;
    int required = 0;
    for (int i = 0; i < sig->count; i++) {
        if (i == varidx || i == kwidx) continue;
        if (varidx >= 0 && i > varidx) continue;
        if (!(defaults && defaults[i].tag != -1)) required++;
    }
    return required;
}

static int sig_max_args(EclSig* sig) {
    if (!sig) return 32;
    if (sig->varargs_index >= 0) return 32;
    int maxargs = 0;
    for (int i = 0; i < sig->count; i++) {
        if (i == sig->kwargs_index || i == sig->varargs_index) continue;
        maxargs++;
    }
    return maxargs;
}

static EclFunction* choose_overload(EclFunction* fn, Value** args, int argc) {
    EclFunction* best = 0;
    int best_score = -1;
    for (EclFunction* p = fn; p; p = p->next) {
        if (!p->sig) { if (!best) best = p; continue; }
        int minargs = sig_min_args(p->sig, p->defaults);
        int maxargs = sig_max_args(p->sig);
        if (argc < minargs || argc > maxargs) continue;
        int score = 0, ok = 1;
        for (int i = 0; i < argc && i < p->sig->count; i++) {
            const char* t = p->sig->types ? p->sig->types[i] : 0;
            if (!t || !t[0]) continue;
            if (value_is_type(args[i], t)) score += 2;
            else if (my_strcmp(t, "int") == 0 || my_strcmp(t, "float") == 0 ||
                     my_strcmp(t, "double") == 0 || my_strcmp(t, "i64") == 0) {
                score += 1;
            } else ok = 0;
        }
        if (ok && score > best_score) { best = p; best_score = score; }
    }
    return best ? best : fn;
}

static void invoke_method(Value* out, EclMethod* m, EclInstance* self,
                          Value** args, int argc, Value* kwd) {
    call_bound(out, m->code, m->sig, 0, self, args, argc, kwd);
}

static void builtin_list_append(Value* result, Value** args, int argc) {
    if (argc >= 2 && args[0]->tag == VAL_LIST) {
        list_push_raw((EclList*)args[0]->ptr, *args[1]);
    }
    result->tag = VAL_NONE; result->i = 0;
}

static void run_class_bodies(EclClass* cls, EclInstance* inst, int depth) {
    if (!cls || depth > 16) return;
    for (int i = 0; i < cls->base_count; i++)
        run_class_bodies(cls->bases[i], inst, depth + 1);
    for (EclMethod* m = cls->methods; m; m = m->next) {
        if (my_strcmp(m->name, "__class_body__") == 0) {
            Value r;
            invoke_method(&r, m, inst, 0, 0, 0);
            value_release(&r);
        }
    }
}

static void dispatch_call(Value* result, Value* f, Value** args, int argc,
                          Value* kwd) {
    if (f->tag == VAL_EXCLASS) {
        EclExc* e = (EclExc*)no_crt_alloc(sizeof(EclExc));
        e->type = f->s;
        if (argc >= 1 && args[0]->tag == VAL_STR) e->msg = args[0]->s;
        else e->msg = my_strdup("");
        result->tag = VAL_EXC;
        result->ptr = e;
        return;
    }
    if (f->tag == VAL_CLASS) {
        EclClass* cls = (EclClass*)f->ptr;
        EclInstance* inst = (EclInstance*)no_crt_alloc(sizeof(EclInstance));
        inst->shared = 1;
        inst->cls = cls; inst->attrs = 0;
        result->tag = VAL_INSTANCE; result->ptr = inst;
        run_class_bodies(cls, inst, 0);
        EclMethod* init = find_method(cls, "__init__");
        if (init) {
            Value init_result;
            invoke_method(&init_result, init, inst, args, argc, kwd);
            value_release(&init_result);
        }
        return;
    }
    if (f->tag == VAL_BOUND) {
        EclBoundMethod* bm = (EclBoundMethod*)f->ptr;
        if (bm->builtin) {
            Value* all_args[33]; all_args[0] = &bm->self_val;
            for (int i = 0; i < argc && i < 32; i++) all_args[i + 1] = args[i];
            int n = argc + 1; if (n > 33) n = 33;
            ((EclFuncPtr)bm->builtin)(result, all_args, n);
            return;
        }
        invoke_method(result, bm->m, bm->self, args, argc, kwd);
        return;
    }
    if (f->tag == VAL_FUNCTION) {
        EclFunction* fn = choose_overload((EclFunction*)f->ptr, args, argc);
        call_bound(result, fn->code, fn->sig, fn->defaults, 0,
                   args, argc, kwd);
        return;
    }
    result->tag = VAL_NONE; result->i = 0;
}

void runtime_call(Value* result, Value* f, Value** args, int argc) {
    dispatch_call(result, f, args, argc, 0);
}

void runtime_call_kw(Value* result, Value* f, Value** args, int64_t* pair) {
    // pair[0] = argc, pair[1] = KeyWord dict Value*
    dispatch_call(result, f, args, (int)pair[0], (Value*)pair[1]);
}

void runtime_call_spread(Value* result, Value* f, Value* pos, Value* kwd) {
    Value* args[64];
    int argc = 0;
    if (pos->tag == VAL_LIST || pos->tag == VAL_TUPLE) {
        EclList* l = (EclList*)pos->ptr;
        argc = (int)(l->len > 64 ? 64 : l->len);
        for (int i = 0; i < argc; i++) args[i] = &l->items[i];
    } else {
        runtime_raise_error("TypeError: * 解包要求 list 或 tuple");
    }
    dispatch_call(result, f, args, argc, kwd);
}

void runtime_new_class(EclClass** out, const char* name) {
    EclClass* c = (EclClass*)no_crt_alloc(sizeof(EclClass));
    c->name = my_strdup(name); c->methods = 0;
    c->bases = (EclClass**)no_crt_alloc(sizeof(EclClass*) * 64);
    c->base_count = 0;
    *out = c;
}
void runtime_class_add_method(EclClass* cls, const char* name, void* code, int64_t nparams) {
    EclMethod* m = (EclMethod*)no_crt_alloc(sizeof(EclMethod));
    m->name = my_strdup(name); m->code = code; m->nparams = (int)nparams;
    m->sig = 0; m->next = cls->methods; cls->methods = m;
}
void runtime_class_add_method_ex(EclClass* cls, const char* name, void* code, EclSig* sig) {
    EclMethod* m = (EclMethod*)no_crt_alloc(sizeof(EclMethod));
    m->name = my_strdup(name); m->code = code;
    m->nparams = sig ? (int)sig->count : 0;
    m->sig = sig; m->next = cls->methods; cls->methods = m;
}

// ============================================================
// 属性 / 容器
// ============================================================
static Value make_str_value(const char* s) {
    Value v; v.tag = VAL_STR; v.s = my_strdup(s); return v;
}

static void str_method_upper(Value* result, Value** args, int argc) {
    if (args[0]->tag != VAL_STR) { result->tag = VAL_NONE; return; }
    int64_t n = my_strlen(args[0]->s);
    char* out = (char*)no_crt_alloc(n + 1);
    for (int64_t i = 0; i < n; i++) {
        char c = args[0]->s[i];
        out[i] = (c >= 'a' && c <= 'z') ? c - 32 : c;
    }
    out[n] = 0;
    result->tag = VAL_STR; result->s = out;
}

static void str_method_lower(Value* result, Value** args, int argc) {
    if (args[0]->tag != VAL_STR) { result->tag = VAL_NONE; return; }
    int64_t n = my_strlen(args[0]->s);
    char* out = (char*)no_crt_alloc(n + 1);
    for (int64_t i = 0; i < n; i++) {
        char c = args[0]->s[i];
        out[i] = (c >= 'A' && c <= 'Z') ? c + 32 : c;
    }
    out[n] = 0;
    result->tag = VAL_STR; result->s = out;
}

static int is_space_char(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

static void str_method_strip(Value* result, Value** args, int argc) {
    if (args[0]->tag != VAL_STR) { result->tag = VAL_NONE; return; }
    const char* s = args[0]->s;
    int64_t n = my_strlen(s), a = 0, b = n;
    while (a < b && is_space_char(s[a])) a++;
    while (b > a && is_space_char(s[b - 1])) b--;
    char* out = (char*)no_crt_alloc(b - a + 1);
    for (int64_t i = a; i < b; i++) out[i - a] = s[i];
    out[b - a] = 0;
    result->tag = VAL_STR; result->s = out;
}

static void str_method_split(Value* result, Value** args, int argc) {
    if (args[0]->tag != VAL_STR) { result->tag = VAL_NONE; return; }
    const char* s = args[0]->s;
    const char* sep = " ";
    if (argc >= 2 && args[1]->tag == VAL_STR) sep = args[1]->s;
    int64_t seplen = my_strlen(sep);
    EclList* l = list_new();
    if (seplen == 0) {
        int64_t n = my_strlen(s);
        for (int64_t i = 0; i < n; i++) {
            char* part = (char*)no_crt_alloc(2);
            part[0] = s[i]; part[1] = 0;
            Value item = make_str_value(part);
            list_push_raw(l, item);
            value_release(&item);
        }
    } else {
        int64_t start = 0, n = my_strlen(s);
        int64_t i = 0;
        while (i <= n) {
            int match = (i + seplen <= n);
            if (match) {
                for (int64_t j = 0; j < seplen; j++)
                    if (s[i + j] != sep[j]) { match = 0; break; }
            }
            if (match || i == n) {
                int64_t len = i - start;
                char* part = (char*)no_crt_alloc(len + 1);
                for (int64_t k = 0; k < len; k++) part[k] = s[start + k];
                part[len] = 0;
                Value item = make_str_value(part);
                list_push_raw(l, item);
                value_release(&item);
                if (i == n) break;
                i += seplen;
                start = i;
            } else {
                i++;
            }
        }
    }
    result->tag = VAL_LIST; result->ptr = l;
}

static void str_method_join(Value* result, Value** args, int argc) {
    if (args[0]->tag != VAL_STR) { result->tag = VAL_NONE; return; }
    const char* sep = args[0]->s;
    if (argc < 2) { result->tag = VAL_STR; result->s = my_strdup(""); return; }
    Value* seq = args[1];
    int64_t total = 0, count = 0;
    EclList* l = (seq->tag == VAL_LIST || seq->tag == VAL_TUPLE)
                     ? (EclList*)seq->ptr : 0;
    if (!l) { result->tag = VAL_STR; result->s = my_strdup(""); return; }
    for (int64_t i = 0; i < l->len; i++) {
        total += my_strlen(value_to_cstr(&l->items[i]));
        count++;
    }
    total += (count > 0 ? count - 1 : 0) * my_strlen(sep);
    char* out = (char*)no_crt_alloc(total + 1);
    int64_t pos = 0;
    for (int64_t i = 0; i < l->len; i++) {
        if (i) {
            int64_t sl = my_strlen(sep);
            for (int64_t j = 0; j < sl; j++) out[pos++] = sep[j];
        }
        char* item = value_to_cstr(&l->items[i]);
        int64_t il = my_strlen(item);
        for (int64_t j = 0; j < il; j++) out[pos++] = item[j];
    }
    out[pos] = 0;
    result->tag = VAL_STR; result->s = out;
}

static void str_method_find(Value* result, Value** args, int argc) {
    result->tag = VAL_INT; result->i = -1;
    if (args[0]->tag != VAL_STR || argc < 2 || args[1]->tag != VAL_STR) return;
    const char* s = args[0]->s;
    const char* sub = args[1]->s;
    int64_t n = my_strlen(s), m = my_strlen(sub);
    for (int64_t i = 0; i + m <= n; i++) {
        int ok = 1;
        for (int64_t j = 0; j < m; j++)
            if (s[i + j] != sub[j]) { ok = 0; break; }
        if (ok) { result->i = i; return; }
    }
}

static void str_method_replace(Value* result, Value** args, int argc) {
    if (args[0]->tag != VAL_STR || argc < 3 ||
        args[1]->tag != VAL_STR || args[2]->tag != VAL_STR) {
        result->tag = VAL_NONE; return;
    }
    const char* s = args[0]->s;
    const char* old = args[1]->s;
    const char* rep = args[2]->s;
    int64_t n = my_strlen(s), m = my_strlen(old), r = my_strlen(rep);
    int64_t count = 0;
    if (m > 0) {
        for (int64_t i = 0; i + m <= n; ) {
            int ok = 1;
            for (int64_t j = 0; j < m; j++)
                if (s[i + j] != old[j]) { ok = 0; break; }
            if (ok) { count++; i += m; } else i++;
        }
    }
    int64_t outlen = n + count * (r - m);
    if (outlen < 0) outlen = 0;
    char* out = (char*)no_crt_alloc(outlen + 1);
    int64_t pos = 0, i = 0;
    while (i < n) {
        int ok = (m > 0 && i + m <= n);
        if (ok) for (int64_t j = 0; j < m; j++)
            if (s[i + j] != old[j]) { ok = 0; break; }
        if (ok) {
            for (int64_t j = 0; j < r; j++) out[pos++] = rep[j];
            i += m;
        } else {
            out[pos++] = s[i++];
        }
    }
    out[pos] = 0;
    result->tag = VAL_STR; result->s = out;
}

static void str_method_format(Value* result, Value** args, int argc) {
    if (args[0]->tag != VAL_STR) { result->tag = VAL_NONE; return; }
    const char* fmt = args[0]->s;
    int64_t n = my_strlen(fmt), total = 0, placeholders = 0;
    for (int64_t i = 0; i + 1 < n; i++) {
        if (fmt[i] == '{' && fmt[i + 1] == '}') { placeholders++; i++; }
    }
    total = n - placeholders * 2;
    for (int64_t i = 0; i < placeholders && i + 1 < argc; i++)
        total += my_strlen(value_to_cstr(args[i + 1]));
    char* out = (char*)no_crt_alloc(total + 1);
    int64_t pos = 0, argi = 1;
    for (int64_t i = 0; i < n; ) {
        if (i + 1 < n && fmt[i] == '{' && fmt[i + 1] == '}') {
            if (argi < argc) {
                char* a = value_to_cstr(args[argi++]);
                int64_t al = my_strlen(a);
                for (int64_t j = 0; j < al; j++) out[pos++] = a[j];
            }
            i += 2;
        } else {
            out[pos++] = fmt[i++];
        }
    }
    out[pos] = 0;
    result->tag = VAL_STR; result->s = out;
}

static void dict_method_get(Value* result, Value** args, int argc) {
    if (args[0]->tag != VAL_DICT) { result->tag = VAL_NONE; return; }
    EclDict* d = (EclDict*)args[0]->ptr;
    EclDictEntry* e = (argc >= 2) ? dict_find(d, *args[1]) : 0;
    if (e) { *result = e->val; value_share(result); return; }
    if (argc >= 3) { *result = *args[2]; value_share(result); return; }
    result->tag = VAL_NONE;
}

static void dict_method_has(Value* result, Value** args, int argc) {
    result->tag = VAL_BOOL; result->i = 0;
    if (args[0]->tag != VAL_DICT || argc < 2) return;
    result->i = dict_find((EclDict*)args[0]->ptr, *args[1]) != 0;
}

static void dict_method_keys(Value* result, Value** args, int argc) {
    result->tag = VAL_LIST; result->ptr = list_new();
    if (args[0]->tag != VAL_DICT) return;
    EclDict* d = (EclDict*)args[0]->ptr;
    EclList* l = (EclList*)result->ptr;
    for (EclDictEntry* e = d->head; e; e = e->order_next)
        list_push_raw(l, e->key);
}

static void dict_method_values(Value* result, Value** args, int argc) {
    result->tag = VAL_LIST; result->ptr = list_new();
    if (args[0]->tag != VAL_DICT) return;
    EclDict* d = (EclDict*)args[0]->ptr;
    EclList* l = (EclList*)result->ptr;
    for (EclDictEntry* e = d->head; e; e = e->order_next)
        list_push_raw(l, e->val);
}

static Value make_bound_builtin(Value* self, void* fn) {
    EclBoundMethod* bm = (EclBoundMethod*)no_crt_alloc(sizeof(EclBoundMethod));
    bm->self = 0; bm->m = 0; bm->builtin = fn;
    bm->self_val = *self;
    value_share(&bm->self_val);
    Value out; out.tag = VAL_BOUND; out.ptr = bm;
    return out;
}

void runtime_new_code(Value* out, void* code) {
    EclCode* c = (EclCode*)no_crt_alloc(sizeof(EclCode));
    c->code = code;
    out->tag = VAL_CODE;
    out->ptr = c;
}

static void runtime_code_run(Value* result, Value** args, int argc) {
    result->tag = VAL_NONE; result->i = 0;
    if (argc < 1 || args[0]->tag != VAL_CODE) return;
    EclCode* c = (EclCode*)args[0]->ptr;
    ((EclFuncPtr)c->code)(result, 0, 0);
}

void runtime_get_attr(Value* out, Value* obj, const char* name) {
    if (obj->tag == VAL_STRUCT) {
        runtime_struct_get_field(out, obj, name);
        return;
    }
    if (obj->tag == VAL_CODE) {
        if (my_strcmp(name, "run") == 0) {
            *out = make_bound_builtin(obj, (void*)runtime_code_run);
            return;
        }
        out->tag = VAL_NONE; out->i = 0; return;
    }
    if (obj->tag == VAL_EXCLASS) {
        out->tag = VAL_STR;
        out->s = (my_strcmp(name, "exc_type") == 0) ? obj->s : my_strdup("");
        return;
    }
    if (obj->tag == VAL_EXC) {
        EclExc* e = (EclExc*)obj->ptr;
        out->tag = VAL_STR;
        if (my_strcmp(name, "message") == 0) out->s = e->msg;
        else if (my_strcmp(name, "exc_type") == 0) out->s = e->type;
        else out->s = my_strdup("");
        return;
    }
    if (obj->tag == VAL_BASEPROXY) {
        EclBaseProxy* p = (EclBaseProxy*)obj->ptr;
        EclAttr* a = find_attr(p->inst, name);
        if (a) { *out = a->value; value_share(out); return; }
        EclMethod* m = find_method(p->base, name);
        if (m) {
            out->tag = VAL_BOUND;
            out->ptr = make_bound(p->inst, m);
            return;
        }
        out->tag = VAL_NONE; out->i = 0; return;
    }
    if (obj->tag == VAL_STR) {
        if (my_strcmp(name, "upper") == 0) { *out = make_bound_builtin(obj, (void*)str_method_upper); return; }
        if (my_strcmp(name, "lower") == 0) { *out = make_bound_builtin(obj, (void*)str_method_lower); return; }
        if (my_strcmp(name, "strip") == 0) { *out = make_bound_builtin(obj, (void*)str_method_strip); return; }
        if (my_strcmp(name, "split") == 0) { *out = make_bound_builtin(obj, (void*)str_method_split); return; }
        if (my_strcmp(name, "join") == 0) { *out = make_bound_builtin(obj, (void*)str_method_join); return; }
        if (my_strcmp(name, "find") == 0) { *out = make_bound_builtin(obj, (void*)str_method_find); return; }
        if (my_strcmp(name, "replace") == 0) { *out = make_bound_builtin(obj, (void*)str_method_replace); return; }
        if (my_strcmp(name, "format") == 0) { *out = make_bound_builtin(obj, (void*)str_method_format); return; }
        out->tag = VAL_NONE; out->i = 0; return;
    }
    if (obj->tag == VAL_DICT) {
        if (my_strcmp(name, "get") == 0) { *out = make_bound_builtin(obj, (void*)dict_method_get); return; }
        if (my_strcmp(name, "has") == 0) { *out = make_bound_builtin(obj, (void*)dict_method_has); return; }
        if (my_strcmp(name, "keys") == 0) { *out = make_bound_builtin(obj, (void*)dict_method_keys); return; }
        if (my_strcmp(name, "values") == 0) { *out = make_bound_builtin(obj, (void*)dict_method_values); return; }
        {
            Value key; key.tag = VAL_STR; key.s = (char*)name;
            EclDictEntry* e = dict_find((EclDict*)obj->ptr, key);
            if (e) { *out = e->val; value_share(out); return; }
        }
        out->tag = VAL_NONE; out->i = 0; return;
    }
    if (obj->tag == VAL_INSTANCE) {
        EclInstance* inst = (EclInstance*)obj->ptr;
        EclAttr* a = find_attr(inst, name);
        if (a) { *out = a->value; value_share(out); return; }
        EclMethod* m = find_method(inst->cls, name);
        if (m) {
            out->tag = VAL_BOUND;
            out->ptr = make_bound(inst, m);
            value_share(obj);
            return;
        }
        if (is_dunder(name)) { out->tag = VAL_NULL; out->i = 0; return; }
        out->tag = VAL_NONE; out->i = 0; return;
    }
    if (obj->tag == VAL_CLASS) {
        EclClass* cls = (EclClass*)obj->ptr;
        EclMethod* m = find_method(cls, name);
        if (m) { out->tag = VAL_FUNCTION; out->ptr = m->code; return; }
        if (is_dunder(name)) { out->tag = VAL_NULL; out->i = 0; return; }
        out->tag = VAL_NONE; out->i = 0; return;
    }
    if (obj->tag == VAL_LIST) {
        if (my_strcmp(name, "append") == 0) {
            EclBoundMethod* bm = (EclBoundMethod*)no_crt_alloc(sizeof(EclBoundMethod));
            bm->self = 0; bm->m = 0;
            bm->builtin = (void*)builtin_list_append;
            bm->self_val = *obj;
            value_share(&bm->self_val);
            out->tag = VAL_BOUND;
            out->ptr = bm;
            return;
        }
    }
    if (obj->tag == VAL_CODE) {
        if (my_strcmp(name, "run") == 0) {
            runtime_raise_error("NotImplementedError: code 对象在 AOT 模式下不支持");
        }
        out->tag = VAL_NONE; out->i = 0; return;
    }
    out->tag = VAL_NONE; out->i = 0;
}
void runtime_set_attr(Value* obj, const char* name, Value* v) {
    if (obj->tag == VAL_STRUCT) {
        runtime_struct_set_field(obj, name, v);
        return;
    }
    if (obj->tag == VAL_BASEPROXY) {
        obj->tag = VAL_INSTANCE;
        obj->ptr = ((EclBaseProxy*)obj->ptr)->inst;
    }
    if (obj->tag == VAL_INSTANCE) {
        EclInstance* inst = (EclInstance*)obj->ptr;
        EclAttr* a = find_attr(inst, name);
        if (a) {
            value_release(&a->value);
            a->value = *v;
            value_share(v);
            return;
        }
        a = (EclAttr*)no_crt_alloc(sizeof(EclAttr));
        a->name = my_strdup(name);
        a->value = *v;
        value_share(v);
        a->next = inst->attrs;
        inst->attrs = a;
    }
}
void runtime_new_list(Value* out) { out->tag = VAL_LIST; out->ptr = list_new(); }
void runtime_list_append(Value* list_val, Value* item) {
    if (list_val->tag != VAL_LIST) return;
    list_push_raw((EclList*)list_val->ptr, *item);
}
void runtime_new_dict(Value* out) { out->tag = VAL_DICT; out->ptr = dict_new(); }
void runtime_dict_set(Value* dict_val, Value* key, Value* val) {
    if (dict_val->tag != VAL_DICT) return;
    dict_put_raw((EclDict*)dict_val->ptr, *key, *val);
}
void runtime_get_item(Value* out, Value* obj, Value* idx) {
    if (obj->tag == VAL_BUFFER && idx->tag == VAL_INT) {
        EclBuffer* b = (EclBuffer*)obj->ptr;
        int64_t i = idx->i;
        if (i < 0) i += b->size;
        if (i < 0 || i >= b->size) { out->tag = VAL_NONE; out->i = 0; return; }
        out->tag = VAL_INT;
        out->i = b->data[i];
        return;
    }
    if ((obj->tag == VAL_LIST || obj->tag == VAL_TUPLE) && idx->tag == VAL_INT) {
        EclList* l = (EclList*)obj->ptr; int64_t i = idx->i;
        if (i < 0) i += l->len;
        if (i < 0 || i >= l->len) { out->tag = VAL_NONE; out->i = 0; return; }
        *out = l->items[i];
        value_share(out);
        return;
    }

    if (obj->tag == VAL_DICT) {
        EclDict* d = (EclDict*)obj->ptr;
        EclDictEntry* e = dict_find(d, *idx);
        if (e) { *out = e->val; value_share(out); return; }
        out->tag = VAL_NONE; out->i = 0; return;
    }

    out->tag = VAL_NONE; out->i = 0;
}
void runtime_set_item(Value* obj, Value* idx, Value* val) {
    if (obj->tag == VAL_BUFFER && idx->tag == VAL_INT) {
        EclBuffer* b = (EclBuffer*)obj->ptr;
        int64_t i = idx->i;
        if (i < 0) i += b->size;
        if (i < 0 || i >= b->size) return;
        b->data[i] = (unsigned char)(dll_to_int(val) & 0xFF);
        return;
    }
    if (obj->tag == VAL_LIST && idx->tag == VAL_INT) {
        EclList* l = (EclList*)obj->ptr; int64_t i = idx->i;
        if (i < 0) i += l->len;
        if (i < 0 || i >= l->len) return;
        value_release(&l->items[i]);
        l->items[i] = *val;
        value_share(val);
        return;
    }
    if (obj->tag == VAL_DICT) {
        dict_put_raw((EclDict*)obj->ptr, *idx, *val);
    }
}

// ============================================================
// 内置函数
// ============================================================
static int format_value(Value* v, char* out, int cap, int quote_str) {
    int n = 0;
#define PUT(ch) do { if (n < cap) out[n++] = (char)(ch); } while (0)
#define PUTS(s) do { const char* _s = (s); while (*_s) PUT(*_s++); } while (0)
    switch (v->tag) {
        case VAL_INT: case VAL_I64: case VAL_I32: {
            char b[32]; int k = itoa10(v->i, b);
            for (int i = 0; i < k; i++) PUT(b[i]);
            break;
        }
        case VAL_BOOL:
            PUTS(v->i ? "True" : "False");
            break;
        case VAL_FLOAT:
            { char b[64]; int k = dtoa(v->f, b); for (int i = 0; i < k; i++) PUT(b[i]); }
            break;
        case VAL_DOUBLE:
            { char b[64]; int k; if (v->f == (double)(int64_t)v->f) k = itoa10((int64_t)v->f, b); else k = dtoa(v->f, b);
              for (int i = 0; i < k; i++) PUT(b[i]); }
            break;
        case VAL_STR:
            if (quote_str) PUT('\'');
            { const char* s = v->s; while (*s) PUT(*s++); }
            if (quote_str) PUT('\'');
            break;
        case VAL_NULL: PUTS("null"); break;
        case VAL_NONE: PUTS("none"); break;
        case VAL_UNKNOWN: PUTS("unknown"); break;
        case VAL_BIGINT: {
            char* s = bi_to_dec((EclBigInt*)v->ptr);
            PUTS(s); break;
        }
        case VAL_LIST: case VAL_TUPLE: {
            EclList* l = (EclList*)v->ptr;
            PUT(v->tag == VAL_LIST ? '[' : '(');
            for (int64_t i = 0; i < l->len; i++) {
                if (i) { PUT(','); PUT(' '); }
                n += format_value(&l->items[i], out + n, cap - n, 1);
            }
            if (v->tag == VAL_TUPLE && l->len == 1) PUT(',');
            PUT(v->tag == VAL_LIST ? ']' : ')');
            break;
        }
        case VAL_DICT: {
            EclDict* d = (EclDict*)v->ptr;
            int first = 1;
            PUT('{');
            for (EclDictEntry* e = d->head; e; e = e->order_next) {
                if (!first) { PUT(','); PUT(' '); }
                first = 0;
                n += format_value(&e->key, out + n, cap - n, 1);
                PUT(':'); PUT(' ');
                n += format_value(&e->val, out + n, cap - n, 1);
            }
            PUT('}');
            break;
        }
        case VAL_INSTANCE: {
            EclInstance* inst = (EclInstance*)v->ptr;
            EclMethod* m = find_method(inst->cls, "__str__");
            if (m) {
                Value r; Value selfv; selfv.tag = VAL_INSTANCE; selfv.ptr = inst;
                call_bound(&r, m->code, m->sig, 0, inst, 0, 0, 0);
                n += format_value(&r, out + n, cap - n, 0);
                value_release(&r);
            } else {
                PUT('<'); PUTS(inst->cls->name); PUTS(" object>");
            }
            break;
        }
        case VAL_CLASS:
            PUT('<'); PUTS("class "); PUTS(((EclClass*)v->ptr)->name); PUT('>');
            break;
        case VAL_FUNCTION: case VAL_BOUND: PUTS("<function>"); break;
        case VAL_EXC: PUTS(((EclExc*)v->ptr)->msg); break;
        case VAL_EXCLASS: PUT('<'); PUTS("class "); PUTS(v->s); PUT('>'); break;
        case VAL_CODE: PUTS("<code>"); break;
        default: PUTS("<value>"); break;
    }
    if (n < cap) out[n] = 0;
    return n;
#undef PUT
#undef PUTS
}

void runtime_str(Value* result, Value* v) {
    char* buf = (char*)no_crt_alloc(8192);
    format_value(v, buf, 8191, 0);
    result->tag = VAL_STR;
    result->s = buf;
}
void runtime_len(Value* result, Value* v) {
    result->tag = VAL_INT;
    if (v->tag == VAL_STR) result->i = my_strlen(v->s);
    else if (v->tag == VAL_LIST || v->tag == VAL_TUPLE) result->i = ((EclList*)v->ptr)->len;
    else if (v->tag == VAL_DICT) result->i = ((EclDict*)v->ptr)->size;
    else if (v->tag == VAL_BUFFER) result->i = ((EclBuffer*)v->ptr)->size;
    else result->i = 0;
}
// 解析 int("...")：支持空白、正负号、0x/0o/0b 前缀和下划线。
// 返回值按 64 位回绕（AOT 的 int 实际是 int64）。
static int parse_int_string(const char* s, int64_t* out) {
    while (*s == ' ' || *s == '\t' || *s == '\r' || *s == '\n' ||
           *s == '\v' || *s == '\f') s++;
    int neg = 0;
    if (*s == '+' || *s == '-') { neg = (*s == '-'); s++; }
    int base = 10;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) { base = 16; s += 2; }
    else if (s[0] == '0' && (s[1] == 'o' || s[1] == 'O')) { base = 8; s += 2; }
    else if (s[0] == '0' && (s[1] == 'b' || s[1] == 'B')) { base = 2; s += 2; }

    uint64_t acc = 0;
    int digits = 0, last_digit = 0, last_us = 0;
    while (*s) {
        char c = *s;
        if (c == '_') {
            if (!last_digit) return 0;
            last_digit = 0;
            last_us = 1;
            s++;
            continue;
        }
        int d;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
        else break;
        if (d >= base) break;
        acc = acc * (uint64_t)base + (uint64_t)d;
        digits++;
        last_digit = 1;
        last_us = 0;
        s++;
    }
    if (!digits || last_us) return 0;
    while (*s == ' ' || *s == '\t' || *s == '\r' || *s == '\n' ||
           *s == '\v' || *s == '\f') s++;
    if (*s) return 0;
    if (neg) acc = (uint64_t)(0 - acc);
    *out = (int64_t)acc;
    return 1;
}

void runtime_int(Value* result, Value* v) {
    result->tag = VAL_INT;
    if (v->tag == VAL_INT || v->tag == VAL_I64 || v->tag == VAL_I32 || v->tag == VAL_BOOL) result->i = v->i;
    else if (v->tag == VAL_FLOAT || v->tag == VAL_DOUBLE) result->i = (int64_t)v->f;
    else if (v->tag == VAL_BIGINT) {
        EclBigInt* b = (EclBigInt*)v->ptr;
        int64_t r = 0;
        for (size_t i = 0; i < b->len && i < 2; i++) {
            r |= ((int64_t)b->limbs[i] << (32 * i));
        }
        result->i = b->sign < 0 ? -r : r;
    }
    else if (v->tag == VAL_STR) {
        int64_t parsed = 0;
        if (!parse_int_string(v->s, &parsed)) {
            result->i = 0;
            runtime_raise_error("TypeError: 无法将 str 转换为 int");
            return;
        }
        result->i = parsed;
    }
    else result->i = 0;
}
void runtime_float(Value* result, Value* v) {
    result->tag = VAL_FLOAT;
    if (v->tag == VAL_FLOAT || v->tag == VAL_DOUBLE) result->f = v->f;
    else if (v->tag == VAL_INT || v->tag == VAL_I64 || v->tag == VAL_I32 || v->tag == VAL_BOOL) result->f = (double)v->i;
    else if (v->tag == VAL_BIGINT) {
        EclBigInt* b = (EclBigInt*)v->ptr;
        double r = 0.0;
        for (size_t i = b->len; i-- > 0; ) {
            r = r * 4294967296.0 + b->limbs[i];
        }
        result->f = b->sign < 0 ? -r : r;
    }
    else result->f = 0.0;
}
void runtime_bool(Value* result, Value* v) { result->tag = VAL_BOOL; result->i = runtime_truthy(v); }
void runtime_type(Value* result, Value* v) {
    result->tag = VAL_STR;
    switch (v->tag) {
        case VAL_INT: result->s = my_strdup("int"); break;
        case VAL_I64: result->s = my_strdup("i64"); break;
        case VAL_I32: result->s = my_strdup("i32"); break;
        case VAL_BOOL: result->s = my_strdup("bool"); break;
        case VAL_FLOAT: result->s = my_strdup("float"); break;
        case VAL_DOUBLE: result->s = my_strdup("double"); break;
        case VAL_STR: result->s = my_strdup("str"); break;
        case VAL_BUFFER: result->s = my_strdup("buffer"); break;
        case VAL_STRUCT: result->s = my_strdup(((EclStruct*)v->ptr)->type_name); break;
        case VAL_WSTR: result->s = my_strdup("wstr"); break;
        case VAL_LIST: result->s = my_strdup("list"); break;
        case VAL_TUPLE: result->s = my_strdup("tuple"); break;
        case VAL_DICT: result->s = my_strdup("dict"); break;
        case VAL_NULL: result->s = my_strdup("null"); break;
        case VAL_NONE: result->s = my_strdup("none"); break;
        case VAL_UNKNOWN: result->s = my_strdup("unknown"); break;
        case VAL_EXC: result->s = my_strdup(((EclExc*)v->ptr)->type); break;
        case VAL_BASEPROXY: result->s = my_strdup(((EclBaseProxy*)v->ptr)->base->name); break;
        case VAL_EXCLASS: result->s = my_strdup("class"); break;
        case VAL_CODE: result->s = my_strdup("code"); break;
        case VAL_FUNCTION: case VAL_BOUND: result->s = my_strdup("function"); break;
        case VAL_CLASS: result->s = my_strdup("class"); break;
        case VAL_BIGINT: result->s = my_strdup("bi"); break;
        case VAL_INSTANCE: result->s = ((EclInstance*)v->ptr)->cls->name; break;
        default: result->s = my_strdup("unknown"); break;
    }
}
void runtime_range(Value* result, Value* start, Value* end) {
    int64_t a = (start->tag == VAL_INT) ? start->i : 0;
    int64_t b = (end->tag == VAL_INT) ? end->i : 0;
    EclList* l = list_new();
    for (int64_t i = a; i < b; i++) {
        Value v; v.tag = VAL_INT; v.i = i;
        list_push_raw(l, v);
    }
    result->tag = VAL_LIST; result->ptr = l;
}

// ============================================================
// 复合构造（tuple / 解包 / match / using 等）
// ============================================================
static int numeric_value(Value* v, double* out) {
    if (v->tag == VAL_INT || v->tag == VAL_I64 || v->tag == VAL_I32 || v->tag == VAL_BOOL) { *out = (double)v->i; return 1; }
    if (v->tag == VAL_FLOAT || v->tag == VAL_DOUBLE) { *out = v->f; return 1; }
    if (v->tag == VAL_BIGINT) {
        EclBigInt* b = (EclBigInt*)v->ptr;
        double r = 0.0;
        for (size_t i = b->len; i-- > 0;)
            r = r * 4294967296.0 + b->limbs[i];
        *out = b->sign < 0 ? -r : r;
        return 1;
    }
    return 0;
}

int64_t runtime_values_equal(Value* a, Value* b) {
    if (a->tag == VAL_UNKNOWN || b->tag == VAL_UNKNOWN)
        return a->tag == b->tag;
    if (a->tag == b->tag) return value_eq_raw(*a, *b);
    double x, y;
    if (numeric_value(a, &x) && numeric_value(b, &y)) return x == y;
    return 0;
}

void runtime_match_eq(Value* out, Value* a, Value* b) {
    out->tag = VAL_BOOL;
    out->i = runtime_values_equal(a, b);
}

void runtime_is_type(Value* out, Value* v, const char* name) {
    int r = 0;
    if (my_strcmp(name, "any") == 0) r = 1;
    else if (my_strcmp(name, "unknown") == 0) r = (v->tag == VAL_UNKNOWN);
    else if (my_strcmp(name, "none") == 0) r = (v->tag == VAL_NONE);
    else if (my_strcmp(name, "null") == 0) r = (v->tag == VAL_NULL);
    else if (my_strcmp(name, "int") == 0) r = (v->tag == VAL_INT);
    else if (my_strcmp(name, "i64") == 0) r = (v->tag == VAL_I64);
    else if (my_strcmp(name, "i32") == 0) r = (v->tag == VAL_I32);
    else if (my_strcmp(name, "cstr") == 0) r = (v->tag == VAL_STR);
    else if (my_strcmp(name, "ptr") == 0) r = (v->tag == VAL_INT);
    else if (my_strcmp(name, "bool") == 0) r = (v->tag == VAL_BOOL);
    else if (my_strcmp(name, "float") == 0) r = (v->tag == VAL_FLOAT);
    else if (my_strcmp(name, "double") == 0) r = (v->tag == VAL_DOUBLE);
    else if (my_strcmp(name, "str") == 0) r = (v->tag == VAL_STR);
    else if (my_strcmp(name, "list") == 0) r = (v->tag == VAL_LIST);
    else if (my_strcmp(name, "tuple") == 0) r = (v->tag == VAL_TUPLE);
    else if (my_strcmp(name, "dict") == 0) r = (v->tag == VAL_DICT);
    else if (my_strcmp(name, "code") == 0) r = (v->tag == VAL_CODE);
    else if (my_strcmp(name, "function") == 0)
        r = (v->tag == VAL_FUNCTION || v->tag == VAL_BOUND);
    else if (v->tag == VAL_INSTANCE)
        r = (my_strcmp(((EclInstance*)v->ptr)->cls->name, name) == 0);
    else if (v->tag == VAL_CLASS)
        r = (my_strcmp(((EclClass*)v->ptr)->name, name) == 0);
    out->tag = VAL_BOOL;
    out->i = r;
}

void runtime_to_tuple(Value* out, Value* src) {
    EclList* l = (EclList*)src->ptr;
    EclList* t = list_new();
    for (int64_t i = 0; i < l->len; i++) list_push_raw(t, l->items[i]);
    out->tag = VAL_TUPLE;
    out->ptr = t;
}

void runtime_iter_list(Value* out, Value* v) {
    EclList* l = list_new();
    if (v->tag == VAL_LIST || v->tag == VAL_TUPLE) {
        EclList* src = (EclList*)v->ptr;
        for (int64_t i = 0; i < src->len; i++) list_push_raw(l, src->items[i]);
    } else if (v->tag == VAL_STR) {
        int64_t n = my_strlen(v->s);
        for (int64_t i = 0; i < n; i++) {
            char* ch = (char*)no_crt_alloc(2);
            ch[0] = v->s[i]; ch[1] = 0;
            Value item; item.tag = VAL_STR; item.s = ch;
            list_push_raw(l, item);
        }
    } else if (v->tag == VAL_DICT) {
        EclDict* d = (EclDict*)v->ptr;
        for (EclDictEntry* e = d->head; e; e = e->order_next)
            list_push_raw(l, e->key);
    } else {
        runtime_raise_error("TypeError: 对象不可迭代");
    }
    out->tag = VAL_LIST;
    out->ptr = l;
}

void runtime_list_extend(Value* dest, Value* src) {
    if (dest->tag != VAL_LIST) return;
    if (src->tag != VAL_LIST && src->tag != VAL_TUPLE) {
        runtime_raise_error("TypeError: * 解包要求 list 或 tuple");
        return;
    }
    EclList* d = (EclList*)dest->ptr;
    EclList* s = (EclList*)src->ptr;
    for (int64_t i = 0; i < s->len; i++) list_push_raw(d, s->items[i]);
}

void runtime_dict_update(Value* dest, Value* src, int64_t strict) {
    if (dest->tag != VAL_DICT) return;
    if (src->tag != VAL_DICT) {
        runtime_raise_error("TypeError: ** 解包要求 dict");
        return;
    }
    EclDict* d = (EclDict*)dest->ptr;
    EclDict* s = (EclDict*)src->ptr;
    for (EclDictEntry* e = s->head; e; e = e->order_next) {
        if (strict) {
            if (e->key.tag != VAL_STR) {
                runtime_raise_error("TypeError: ** 解包的键必须是 str");
                return;
            }
        }
        dict_put_raw(d, e->key, e->val);
    }
}

void runtime_expect_len(Value* v, int64_t expected) {
    if (v->tag != VAL_LIST && v->tag != VAL_TUPLE && v->tag != VAL_STR) return;
    int64_t actual = (v->tag == VAL_STR) ? my_strlen(v->s)
                                         : ((EclList*)v->ptr)->len;
    if (actual != expected) {
        runtime_raise_error("ValueError: 固定长度 list 元素数量不匹配");
    }
}

void runtime_convert(Value* out, Value* v, const char* type) {
    if (my_strcmp(type, "int") == 0) {
        runtime_int(out, v);
    } else if (my_strcmp(type, "i64") == 0) {
        runtime_int(out, v);
        out->tag = VAL_I64;
    } else if (my_strcmp(type, "i32") == 0) {
        runtime_int(out, v);
        out->tag = VAL_I32;
        out->i = (int32_t)out->i;
    } else if (my_strcmp(type, "float") == 0) {
        runtime_float(out, v);
    } else if (my_strcmp(type, "double") == 0) {
        runtime_float(out, v);
        out->tag = VAL_DOUBLE;
    } else if (my_strcmp(type, "str") == 0) {
        runtime_str(out, v);
    } else if (my_strcmp(type, "cstr") == 0) {
        // cstr 是外部传入的 UTF-8 字节指针，原样保留，不做任何转换
        *out = *v;
    } else if (my_strcmp(type, "ptr") == 0) {
        *out = *v;
    } else if (my_strcmp(type, "bool") == 0) {
        runtime_bool(out, v);
    } else {
        *out = *v;
        value_share(out);
    }
}

void runtime_using_enter(Value* out, Value* v) {    if (v->tag != VAL_INSTANCE && v->tag != VAL_CLASS &&
        v->tag != VAL_BASEPROXY) {
        runtime_raise_error("TypeError: using 的对象必须是实例或类");
        out->tag = VAL_NONE; out->i = 0;
        return;
    }
    *out = *v;
    value_share(out);
}

void runtime_with_enter(Value* entered, Value* leave_out, Value* obj) {
    if (obj->tag != VAL_INSTANCE && obj->tag != VAL_BASEPROXY) {
        runtime_raise_error("TypeError: with 的对象必须实现 __enter__ 和 __leave__");
        return;
    }
    Value enter_fn, leave_fn;
    runtime_get_attr(&enter_fn, obj, "__enter__");
    runtime_get_attr(&leave_fn, obj, "__leave__");
    if (enter_fn.tag == VAL_NULL || enter_fn.tag == VAL_NONE ||
        leave_fn.tag == VAL_NULL || leave_fn.tag == VAL_NONE) {
        runtime_raise_error("TypeError: with 的对象必须实现 __enter__ 和 __leave__");
        return;
    }
    runtime_call(entered, &enter_fn, 0, 0);
    *leave_out = leave_fn;
    value_share(leave_out);
}

int64_t runtime_get_attr_opt(Value* out, Value* obj, const char* name) {
    char probe[1];
    (void)probe;
    if (obj->tag == VAL_INSTANCE) {
        EclInstance* inst = (EclInstance*)obj->ptr;
        EclAttr* a = find_attr(inst, name);
        if (a) { *out = a->value; value_share(out); return 1; }
        EclMethod* m = find_method(inst->cls, name);
        if (m) {
            out->tag = VAL_BOUND;
            out->ptr = make_bound(inst, m);
            value_share(obj);
            return 1;
        }
        return 0;
    }
    if (obj->tag == VAL_CLASS) {
        EclClass* cls = (EclClass*)obj->ptr;
        EclMethod* m = find_method(cls, name);
        if (m) { runtime_get_attr(out, obj, name); return 1; }
        return 0;
    }
    if (obj->tag == VAL_BASEPROXY) {
        runtime_get_attr(out, obj, name);
        return out->tag != VAL_NONE;
    }
    return 0;
}

void runtime_class_add_base(EclClass* cls, Value* base) {
    if (base->tag != VAL_CLASS) return;
    if (cls->base_count >= 64) return;
    cls->bases[cls->base_count++] = (EclClass*)base->ptr;
}

void runtime_base_view(Value* out, Value* self, const char* base_name) {
    if (self->tag != VAL_INSTANCE) {
        out->tag = VAL_NONE; out->i = 0;
        return;
    }
    Value* cls_val = get_var(base_name);
    if (cls_val && cls_val->tag == VAL_CLASS) {
        EclBaseProxy* p = (EclBaseProxy*)no_crt_alloc(sizeof(EclBaseProxy));
        p->inst = (EclInstance*)self->ptr;
        p->base = (EclClass*)cls_val->ptr;
        out->tag = VAL_BASEPROXY;
        out->ptr = p;
    } else {
        out->tag = VAL_NONE; out->i = 0;
    }
}

// ============================================================
// 异常
// ============================================================
typedef struct {
    int64_t rbx, rbp, rdi, rsi, rsp, r12, r13, r14, r15, rip;
} jmp_buf_t;

int64_t runtime_setjmp(jmp_buf_t* env);
void runtime_longjmp(jmp_buf_t* env, int64_t val);

#define MAX_TRY_DEPTH 32
static jmp_buf_t* exception_stack[MAX_TRY_DEPTH];
static int exception_depth = 0;
static int exc_active = 0;
static char* exc_type = 0;
static char* exc_message = 0;

static void set_current_exception(const char* msg, const char* forced_type) {
    const char* colon = 0;
    for (const char* p = msg; *p; p++) {
        if (*p == ':') { colon = p; break; }
    }
    if (forced_type) {
        exc_type = my_strdup(forced_type);
        exc_message = my_strdup(msg);
    } else if (colon) {
        int64_t n = colon - msg;
        exc_type = (char*)no_crt_alloc(n + 1);
        for (int64_t i = 0; i < n; i++) exc_type[i] = msg[i];
        exc_type[n] = 0;
        const char* rest = colon + 1;
        if (*rest == ' ') rest++;
        exc_message = my_strdup(rest);
    } else {
        exc_type = my_strdup(msg);
        exc_message = my_strdup("");
    }
    exc_active = 1;
}

void runtime_raise_error(const char* msg) {
    set_current_exception(msg, 0);
    if (exception_depth > 0) {
        exception_depth--;
        runtime_longjmp(exception_stack[exception_depth], 1);
    }
    write_str("Unhandled Exception: ", 21);
    write_str(msg, my_strlen(msg));
    write_str("\n", 1);
    runtime_shutdown();
    ExitProcess(1);
}

void runtime_raise_value(Value* v) {
    if (v->tag == VAL_EXC) {
        EclExc* e = (EclExc*)v->ptr;
        set_current_exception(e->msg, e->type);
    } else if (v->tag == VAL_EXCLASS) {
        set_current_exception("", v->s);
    } else if (v->tag == VAL_STR) {
        set_current_exception(v->s, 0);
    } else {
        set_current_exception("Exception", 0);
    }
    if (exception_depth > 0) {
        exception_depth--;
        runtime_longjmp(exception_stack[exception_depth], 1);
    }
    write_str("Unhandled Exception: ", 21);
    write_str(exc_type, my_strlen(exc_type));
    write_str("\n", 1);
    runtime_shutdown();
    ExitProcess(1);
}

void runtime_reraise(void) {
    if (!exc_active) runtime_raise_error("RuntimeError: 没有可抛出的异常");
    if (exception_depth > 0) {
        exception_depth--;
        runtime_longjmp(exception_stack[exception_depth], 1);
    }
    write_str("Unhandled Exception: ", 21);
    write_str(exc_type, my_strlen(exc_type));
    write_str("\n", 1);
    runtime_shutdown();
    ExitProcess(1);
}

void runtime_exc_match(Value* out, const char* wanted) {
    int r = 0;
    if (exc_active) {
        if (!wanted) r = 1;
        else if (my_strcmp(wanted, "Exception") == 0) r = 1;
        else r = (my_strcmp(exc_type, wanted) == 0);
    }
    out->tag = VAL_BOOL;
    out->i = r;
}

void runtime_get_exc_value(Value* out) {
    EclExc* e = (EclExc*)no_crt_alloc(sizeof(EclExc));
    e->type = exc_active ? exc_type : my_strdup("none");
    e->msg = exc_active ? exc_message : my_strdup("");
    out->tag = VAL_EXC;
    out->ptr = e;
}

void runtime_get_exc_type(Value* out) {
    if (exc_active) {
        out->tag = VAL_EXCLASS;
        out->s = exc_type;
    } else {
        out->tag = VAL_NONE;
        out->i = 0;
    }
}
void runtime_push_exception(jmp_buf_t* env) {
    if (exception_depth < MAX_TRY_DEPTH) exception_stack[exception_depth++] = env;
}
void runtime_pop_exception() {
    if (exception_depth > 0) exception_depth--;
}

// ============================================================
// free / store_clone / shutdown
// ============================================================
void runtime_new_buffer(Value* result, Value* size) {
    int64_t n = dll_to_int(size);
    if (n <= 0) n = 1;
    EclBuffer* b = (EclBuffer*)no_crt_alloc(sizeof(EclBuffer));
    b->size = n;
    b->data = (unsigned char*)no_crt_alloc((size_t)n);
    memset(b->data, 0, (uint64_t)n);
    result->tag = VAL_BUFFER;
    result->ptr = b;
}

void runtime_buffer_address(Value* result, Value* buf) {
    if (buf && buf->tag == VAL_STRUCT) {
        runtime_struct_address(result, buf);
        return;
    }
    result->tag = VAL_INT;
    result->i = (buf && buf->tag == VAL_BUFFER)
        ? (int64_t)(uintptr_t)((EclBuffer*)buf->ptr)->data : 0;
}

void runtime_free(Value* v) {
    // free 立刻物理释放该槽位指向的堆内存，并置为 null
    value_destroy(v);
    v->tag = VAL_NULL;
    v->i = 0;
}

void runtime_free_item(Value* container, Value* idx) {
    if (!container || !idx) return;
    if (container->tag == VAL_LIST || container->tag == VAL_TUPLE) {
        EclList* l = (EclList*)container->ptr;
        if (idx->tag == VAL_INT) {
            int64_t i = idx->i;
            if (i < 0) i += l->len;
            if (i >= 0 && i < l->len) {
                value_destroy(&l->items[i]);
                l->items[i].tag = VAL_NULL;
                l->items[i].i = 0;
            }
        }
    } else if (container->tag == VAL_DICT) {
        EclDict* d = (EclDict*)container->ptr;
        EclDictEntry* e = dict_find(d, *idx);
        if (e) {
            value_destroy(&e->val);
            e->val.tag = VAL_NULL;
            e->val.i = 0;
        }
    }
}

void runtime_free_attr(Value* obj, const char* name) {
    if (!obj) return;
    if (obj->tag == VAL_INSTANCE) {
        EclInstance* inst = (EclInstance*)obj->ptr;
        for (EclAttr* a = inst->attrs; a; a = a->next) {
            if (my_strcmp(a->name, name) == 0) {
                value_destroy(&a->value);
                a->value.tag = VAL_NULL;
                a->value.i = 0;
                return;
            }
        }
    } else if (obj->tag == VAL_DICT) {
        Value key;
        key.tag = VAL_STR;
        key.s = (char*)name;
        EclDictEntry* e = dict_find((EclDict*)obj->ptr, key);
        if (e) {
            value_destroy(&e->val);
            e->val.tag = VAL_NULL;
            e->val.i = 0;
        }
    }
}

void runtime_store_clone(Value* slot, Value* src) {
    Value* dst = deref_ptr(slot);
    if (src->tag == VAL_NULL) {
        dst->tag = VAL_NULL;
        dst->i = 0;
        return;
    }
    *dst = value_clone(src);
}

// 兼容旧名字：AOT 的 store 现在统一走深拷贝。
void runtime_store_share(Value* slot, Value* src) {
    runtime_store_clone(slot, src);
}

void runtime_shutdown(void) {
    for (int i = 0; i < g_alloc_count; i++) {
        VirtualFree(g_allocs[i], 0, MEM_RELEASE);
    }
    g_alloc_count = 0;
}

// ============================================================
// dll_import：运行时动态调用
// ============================================================
uint64_t ecl_xmm0_result = 0;
extern uint64_t ecl_invoke(void* fn, uint64_t* slots, uint64_t types,
                           int64_t nargs);

#define DLL_MAX_LAYOUTS 64
#define DLL_MAX_FIELDS  32
#define DLL_MAX_LIBS    16

typedef struct {
    char name[64];
    int nfields;
    char ftype[DLL_MAX_FIELDS][32];
    char fname[DLL_MAX_FIELDS][32];
    int foffset[DLL_MAX_FIELDS];
    int fsize[DLL_MAX_FIELDS];
    int falign[DLL_MAX_FIELDS];
    int size, align, resolved, resolving;
} DllLayout;

static DllLayout dll_layouts[DLL_MAX_LAYOUTS];
static int dll_layout_count = 0;

typedef struct {
    char path[260];
    HANDLE handle;
} DllLib;
static DllLib dll_libs[DLL_MAX_LIBS];
static int dll_lib_count = 0;

static int dll_type_size(const char* t) {
    if (my_strcmp(t, "bool") == 0 || my_strcmp(t, "i32") == 0 ||
        my_strcmp(t, "float") == 0) return 4;
    return 8;
}
static int dll_type_align(const char* t) { return dll_type_size(t); }

static DllLayout* dll_find_layout(const char* name) {
    for (int i = 0; i < dll_layout_count; i++) {
        if (my_strcmp(dll_layouts[i].name, name) == 0) return &dll_layouts[i];
    }
    return 0;
}

static void dll_copy_name(char* dst, int cap, const char** pp, char stop1,
                          char stop2) {
    const char* p = *pp;
    int n = 0;
    while (*p && *p != stop1 && *p != stop2 && n < cap - 1) dst[n++] = *p++;
    dst[n] = 0;
    *pp = p;
}

static void dll_resolve_layout(DllLayout* L) {
    if (L->resolved) return;
    if (L->resolving) { L->resolved = 1; L->size = 0; L->align = 1; return; }
    L->resolving = 1;
    int off = 0, maxa = 1;
    for (int i = 0; i < L->nfields; i++) {
        int sz, al;
        DllLayout* sub = dll_find_layout(L->ftype[i]);
        if (sub) {
            dll_resolve_layout(sub);
            sz = sub->size;
            al = sub->align;
        } else {
            sz = dll_type_size(L->ftype[i]);
            al = dll_type_align(L->ftype[i]);
        }
        if (al < 1) al = 1;
        off = (off + al - 1) / al * al;
        L->foffset[i] = off;
        L->fsize[i] = sz;
        L->falign[i] = al;
        off += sz;
        if (al > maxa) maxa = al;
    }
    L->size = (off + maxa - 1) / maxa * maxa;
    L->align = maxa;
    L->resolving = 0;
    L->resolved = 1;
}

void runtime_dll_register_layouts(const char* spec) {
    dll_layout_count = 0;
    const char* p = spec;
    while (*p && dll_layout_count < DLL_MAX_LAYOUTS) {
        DllLayout* L = &dll_layouts[dll_layout_count++];
        memset(L, 0, sizeof(DllLayout));
        dll_copy_name(L->name, 64, &p, ':', ';');
        if (*p == ':') p++;
        while (*p && *p != ';' && L->nfields < DLL_MAX_FIELDS) {
            while (*p == ' ' || *p == ',') p++;
            if (*p == ';' || !*p) break;
            dll_copy_name(L->ftype[L->nfields], 32, &p, ' ', ',');
            while (*p == ' ') p++;
            dll_copy_name(L->fname[L->nfields], 32, &p, ',', ';');
            L->nfields++;
            while (*p == ' ') p++;
        }
        if (*p == ';') p++;
    }
    /* 全部登记后再解析，支持 struct 声明顺序任意/嵌套后声明 */
    for (int i = 0; i < dll_layout_count; i++) {
        dll_resolve_layout(&dll_layouts[i]);
    }
}

static wchar_t* dll_utf8_to_utf16(const char* s) {
    if (!s) s = "";
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, 0, 0);
    if (n <= 0) return 0;
    wchar_t* w = (wchar_t*)no_crt_alloc((size_t)n * 2);
    if (!w) return 0;
    MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n);
    return w;
}

static char* dll_utf16_to_utf8(const wchar_t* w) {
    if (!w) return my_strdup("");
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, 0, 0, 0, 0);
    if (n <= 0) return my_strdup("");
    char* s = (char*)no_crt_alloc((size_t)n);
    if (!s) return my_strdup("");
    WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, 0, 0);
    return s;
}

// ============================================================
// 真 struct 值（缓冲区 + 编译期布局）
// ============================================================
static DllLayout* struct_layout(Value* st) {
    if (!st || st->tag != VAL_STRUCT) return 0;
    EclStruct* s = (EclStruct*)st->ptr;
    return s ? dll_find_layout(s->type_name) : 0;
}

static int struct_field_index(DllLayout* L, const char* name) {
    for (int i = 0; i < L->nfields; i++) {
        if (my_strcmp(L->fname[i], name) == 0) return i;
    }
    return -1;
}

void runtime_struct_new(Value* result, const char* name) {
    DllLayout* L = dll_find_layout(name);
    if (!L) {
        runtime_raise_error("TypeError: 未知 struct 类型");
        result->tag = VAL_NONE;
        return;
    }
    EclStruct* s = (EclStruct*)no_crt_alloc(sizeof(EclStruct));
    s->type_name = my_strdup(name);
    s->size = L->size;
    s->data = (unsigned char*)no_crt_alloc((size_t)(L->size > 0 ? L->size : 1));
    memset(s->data, 0, (uint64_t)L->size);
    s->owned = 1;
    result->tag = VAL_STRUCT;
    result->ptr = s;
}

void runtime_struct_address(Value* result, Value* st) {
    result->tag = VAL_INT;
    result->i = 0;
    if (st && st->tag == VAL_STRUCT) {
        EclStruct* s = (EclStruct*)st->ptr;
        result->i = (int64_t)(uintptr_t)s->data;
    }
}

void runtime_struct_get_field(Value* out, Value* st, const char* name) {
    DllLayout* L = struct_layout(st);
    if (!L) { out->tag = VAL_NONE; out->i = 0; return; }
    int idx = struct_field_index(L, name);
    if (idx < 0) {
        runtime_raise_error("AttributeError: struct 没有该字段");
        out->tag = VAL_NONE;
        return;
    }
    EclStruct* s = (EclStruct*)st->ptr;
    unsigned char* p = s->data + L->foffset[idx];
    const char* t = L->ftype[idx];
    if (my_strcmp(t, "bool") == 0) {
        out->tag = VAL_BOOL; out->i = *(int32_t*)p != 0;
    } else if (my_strcmp(t, "i32") == 0) {
        out->tag = VAL_I32; out->i = *(int32_t*)p;
    } else if (my_strcmp(t, "i64") == 0) {
        out->tag = VAL_I64; out->i = *(int64_t*)p;
    } else if (my_strcmp(t, "int") == 0) {
        out->tag = VAL_INT; out->i = *(int64_t*)p;
    } else if (my_strcmp(t, "float") == 0) {
        out->tag = VAL_FLOAT; out->f = *(float*)p;
    } else if (my_strcmp(t, "double") == 0) {
        out->tag = VAL_DOUBLE; out->f = *(double*)p;
    } else if (my_strcmp(t, "ptr") == 0) {
        out->tag = VAL_INT; out->i = (int64_t)(intptr_t)(*(void**)p);
    } else if (my_strcmp(t, "str") == 0) {
        char* sval = *(char**)p;
        out->tag = VAL_STR; out->s = my_strdup(sval ? sval : "");
    } else if (my_strcmp(t, "wstr") == 0) {
        out->tag = VAL_WSTR;
        out->ptr = *(void**)p;
    } else if (my_strcmp(t, "cstr") == 0) {
        out->tag = VAL_STR;
        out->s = my_strdup(*(char**)p ? *(char**)p : "");
    } else {
        DllLayout* sub = dll_find_layout(t);
        if (sub) {
            EclStruct* ns = (EclStruct*)no_crt_alloc(sizeof(EclStruct));
            ns->type_name = my_strdup(t);
            ns->data = p;
            ns->size = sub->size;
            ns->owned = 0;
            out->tag = VAL_STRUCT;
            out->ptr = ns;
        } else {
            out->tag = VAL_NONE; out->i = 0;
        }
    }
}

void runtime_struct_set_field(Value* st, const char* name, Value* v) {
    DllLayout* L = struct_layout(st);
    if (!L) return;
    int idx = struct_field_index(L, name);
    if (idx < 0) {
        runtime_raise_error("AttributeError: struct 没有该字段");
        return;
    }
    EclStruct* s = (EclStruct*)st->ptr;
    unsigned char* p = s->data + L->foffset[idx];
    const char* t = L->ftype[idx];
    if (my_strcmp(t, "bool") == 0) {
        *(int32_t*)p = dll_to_int(v) != 0;
    } else if (my_strcmp(t, "i32") == 0) {
        *(int32_t*)p = (int32_t)dll_to_int(v);
    } else if (my_strcmp(t, "i64") == 0 || my_strcmp(t, "int") == 0) {
        *(int64_t*)p = dll_to_int(v);
    } else if (my_strcmp(t, "float") == 0) {
        *(float*)p = (float)dll_to_double(v);
    } else if (my_strcmp(t, "double") == 0) {
        *(double*)p = dll_to_double(v);
    } else if (my_strcmp(t, "ptr") == 0) {
        *(void**)p = (void*)(intptr_t)dll_to_int(v);
    } else if (my_strcmp(t, "str") == 0 || my_strcmp(t, "cstr") == 0) {
        char* old = *(char**)p;
        no_crt_free(old);
        *(char**)p = (v && v->tag == VAL_STR) ? my_strdup(v->s) : my_strdup("");
    } else if (my_strcmp(t, "wstr") == 0) {
        wchar_t* old = *(wchar_t**)p;
        no_crt_free(old);
        wchar_t* dst = 0;
        if (v && v->tag == VAL_WSTR) {
            wchar_t* src = (wchar_t*)v->ptr;
            int64_t n = 0; while (src && src[n]) n++;
            dst = (wchar_t*)no_crt_alloc((size_t)(n + 1) * 2);
            for (int64_t i = 0; i <= n; i++) dst[i] = src[i];
        } else if (v && v->tag == VAL_STR) {
            dst = dll_utf8_to_utf16(v->s);
        } else {
            dst = dll_utf8_to_utf16("");
        }
        *(wchar_t**)p = dst;
    }
}

// DLL 导出边界：C wchar_t* -> ECL UTF-8；ECL UTF-8 -> C wchar_t*
char* runtime_export_from_wide(const wchar_t* w) {
    return dll_utf16_to_utf8(w);
}
wchar_t* runtime_export_to_wide(Value* v) {
    if (!v || v->tag != VAL_STR) return dll_utf8_to_utf16("");
    return dll_utf8_to_utf16(v->s);
}

static int64_t dll_to_int(Value* v) {
    if (!v) return 0;
    switch (v->tag) {
        case VAL_INT: case VAL_I64: case VAL_I32: case VAL_BOOL:
            return v->i;
        case VAL_FLOAT: case VAL_DOUBLE:
            return (int64_t)v->f;
        case VAL_STR: {
            int64_t r = 0; int neg = 0; const char* s = v->s;
            if (*s == '-') { neg = 1; s++; }
            while (*s >= '0' && *s <= '9') { r = r * 10 + (*s - '0'); s++; }
            return neg ? -r : r;
        }
        default: return 0;
    }
}
static double dll_to_double(Value* v) {
    if (!v) return 0.0;
    switch (v->tag) {
        case VAL_FLOAT: case VAL_DOUBLE: return v->f;
        case VAL_INT: case VAL_I64: case VAL_I32: case VAL_BOOL:
            return (double)v->i;
        default: return 0.0;
    }
}

static char* dll_fill_struct(DllLayout* L, Value* v) {
    char* buf = (char*)no_crt_alloc((size_t)(L->size > 0 ? L->size : 1));
    if (!buf) return 0;
    memset(buf, 0, (size_t)L->size);
    if (!v || v->tag != VAL_DICT) return buf;
    EclDict* d = (EclDict*)v->ptr;
    for (int i = 0; i < L->nfields; i++) {
        Value key;
        key.tag = VAL_STR;
        key.s = L->fname[i];
        EclDictEntry* e = dict_find(d, key);
        if (!e) continue;
        Value* fv = &e->val;
        char* dst = buf + L->foffset[i];
        const char* t = L->ftype[i];
        DllLayout* sub = dll_find_layout(t);
        if (sub) {
            char* nested = dll_fill_struct(sub, fv);
            if (nested) memcpy(dst, nested, (uint64_t)sub->size);
        } else if (my_strcmp(t, "bool") == 0 || my_strcmp(t, "i32") == 0) {
            *(int32_t*)dst = (int32_t)dll_to_int(fv);
        } else if (my_strcmp(t, "i64") == 0 || my_strcmp(t, "int") == 0) {
            *(int64_t*)dst = dll_to_int(fv);
        } else if (my_strcmp(t, "float") == 0) {
            *(float*)dst = (float)dll_to_double(fv);
        } else if (my_strcmp(t, "double") == 0) {
            *(double*)dst = dll_to_double(fv);
        } else if (my_strcmp(t, "str") == 0) {
            if (fv->tag == VAL_STR) *(wchar_t**)dst = dll_utf8_to_utf16(fv->s);
        } else if (my_strcmp(t, "cstr") == 0) {
            if (fv->tag == VAL_STR) *(char**)dst = fv->s;
        } else if (my_strcmp(t, "ptr") == 0) {
            *(void**)dst = (void*)(intptr_t)dll_to_int(fv);
        }
    }
    return buf;
}

static void dll_dict_set_str(Value* dict, const char* key, Value* val) {
    Value k;
    k.tag = VAL_STR;
    k.s = (char*)key;
    runtime_dict_set(dict, &k, val);
}

static void dll_read_struct(DllLayout* L, char* buf, Value* dict) {
    for (int i = 0; i < L->nfields; i++) {
        char* src = buf + L->foffset[i];
        const char* t = L->ftype[i];
        Value out;
        DllLayout* sub = dll_find_layout(t);
        if (sub) {
            out.tag = VAL_DICT;
            out.ptr = dict_new();
            dll_read_struct(sub, src, &out);
        } else if (my_strcmp(t, "bool") == 0) {
            out.tag = VAL_BOOL;
            out.i = (*(int32_t*)src) != 0;
        } else if (my_strcmp(t, "i32") == 0) {
            out.tag = VAL_I32;
            out.i = (int32_t)(*(int32_t*)src);
        } else if (my_strcmp(t, "i64") == 0 || my_strcmp(t, "int") == 0) {
            out.tag = (my_strcmp(t, "i64") == 0) ? VAL_I64 : VAL_INT;
            out.i = *(int64_t*)src;
        } else if (my_strcmp(t, "float") == 0) {
            out.tag = VAL_FLOAT;
            out.f = (double)(*(float*)src);
        } else if (my_strcmp(t, "double") == 0) {
            out.tag = VAL_DOUBLE;
            out.f = *(double*)src;
        } else if (my_strcmp(t, "str") == 0) {
            out.tag = VAL_STR;
            out.s = dll_utf16_to_utf8(*(wchar_t**)src);
        } else if (my_strcmp(t, "cstr") == 0) {
            char* s = *(char**)src;
            out.tag = VAL_STR;
            out.s = my_strdup(s ? s : "");
        } else if (my_strcmp(t, "ptr") == 0) {
            out.tag = VAL_INT;
            out.i = (int64_t)(intptr_t)(*(void**)src);
        } else {
            out.tag = VAL_NONE;
            out.i = 0;
        }
        dll_dict_set_str(dict, L->fname[i], &out);
    }
}

static HANDLE dll_load_lib(const char* path) {
    for (int i = 0; i < dll_lib_count; i++) {
        if (my_strcmp(dll_libs[i].path, path) == 0) return dll_libs[i].handle;
    }
    HANDLE h = LoadLibraryW(dll_utf8_to_utf16(path));
    if (!h) {
        char buf[512];
        char* msg = "DllNotFoundError: 无法加载 DLL '";
        char* p = buf;
        while (*msg) *p++ = *msg++;
        while (*path) *p++ = *path++;
        *p++ = '\'';
        *p = 0;
        runtime_raise_error(buf);
        return 0;
    }
    if (dll_lib_count < DLL_MAX_LIBS) {
        DllLib* lib = &dll_libs[dll_lib_count++];
        int n = 0;
        while (path[n] && n < 259) { lib->path[n] = path[n]; n++; }
        lib->path[n] = 0;
        lib->handle = h;
    }
    return h;
}

void runtime_dll_call(Value* dest, const char* spec, Value** args,
                      int64_t nargs) {
    char path[260], fname[128], ret[32];
    const char* p = spec;
    dll_copy_name(path, 260, &p, '|', '|');
    if (*p == '|') p++;
    dll_copy_name(fname, 128, &p, '|', '|');
    if (*p == '|') p++;
    dll_copy_name(ret, 32, &p, '|', '|');
    if (*p == '|') p++;
    char ptypes[16][32];
    int pmods[16];
    int np = 0;
    while (*p && np < 16) {
        dll_copy_name(ptypes[np], 32, &p, ':', ';');
        pmods[np] = 0;
        if (*p == ':') {
            p++;
            char mod[16];
            dll_copy_name(mod, 16, &p, ';', '|');
            if (my_strcmp(mod, "ptr") == 0) pmods[np] = 1;
            else if (my_strcmp(mod, "in") == 0) pmods[np] = 2;
            else if (my_strcmp(mod, "out") == 0) pmods[np] = 3;
        }
        np++;
        if (*p == ';') p++;
    }
    if (nargs != np) runtime_raise_error("TypeError: DLL 参数数量不匹配");
    HANDLE h = dll_load_lib(path);
    void* fn = GetProcAddress(h, fname);
    if (!fn) {
        char buf[256];
        char* msg = "AttributeError: 找不到 DLL 函数 '";
        char* q = buf;
        while (*msg) *q++ = *msg++;
        p = fname;
        while (*p) *q++ = *p++;
        *q++ = '\'';
        *q = 0;
        runtime_raise_error(buf);
        return;
    }

    uint64_t slots[32];
    uint64_t types = 0;
    char* struct_bufs[32];
    Value* struct_dicts[32];
    int struct_mods[32];
    DllLayout* struct_layouts[32];
    int struct_count = 0;
    uint64_t allocs[64];
    int alloc_count = 0;

    for (int i = 0; i < np; i++) {
        const char* t = ptypes[i];
        int mod = pmods[i];
        Value* v = args[i];
        DllLayout* L = dll_find_layout(t);
        if (L) {
            char* buf;
            if (mod == 3) {
                buf = (char*)no_crt_alloc((size_t)(L->size > 0 ? L->size : 1));
                memset(buf, 0, (size_t)L->size);
            } else {
                buf = dll_fill_struct(L, v);
            }
            allocs[alloc_count++] = (uint64_t)(uintptr_t)buf;
            if (mod == 0 && (L->size == 1 || L->size == 2 ||
                             L->size == 4 || L->size == 8)) {
                /* 小结构体按值放在整数寄存器里 */
                if (L->size == 1) slots[i] = *(uint8_t*)buf;
                else if (L->size == 2) slots[i] = *(uint16_t*)buf;
                else if (L->size == 4) slots[i] = *(uint32_t*)buf;
                else slots[i] = *(uint64_t*)buf;
            } else {
                slots[i] = (uint64_t)(uintptr_t)buf;
            }
            if (mod == 1 || mod == 3) {
                struct_bufs[struct_count] = buf;
                struct_dicts[struct_count] = v;
                struct_mods[struct_count] = mod;
                struct_layouts[struct_count] = L;
                struct_count++;
            }
            types |= 0ULL << (i * 2);
        } else if (my_strcmp(t, "float") == 0) {
            float f = (float)dll_to_double(v);
            uint32_t bits;
            memcpy(&bits, &f, 4);
            slots[i] = bits;
            if (i < 4) types |= 1ULL << (i * 2);
        } else if (my_strcmp(t, "double") == 0) {
            double d = dll_to_double(v);
            memcpy(&slots[i], &d, 8);
            if (i < 4) types |= 2ULL << (i * 2);
        } else if (my_strcmp(t, "str") == 0) {
            wchar_t* w = (v && v->tag == VAL_STR)
                             ? dll_utf8_to_utf16(v->s) : dll_utf8_to_utf16("");
            allocs[alloc_count++] = (uint64_t)(uintptr_t)w;
            slots[i] = (uint64_t)(uintptr_t)w;
        } else if (my_strcmp(t, "wstr") == 0) {
            if (v && v->tag == VAL_WSTR) {
                slots[i] = (uint64_t)(uintptr_t)v->ptr;
            } else {
                wchar_t* w = dll_utf8_to_utf16(
                    (v && v->tag == VAL_STR) ? v->s : "");
                allocs[alloc_count++] = (uint64_t)(uintptr_t)w;
                slots[i] = (uint64_t)(uintptr_t)w;
            }
        } else if (my_strcmp(t, "cstr") == 0) {
            if (v && v->tag == VAL_INT) {
                slots[i] = (uint64_t)v->i;
            } else {
                const char* s = (v && v->tag == VAL_STR) ? v->s : "";
                slots[i] = (uint64_t)(uintptr_t)s;
            }
        } else if (my_strcmp(t, "ptr") == 0) {
            slots[i] = (uint64_t)dll_to_int(v);
        } else {
            slots[i] = (uint64_t)dll_to_int(v);
        }
    }

    uint64_t raw = ecl_invoke(fn, slots, types, np);

    for (int i = 0; i < struct_count; i++) {
        if (struct_dicts[i] && struct_dicts[i]->tag == VAL_DICT) {
            dll_read_struct(struct_layouts[i], struct_bufs[i],
                            struct_dicts[i]);
        }
    }
    (void)allocs;
    (void)alloc_count;

    if (my_strcmp(ret, "none") == 0) {
        dest->tag = VAL_NONE;
        dest->i = 0;
    } else if (my_strcmp(ret, "bool") == 0) {
        dest->tag = VAL_BOOL;
        dest->i = (int32_t)raw != 0;
    } else if (my_strcmp(ret, "i32") == 0) {
        dest->tag = VAL_I32;
        dest->i = (int32_t)raw;
    } else if (my_strcmp(ret, "i64") == 0) {
        dest->tag = VAL_I64;
        dest->i = (int64_t)raw;
    } else if (my_strcmp(ret, "int") == 0) {
        dest->tag = VAL_INT;
        dest->i = (int64_t)raw;
    } else if (my_strcmp(ret, "float") == 0) {
        float f;
        uint32_t bits = (uint32_t)ecl_xmm0_result;
        memcpy(&f, &bits, 4);
        dest->tag = VAL_FLOAT;
        dest->f = (double)f;
    } else if (my_strcmp(ret, "double") == 0) {
        double d;
        memcpy(&d, &ecl_xmm0_result, 8);
        dest->tag = VAL_DOUBLE;
        dest->f = d;
    } else if (my_strcmp(ret, "str") == 0) {
        dest->tag = VAL_STR;
        dest->s = dll_utf16_to_utf8((const wchar_t*)(uintptr_t)raw);
    } else if (my_strcmp(ret, "cstr") == 0) {
        const char* s = (const char*)(uintptr_t)raw;
        dest->tag = VAL_STR;
        dest->s = my_strdup(s ? s : "");
    } else if (my_strcmp(ret, "ptr") == 0) {
        dest->tag = VAL_INT;
        dest->i = (int64_t)raw;
    } else {
        dest->tag = VAL_NONE;
        dest->i = 0;
    }
}

// ============================================================
// 入口
// ============================================================
__declspec(noreturn) void startup(void);
extern int main(void);
__declspec(noreturn) void startup(void) {
    int r = main();
    runtime_shutdown();
    ExitProcess((DWORD)r);
}