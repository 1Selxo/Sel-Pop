cmake_minimum_required(VERSION 3.22.1)

project(sel_pop_hoshidicts_server LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED HOSHIDICTS_SOURCE_DIR)
    set(HOSHIDICTS_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../external/hoshidicts")
endif()
file(TO_CMAKE_PATH "${HOSHIDICTS_SOURCE_DIR}" HOSHIDICTS_SOURCE_DIR)

set(ZSTD_BUILD_PROGRAMS OFF CACHE BOOL "")
set(ZSTD_BUILD_TESTS OFF CACHE BOOL "")
set(ZSTD_BUILD_SHARED OFF CACHE BOOL "")
set(ZSTD_BUILD_STATIC ON CACHE BOOL "")
set(LIBDEFLATE_BUILD_SHARED_LIB OFF CACHE BOOL "")
set(LIBDEFLATE_BUILD_GZIP OFF CACHE BOOL "")
set(UTF8PROC_INSTALL OFF CACHE BOOL "")

add_subdirectory("${HOSHIDICTS_SOURCE_DIR}/external/glaze" external/glaze)
add_subdirectory("${HOSHIDICTS_SOURCE_DIR}/external/zstd" external/zstd)
add_subdirectory("${HOSHIDICTS_SOURCE_DIR}/external/unordered_dense" external/unordered_dense)
add_subdirectory("${HOSHIDICTS_SOURCE_DIR}/external/libdeflate" external/libdeflate)
add_subdirectory("${HOSHIDICTS_SOURCE_DIR}/external/utf8proc" external/utf8proc)

add_library(hoshidicts-query STATIC
    "${HOSHIDICTS_SOURCE_DIR}/src/hash/hash.cpp"
    "${HOSHIDICTS_SOURCE_DIR}/src/hash/bloom.cpp"
    "${HOSHIDICTS_SOURCE_DIR}/src/importer.cpp"
    "${HOSHIDICTS_SOURCE_DIR}/src/memory/memory.cpp"
    "${HOSHIDICTS_SOURCE_DIR}/src/zip/zip.cpp"
    "${HOSHIDICTS_SOURCE_DIR}/src/json/yomitan_parser.cpp"
    "${HOSHIDICTS_SOURCE_DIR}/src/query.cpp"
)
target_include_directories(hoshidicts-query PUBLIC
    "${HOSHIDICTS_SOURCE_DIR}/include"
    "${HOSHIDICTS_SOURCE_DIR}/external/utfcpp/source"
    "${HOSHIDICTS_SOURCE_DIR}/external/xxHash"
)
target_link_libraries(hoshidicts-query PRIVATE
    glaze::glaze
    libzstd_static
    libdeflate_static
    unordered_dense
    utf8proc::utf8proc
)
if(MSVC)
    target_compile_options(hoshidicts-query PRIVATE /utf-8)
endif()

add_executable(sel-pop-hoshidicts-server hoshidicts_server.cpp)
target_link_libraries(sel-pop-hoshidicts-server PRIVATE hoshidicts-query glaze::glaze)
if(MSVC)
    target_compile_options(sel-pop-hoshidicts-server PRIVATE /utf-8)
endif()
