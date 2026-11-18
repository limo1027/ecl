#include <stdint.h>
#include <wchar.h>

typedef struct { int32_t x; int32_t y; } POINT;
typedef struct { POINT a; POINT b; } PAIR;

__declspec(dllexport) int32_t add32(int32_t a, int32_t b) { return a + b; }
__declspec(dllexport) int64_t add64(int64_t a, int64_t b) { return a + b; }
__declspec(dllexport) int32_t sum5(int32_t a, int32_t b, int32_t c, int32_t d, int32_t e) {
    return a + b + c + d + e;
}
__declspec(dllexport) float addf(float a, float b) { return a + b; }
__declspec(dllexport) double addd(double a, double b) { return a + b; }
__declspec(dllexport) int32_t wlen(const wchar_t* s) {
    int32_t n = 0;
    while (s && s[n]) n++;
    return n;
}
__declspec(dllexport) int32_t clen(const char* s) {
    int32_t n = 0;
    while (s && s[n]) n++;
    return n;
}
__declspec(dllexport) const wchar_t* greet(void) { return L"hello"; }
__declspec(dllexport) const char* cgreet(void) { return "hi"; }
__declspec(dllexport) int32_t point_sum(POINT* p) { return p ? p->x + p->y : -1; }
__declspec(dllexport) int32_t point_sum_val(POINT p) { return p.x + p.y; }
__declspec(dllexport) int32_t pair_sum(PAIR* p) {
    return p ? p->a.x + p->a.y + p->b.x + p->b.y : -1;
}
__declspec(dllexport) void set_point(POINT* p, int32_t x, int32_t y) {
    if (p) { p->x = x; p->y = y; }
}
__declspec(dllexport) void get_point(POINT* p) {
    if (p) { p->x = 30; p->y = 12; }
}
