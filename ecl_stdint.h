#ifndef ECL_STDINT_H
#define ECL_STDINT_H

/* runtime.c 只需要固定宽度整数，避免 clang-cl 依赖 MSVC 头文件。 */
typedef signed char int8_t;
typedef unsigned char uint8_t;
typedef short int16_t;
typedef unsigned short uint16_t;
typedef int int32_t;
typedef unsigned int uint32_t;
typedef __int64 int64_t;
typedef unsigned __int64 uint64_t;
typedef __int64 intptr_t;
typedef unsigned __int64 uintptr_t;
typedef unsigned __int64 size_t;

#ifndef NULL
#define NULL 0
#endif

#endif
