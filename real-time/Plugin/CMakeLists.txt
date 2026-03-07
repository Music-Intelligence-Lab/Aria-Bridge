cmake_minimum_required(VERSION 3.22)

project(AriaBridge VERSION 0.1.0 LANGUAGES CXX)

include(FetchContent)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

FetchContent_Declare(
    juce
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 7.0.9
)

FetchContent_MakeAvailable(juce)

juce_add_plugin(AriaBridge
    COMPANY_NAME "Aria"
    BUNDLE_ID "com.aria.bridge"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT TRUE
    NEEDS_MIDI_OUTPUT TRUE
    IS_MIDI_EFFECT TRUE
    EDITOR_WANTS_KEYBOARD_FOCUS FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE
    PLUGIN_MANUFACTURER_CODE Aria
    PLUGIN_CODE ArBr
    FORMATS VST3 Standalone
    PRODUCT_NAME "Aria Bridge"
)

juce_generate_juce_header(AriaBridge)

target_sources(AriaBridge
    PRIVATE
        Source/PluginEditor.cpp
        Source/PluginProcessor.cpp
)

target_include_directories(AriaBridge
    PRIVATE
        Source
)

target_compile_definitions(AriaBridge
    PRIVATE
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0
)

if (TARGET AriaBridge_Standalone)
    target_compile_definitions(AriaBridge_Standalone
        PRIVATE
            JUCE_USE_CUSTOM_PLUGIN_STANDALONE_APP=0
            JUCE_STANDALONE_FILTER_WINDOW_USE_KIOSK_MODE=0
    )

    target_link_libraries(AriaBridge_Standalone
        PRIVATE
            juce::juce_audio_devices
            juce::juce_audio_processors
            juce::juce_audio_utils
            juce::juce_core
            juce::juce_events
            juce::juce_graphics
            juce::juce_gui_basics
    )

    set_target_properties(AriaBridge_Standalone
        PROPERTIES
            OUTPUT_NAME "Aria Bridge"
    )
endif()

if (WIN32)
    set(vst3_install_dir "C:/Program Files/Common Files/VST3")
    install(
        DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/AriaBridge_artefacts/$<CONFIG>/VST3/Aria Bridge.vst3"
        DESTINATION "${vst3_install_dir}"
        COMPONENT AriaBridgeVST3
    )
endif()

target_link_libraries(AriaBridge
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
