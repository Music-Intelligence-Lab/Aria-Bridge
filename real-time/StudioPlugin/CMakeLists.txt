cmake_minimum_required(VERSION 3.22)

project(AriaStudio VERSION 0.2.0 LANGUAGES CXX)

include(FetchContent)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

FetchContent_Declare(
    juce
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 8.0.7
)

FetchContent_MakeAvailable(juce)

juce_add_plugin(AriaStudio
    COMPANY_NAME "Aria"
    BUNDLE_ID "com.aria.studio"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT TRUE
    NEEDS_MIDI_OUTPUT TRUE
    IS_MIDI_EFFECT TRUE
    EDITOR_WANTS_KEYBOARD_FOCUS FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE
    PLUGIN_MANUFACTURER_CODE Aria
    PLUGIN_CODE ArSt
    VST3_CATEGORIES Fx Tools
    FORMATS VST3 Standalone
    PRODUCT_NAME "Aria Studio"
)

juce_generate_juce_header(AriaStudio)

target_sources(AriaStudio
    PRIVATE
        Source/PluginEditor.cpp
        Source/PluginProcessor.cpp
)

target_include_directories(AriaStudio
    PRIVATE
        Source
)

target_compile_definitions(AriaStudio
    PRIVATE
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0
)

target_compile_options(AriaStudio PRIVATE $<$<CXX_COMPILER_ID:MSVC>:/utf-8>)

if (TARGET AriaStudio_Standalone)
    target_compile_definitions(AriaStudio_Standalone
        PRIVATE
            JUCE_USE_CUSTOM_PLUGIN_STANDALONE_APP=0
            JUCE_STANDALONE_FILTER_WINDOW_USE_KIOSK_MODE=0
    )

    target_link_libraries(AriaStudio_Standalone
        PRIVATE
            juce::juce_audio_devices
            juce::juce_audio_processors
            juce::juce_audio_utils
            juce::juce_core
            juce::juce_events
            juce::juce_graphics
            juce::juce_gui_basics
    )

    set_target_properties(AriaStudio_Standalone
        PROPERTIES
            OUTPUT_NAME "Aria Studio"
    )
endif()

if (WIN32)
    set(vst3_install_dir "C:/Program Files/Common Files/VST3")
    install(
        DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/AriaStudio_artefacts/$<CONFIG>/VST3/Aria Studio.vst3"
        DESTINATION "${vst3_install_dir}"
        COMPONENT AriaStudioVST3
    )
endif()

target_link_libraries(AriaStudio
    PRIVATE
        juce::juce_audio_devices
        juce::juce_audio_processors
        juce::juce_audio_utils
        juce::juce_core
        juce::juce_events
        juce::juce_graphics
        juce::juce_gui_basics
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags
)

option(ARIA_BUILD_TESTS "Build the plugin control regression tests and UI previews" OFF)
if (ARIA_BUILD_TESTS AND WIN32)
    enable_testing()
    add_executable(AriaStudioControlTests Tests/ControlTests.cpp)
    target_include_directories(AriaStudioControlTests PRIVATE
        $<TARGET_PROPERTY:AriaStudio,INCLUDE_DIRECTORIES>
    )
    target_compile_definitions(AriaStudioControlTests PRIVATE
        $<TARGET_PROPERTY:AriaStudio,COMPILE_DEFINITIONS>
    )
    target_link_libraries(AriaStudioControlTests PRIVATE AriaStudio)
    add_test(NAME AriaStudioControls COMMAND AriaStudioControlTests)
endif()
