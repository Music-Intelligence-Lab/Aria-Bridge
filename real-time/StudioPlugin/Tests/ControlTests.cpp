#include "PluginEditor.h"

#include <iostream>
#include <cstring>
#include <stdexcept>
#include <vector>
#include <windows.h>

namespace
{
using Control = AriaStudioAudioProcessor::ControlId;

void require(bool condition, const char* message)
{
    if (! condition)
        throw std::runtime_error(message);
}

void pumpMessages()
{
    // JUCE disables nested modal loops in plugins. Pump only this test thread's
    // Windows messages so AsyncUpdater and Button::triggerClick can complete.
    const auto until = juce::Time::getMillisecondCounterHiRes() + 180.0;
    while (juce::Time::getMillisecondCounterHiRes() < until)
    {
        MSG message {};
        while (PeekMessage(&message, nullptr, 0, 0, PM_REMOVE))
        {
            TranslateMessage(&message);
            DispatchMessage(&message);
        }
        juce::Thread::sleep(1);
    }
}

void backendMessage(const char* address, const char* type, float value = 0.0f)
{
    juce::MemoryOutputStream packet;
    for (const auto* text : { address, type })
    {
        packet.writeString(text);
        while (packet.getDataSize() % 4 != 0)
            packet.writeByte(0);
    }
    if (juce::String(type) == ",i")
        packet.writeIntBigEndian(juce::roundToInt(value));
    else if (juce::String(type) == ",f")
    {
        int bits = 0;
        std::memcpy(&bits, &value, sizeof(bits));
        packet.writeIntBigEndian(bits);
    }
    juce::DatagramSocket sender(false);
    sender.write("127.0.0.1", 9001, packet.getData(), static_cast<int>(packet.getDataSize()));
    pumpMessages();
}

int progressPixels(AriaStudioAudioProcessorEditor& editor)
{
    const auto image = editor.createComponentSnapshot(editor.getLocalBounds());
    const auto bar = editor.getProgressDisplayBounds();
    int result = 0;
    for (int x = bar.getX(); x < bar.getRight(); ++x)
        if (image.getPixelAt(x, bar.getCentreY()) == juce::Colour::fromRGB(78, 201, 231))
            ++result;
    return result;
}

void feedCC(AriaStudioAudioProcessor& processor, int cc, int value)
{
    juce::AudioBuffer<float> audio(0, 64);
    juce::MidiBuffer midi;
    midi.addEvent(juce::MidiMessage::controllerEvent(1, cc, value), 0);
    processor.processBlock(audio, midi);
    pumpMessages();
}

std::vector<juce::String> drainAddresses(juce::DatagramSocket& socket)
{
    std::vector<juce::String> result;
    char data[2048] {};
    while (socket.waitUntilReady(true, 20) > 0)
    {
        const int size = socket.read(data, sizeof(data), false);
        if (size > 0)
            result.push_back(juce::String::fromUTF8(data));
    }
    return result;
}

bool contains(const std::vector<juce::String>& messages, const char* address)
{
    return std::find(messages.begin(), messages.end(), juce::String(address)) != messages.end();
}

juce::Slider& sliderNamed(AriaStudioAudioProcessorEditor& editor, const char* name)
{
    for (auto* child : editor.getChildren())
        if (auto* slider = dynamic_cast<juce::Slider*>(child); slider != nullptr && slider->getName() == name)
            return *slider;
    throw std::runtime_error("Expected knob missing from editor");
}

juce::Button& buttonNamed(AriaStudioAudioProcessorEditor& editor, const char* name)
{
    for (auto* child : editor.getChildren())
        if (auto* button = dynamic_cast<juce::Button*>(child); button != nullptr && button->getName() == name)
            return *button;
    throw std::runtime_error("Expected button missing from editor");
}

void snapshot(AriaStudioAudioProcessorEditor& editor, const juce::File& folder, const char* name, int width, int height)
{
    editor.setSize(width, height);
    for (auto* child : editor.getChildren())
        if (child->isVisible())
            require(editor.getLocalBounds().contains(child->getBounds()), "Control falls outside the resized editor");
    auto image = editor.createComponentSnapshot(editor.getLocalBounds());
    auto stream = folder.getChildFile(name).createOutputStream();
    require(stream != nullptr, "Cannot write UI preview");
    require(stream->setPosition(0) && stream->truncate().wasOk(), "Cannot replace the previous UI preview");
    juce::PNGImageFormat format;
    require(format.writeImageToStream(image, *stream), "Cannot encode UI preview");
}
}

int main(int argc, char** argv)
{
    juce::ScopedJuceInitialiser_GUI initialiser;
    try
    {
        juce::DatagramSocket receiver(false);
        require(receiver.bindToPort(9000), "Control tests need port 9000 free (backend must be stopped)");
        AriaStudioAudioProcessor processor;
        std::unique_ptr<AriaStudioAudioProcessorEditor> editor(
            static_cast<AriaStudioAudioProcessorEditor*>(processor.createEditor()));
        pumpMessages();
        require(! processor.getOSCStateSnapshot().lastLog.contains("Failed to bind"),
                "Control tests need port 9001 free (close other bridge instances)");
        drainAddresses(receiver);

        // The Max device's new destination and length controls must reach the backend.
        for (const auto& entry : std::array<std::pair<Control, const char*>, 3> {{
                 { Control::track, "/aria/track" }, { Control::slot, "/aria/slot" },
                 { Control::measures, "/aria/measures" } }})
        {
            processor.beginMidiLearn(entry.first);
            feedCC(processor, 20 + static_cast<int>(entry.first), 127);
            require(contains(drainAddresses(receiver), entry.second), "New knob's MIDI mapping did not send OSC");
        }
        require(processor.getControlValue(Control::track) == 10.0, "Track MIDI range differs from Ableton");
        require(processor.getControlValue(Control::slot) == 10.0, "Slot MIDI range differs from Ableton");
        require(processor.getControlValue(Control::measures) == 60.0, "Measures MIDI range differs from Ableton");

        // CC press/release must toggle once, including immediately after MIDI learn.
        processor.beginMidiLearn(Control::clip);
        feedCC(processor, 40, 127);
        require(buttonNamed(*editor, "clip").getToggleState(), "Clip MIDI press did not enable the toggle");
        require(contains(drainAddresses(receiver), "/aria/clip"), "Clip toggle did not send OSC");
        feedCC(processor, 40, 0);
        require(buttonNamed(*editor, "clip").getToggleState(), "CC release toggled clip a second time");
        require(! contains(drainAddresses(receiver), "/aria/clip"), "CC release sent a second clip command");
        feedCC(processor, 40, 127);
        require(! buttonNamed(*editor, "clip").getToggleState(), "Second MIDI press did not disable clip");
        drainAddresses(receiver);

        buttonNamed(*editor, "loop").triggerClick();
        pumpMessages();
        const auto loopMessages = drainAddresses(receiver);
        require(buttonNamed(*editor, "clip").getToggleState(), "Loop must enable its required clip output");
        const auto clipPosition = std::find(loopMessages.begin(), loopMessages.end(), juce::String("/aria/clip"));
        const auto loopPosition = std::find(loopMessages.begin(), loopMessages.end(), juce::String("/aria/loop"));
        require(clipPosition != loopMessages.end() && loopPosition != loopMessages.end() && clipPosition < loopPosition,
                "Clip must be enabled before the backend receives loop-on");
        buttonNamed(*editor, "clip").triggerClick();
        pumpMessages();
        require(! buttonNamed(*editor, "loop").getToggleState(), "Disabling clip must also disable loop");
        drainAddresses(receiver);
        processor.beginMidiLearn(Control::autoFire);
        feedCC(processor, 41, 1);
        require(buttonNamed(*editor, "auto").getToggleState(), "Low-valued CC press did not enable Auto");
        require(contains(drainAddresses(receiver), "/aria/fire"), "Auto must use Ableton's fire-on-write address");
        feedCC(processor, 41, 0);
        require(buttonNamed(*editor, "auto").getToggleState(), "Low-valued CC release toggled Auto again");

        // Playback and generation share the M4L progress address. Playback wins
        // over a background generation start, and clip output disables the slider.
        backendMessage("/playback_duration", ",f", 10.0f);
        backendMessage("/generation_progress", ",i", 50.0f);
        const auto halfProgress = progressPixels(*editor);
        require(halfProgress > 50, "Shared playback progress did not move the slider");
        backendMessage("/generation_start", ",");
        require(progressPixels(*editor) >= halfProgress, "Background generation reset the playback slider");
        backendMessage("/generation_progress", ",f", 75.0f);
        require(progressPixels(*editor) > halfProgress, "Float progress updates were ignored");
        buttonNamed(*editor, "clip").triggerClick();
        pumpMessages();
        require(progressPixels(*editor) < 10, "Clip output must disable the progress slider");
        for (auto* child : editor->getChildren())
            if (auto* label = dynamic_cast<juce::Label*>(child); label != nullptr && label->getText().contains("left"))
                require(! label->isVisible(), "Clip mode must hide the stale playback countdown");
        buttonNamed(*editor, "clip").triggerClick();
        pumpMessages();
        backendMessage("/generation_done", ",");
        backendMessage("/playback_stopped", ",");
        backendMessage("/generation_progress", ",i", 0.0f);
        pumpMessages();
        require(progressPixels(*editor) < 10, "Stopped playback did not clear the shared slider");
        drainAddresses(receiver);

        // Knobs and toggles survive closing the editor and reopening the project.
        sliderNamed(*editor, "track").setValue(4, juce::sendNotificationSync);
        sliderNamed(*editor, "slot").setValue(4, juce::sendNotificationSync);
        sliderNamed(*editor, "tokens").setValue(1290, juce::sendNotificationSync);
        sliderNamed(*editor, "measures").setValue(0, juce::sendNotificationSync);
        require(contains(drainAddresses(receiver), "/aria/track"), "Direct knob changes did not send OSC");
        buttonNamed(*editor, "sync").triggerClick();
        pumpMessages();
        const auto syncMessages = drainAddresses(receiver);
        for (const char* address : { "/aria/temp", "/aria/top_p", "/aria/min_p", "/aria/tokens", "/aria/grade",
                                    "/aria/track", "/aria/slot", "/aria/measures", "/aria/clip", "/aria/loop",
                                    "/aria/fire", "/aria/ping" })
            require(contains(syncMessages, address), "Sync omitted a control from the Ableton device");
        juce::MemoryBlock saved;
        processor.getStateInformation(saved);
        editor.reset();
        processor.setControlValue(Control::track, 0.0);
        processor.setControlValue(Control::autoFire, 0.0);
        processor.setStateInformation(saved.getData(), static_cast<int>(saved.getSize()));
        pumpMessages();
        editor.reset(static_cast<AriaStudioAudioProcessorEditor*>(processor.createEditor()));
        require(sliderNamed(*editor, "track").getValue() == 4.0, "Track value was lost when reopening");
        require(buttonNamed(*editor, "auto").getToggleState(), "Auto value was lost when reopening");

        // Existing sessions used numeric IDs 0..9; inserted knobs must not steal button mappings.
        juce::XmlElement legacy("AriaStudioState");
        auto* mappings = legacy.createNewChildElement("MidiMappings");
        for (int index = 0; index < 10; ++index)
        {
            auto* mapping = mappings->createNewChildElement("Mapping");
            mapping->setAttribute("control", index);
            mapping->setAttribute("cc", 50 + index);
        }
        juce::MemoryBlock legacyState;
        juce::AudioProcessor::copyXmlToBinary(legacy, legacyState);
        processor.setStateInformation(legacyState.getData(), static_cast<int>(legacyState.getSize()));
        pumpMessages();
        require(processor.getMappedMidiCC(Control::grade) == 54, "Legacy grade mapping changed");
        require(processor.getMappedButtonMidi(Control::record) == 55, "Legacy record mapping changed");
        require(processor.getMappedButtonMidi(Control::sync) == 56, "Legacy sync mapping changed");
        require(processor.getMappedButtonMidi(Control::commit) == 57, "Legacy commit mapping changed");
        require(processor.getMappedButtonMidi(Control::play) == 58, "Legacy play mapping changed");
        require(processor.getMappedButtonMidi(Control::cancel) == 59, "Legacy cancel mapping changed");
        require(processor.getMappedMidiCC(Control::track) == -1, "Legacy mapping was assigned to the new track knob");

        // Preview the actual editor at minimum, reference, and enlarged sizes.
        if (argc > 1)
        {
            const juce::File folder { juce::String(argv[1]) };
            require(folder.createDirectory().wasOk(), "Cannot create preview directory");
            processor.setStateInformation(saved.getData(), static_cast<int>(saved.getSize()));
            pumpMessages();
            buttonNamed(*editor, "auto").setToggleState(false, juce::dontSendNotification);
            snapshot(*editor, folder, "aria-studio.png", 960, 500);
            snapshot(*editor, folder, "aria-studio-minimum.png", 768, 400);
            snapshot(*editor, folder, "aria-studio-large.png", 1920, 1000);
            snapshot(*editor, folder, "aria-studio-tall.png", 960, 750);
            editor->setSize(960, 500);
            backendMessage("/generation_start", ",");
            backendMessage("/generation_progress", ",i", 65.0f);
            pumpMessages();
            pumpMessages();
            snapshot(*editor, folder, "aria-studio-active.png", 960, 500);
            buttonNamed(*editor, "loop").triggerClick();
            buttonNamed(*editor, "auto").triggerClick();
            pumpMessages();
            snapshot(*editor, folder, "aria-studio-clip.png", 960, 500);
        }
        std::cout << "PASS: Ableton controls, OSC routing, MIDI press/release, shared progress, state restoration, legacy mappings, resizing\n";
        editor.reset();
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
