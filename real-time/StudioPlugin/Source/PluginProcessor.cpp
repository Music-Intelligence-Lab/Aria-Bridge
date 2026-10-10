#include "PluginProcessor.h"
#include "PluginEditor.h"

#include <cmath>
#include <cstring>

namespace
{
constexpr int oscSendPort = 9000;
constexpr int oscReceivePort = 9001;
constexpr const char* oscHost = "127.0.0.1";
constexpr int maxOscPacketSize = 2048;
constexpr auto controlCount = static_cast<int>(AriaStudioAudioProcessor::ControlId::count);
constexpr auto knobCount = static_cast<int>(AriaStudioAudioProcessor::ControlId::record);

struct ControlSpec
{
    const char* address;
    double minimum;
    double maximum;
    bool integerOnly;
};

constexpr std::array<ControlSpec, knobCount> controlSpecs {{
    { "/aria/temp", 0.1, 2.0, false },
    { "/aria/top_p", 0.7, 1.0, false },
    { "/aria/min_p", 0.0, 0.1, false },
    { "/aria/tokens", 0.0, 2048.0, true },
    { "/aria/grade", 1.0, 5.0, true },
    { "/aria/track", 0.0, 10.0, true },
    { "/aria/slot", 0.0, 10.0, true },
    { "/aria/measures", 0.0, 60.0, true },
}};

constexpr std::array<double, controlCount> defaultControlValues {{
    1.0, 0.98, 0.01, 1000.0, 3.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
}};

int toIndex(AriaStudioAudioProcessor::ControlId controlId)
{
    return static_cast<int>(controlId);
}

float roundToTwoDecimals(float value)
{
    return std::round(value * 100.0f) / 100.0f;
}

int normaliseOscIntValue(const juce::String& address, int value)
{
    if (address == "/aria/tokens")
        return juce::jlimit(0, 2048, value);

    if (address == "/aria/grade")
        return juce::jlimit(1, 5, value);

    if (address == "/aria/track" || address == "/aria/slot")
        return juce::jlimit(0, 10, value);

    if (address == "/aria/measures")
        return juce::jlimit(0, 60, value);

    if (address == "/aria/record" || address == "/aria/clip"
        || address == "/aria/loop" || address == "/aria/fire")
        return juce::jlimit(0, 1, value);

    return value;
}

double snappedControlValue(AriaStudioAudioProcessor::ControlId controlId, double value)
{
    const auto& spec = controlSpecs[static_cast<size_t>(toIndex(controlId))];
    const auto clamped = juce::jlimit(spec.minimum, spec.maximum, value);

    if (spec.integerOnly)
        return static_cast<double>(juce::roundToInt(clamped));

    return clamped;
}

double midiValueToControlValue(AriaStudioAudioProcessor::ControlId controlId, int midiValue)
{
    const auto& spec = controlSpecs[static_cast<size_t>(toIndex(controlId))];
    const auto normalised = juce::jlimit(0, 127, midiValue) / 127.0;
    const auto mapped = spec.minimum + ((spec.maximum - spec.minimum) * normalised);
    return snappedControlValue(controlId, mapped);
}

juce::String controlAddress(AriaStudioAudioProcessor::ControlId controlId)
{
    return controlSpecs[static_cast<size_t>(toIndex(controlId))].address;
}

void writePaddedString(juce::MemoryOutputStream& stream, const juce::String& text)
{
    const auto utf8 = text.toRawUTF8();
    const auto length = std::strlen(utf8);

    stream.write(utf8, static_cast<size_t>(length));
    stream.writeByte(0);

    while ((stream.getDataSize() % 4u) != 0u)
        stream.writeByte(0);
}

void writeInt32(juce::MemoryOutputStream& stream, int value)
{
    const auto encoded = juce::ByteOrder::swapIfLittleEndian(static_cast<juce::uint32>(value));
    stream.write(&encoded, sizeof(encoded));
}

void writeFloat32(juce::MemoryOutputStream& stream, float value)
{
    juce::uint32 bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    bits = juce::ByteOrder::swapIfLittleEndian(bits);
    stream.write(&bits, sizeof(bits));
}

bool readPaddedString(const char* data, size_t size, size_t& offset, juce::String& text)
{
    if (offset >= size)
        return false;

    const auto start = offset;

    while (offset < size && data[offset] != '\0')
        ++offset;

    if (offset >= size)
        return false;

    text = juce::String::fromUTF8(data + start, static_cast<int>(offset - start));
    ++offset;

    while ((offset % 4u) != 0u)
    {
        if (offset >= size)
            return false;

        ++offset;
    }

    return true;
}

bool readFloat32(const char* data, size_t size, size_t& offset, float& value)
{
    if ((offset + sizeof(juce::uint32)) > size)
        return false;

    juce::uint32 encoded = 0;
    std::memcpy(&encoded, data + offset, sizeof(encoded));
    offset += sizeof(encoded);

    const auto bits = juce::ByteOrder::swapIfLittleEndian(encoded);
    std::memcpy(&value, &bits, sizeof(value));
    return true;
}

bool readInt32(const char* data, size_t size, size_t& offset, int& value)
{
    if ((offset + sizeof(juce::uint32)) > size)
        return false;

    juce::uint32 encoded = 0;
    std::memcpy(&encoded, data + offset, sizeof(encoded));
    offset += sizeof(encoded);

    value = static_cast<int>(juce::ByteOrder::swapIfLittleEndian(encoded));
    return true;
}

juce::MemoryBlock buildOSCMessage(const juce::String& address, const juce::String& typeTags)
{
    juce::MemoryBlock packet;
    juce::MemoryOutputStream stream(packet, false);
    writePaddedString(stream, address);
    writePaddedString(stream, typeTags);
    return packet;
}

juce::MemoryBlock buildOSCMessage(const juce::String& address, int value)
{
    auto packet = buildOSCMessage(address, ",i");
    juce::MemoryOutputStream stream(packet, true);
    stream.setPosition(packet.getSize());
    writeInt32(stream, value);
    return packet;
}

juce::MemoryBlock buildOSCMessage(const juce::String& address, float value)
{
    auto packet = buildOSCMessage(address, ",f");
    juce::MemoryOutputStream stream(packet, true);
    stream.setPosition(packet.getSize());
    writeFloat32(stream, value);
    return packet;
}
}

class AriaStudioAudioProcessor::OSCReceiverThread final : public juce::Thread
{
public:
    explicit OSCReceiverThread(AriaStudioAudioProcessor& ownerToUse)
        : juce::Thread("AriaStudio OSC Receiver"), owner(ownerToUse)
    {
    }

    void run() override
    {
        juce::DatagramSocket socket(false);

        if (! socket.bindToPort(oscReceivePort))
        {
            {
                const juce::ScopedLock lock(owner.oscStateLock);
                owner.lastLog = "Failed to bind UDP receive socket on port 9001";
            }

            owner.triggerAsyncUpdate();
            return;
        }

        char buffer[maxOscPacketSize] = {};

        while (! threadShouldExit())
        {
            const auto ready = socket.waitUntilReady(true, 100);

            if (ready <= 0)
                continue;

            const auto bytesRead = socket.read(buffer, maxOscPacketSize, false);

            if (bytesRead > 0)
                owner.handleIncomingOSCMessage(buffer, static_cast<size_t>(bytesRead));
        }
    }

private:
    AriaStudioAudioProcessor& owner;
};

AriaStudioAudioProcessor::AriaStudioAudioProcessor()
    : AudioProcessor(BusesProperties()),
      oscSenderSocket(false)
{
    midiMappings.fill(-1);
    pendingMidiValues.fill(0.0);
    pendingMidiValueDirty.fill(false);
    controlValues = defaultControlValues;
    oscReceiverThread = std::make_unique<OSCReceiverThread>(*this);
    oscReceiverThread->startThread();
    // Ping the backend every 2s so status/params refresh. The plugin is a pure OSC
    // client — the backend is started separately (from a terminal / future launcher).
    startTimer(2000);
    startStandaloneMidiInputs();
}

AriaStudioAudioProcessor::~AriaStudioAudioProcessor()
{
    stopTimer();
    cancelPendingUpdate();
    activeEditor = nullptr;
    stopStandaloneMidiInputs();

    if (oscReceiverThread != nullptr)
    {
        oscReceiverThread->stopThread(1500);
        oscReceiverThread.reset();
    }
}

void AriaStudioAudioProcessor::prepareToPlay(double, int)
{
}

void AriaStudioAudioProcessor::releaseResources()
{
}

bool AriaStudioAudioProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const
{
    juce::ignoreUnused(layouts);
    return true;
}

void AriaStudioAudioProcessor::processBlock(juce::AudioBuffer<float>& audioBuffer,
                                            juce::MidiBuffer& midiMessages)
{
    juce::ScopedNoDenormals noDenormals;

    // Control surface only — MIDI reaches the backend over the ARIA_IN virtual port,
    // not through this plugin. We just watch for MIDI-learn CC/note triggers.
    for (const auto metadata : midiMessages)
        handleMidiMessage(metadata.getMessage());

    audioBuffer.clear();
}

juce::AudioProcessorEditor* AriaStudioAudioProcessor::createEditor()
{
    return new AriaStudioAudioProcessorEditor(*this);
}

bool AriaStudioAudioProcessor::hasEditor() const
{
    return true;
}

const juce::String AriaStudioAudioProcessor::getName() const
{
    return JucePlugin_Name;
}

bool AriaStudioAudioProcessor::acceptsMidi() const
{
   #if JucePlugin_WantsMidiInput
    return true;
   #else
    return false;
   #endif
}

bool AriaStudioAudioProcessor::producesMidi() const
{
   #if JucePlugin_ProducesMidiOutput
    return true;
   #else
    return false;
   #endif
}

bool AriaStudioAudioProcessor::isMidiEffect() const
{
   #if JucePlugin_IsMidiEffect
    if (wrapperType == wrapperType_Standalone)
        return false;

    return true;
   #else
    return false;
   #endif
}

double AriaStudioAudioProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

int AriaStudioAudioProcessor::getNumPrograms()
{
    return 1;
}

int AriaStudioAudioProcessor::getCurrentProgram()
{
    return 0;
}

void AriaStudioAudioProcessor::setCurrentProgram(int)
{
}

const juce::String AriaStudioAudioProcessor::getProgramName(int)
{
    return {};
}

void AriaStudioAudioProcessor::changeProgramName(int, const juce::String&)
{
}

void AriaStudioAudioProcessor::getStateInformation(juce::MemoryBlock& destData)
{
    juce::XmlElement state("AriaStudioState");
    state.setAttribute("version", 2);
    auto* mappings = state.createNewChildElement("MidiMappings");
    auto* values = state.createNewChildElement("ControlValues");

    const juce::SpinLock::ScopedLockType lock(midiMappingLock);

    for (int index = 0; index < controlCount; ++index)
    {
        auto* mapping = mappings->createNewChildElement("Mapping");
        mapping->setAttribute("control", index);
        mapping->setAttribute("cc", midiMappings[static_cast<size_t>(index)]);
        auto* value = values->createNewChildElement("Value");
        value->setAttribute("control", index);
        value->setAttribute("value", controlValues[static_cast<size_t>(index)]);
    }

    copyXmlToBinary(state, destData);
}

void AriaStudioAudioProcessor::setStateInformation(const void* data, int sizeInBytes)
{
    std::array<int, controlCount> loadedMappings {};
    loadedMappings.fill(-1);
    auto loadedValues = defaultControlValues;

    if (const auto xmlState = getXmlFromBinary(data, sizeInBytes))
    {
        if (auto* mappings = xmlState->getChildByName("MidiMappings"))
        {
            for (auto* mapping = mappings->getFirstChildElement(); mapping != nullptr; mapping = mapping->getNextElement())
            {
                auto controlIndex = mapping->getIntAttribute("control", -1);

                // Version 1 had five knobs followed by five buttons. Keep their mappings
                // attached to the same actions after inserting the three Ableton knobs.
                if (xmlState->getIntAttribute("version", 1) < 2 && controlIndex >= 5)
                    controlIndex = controlIndex < 10 ? controlIndex + 3 : -1;

                if (juce::isPositiveAndBelow(controlIndex, controlCount))
                    loadedMappings[static_cast<size_t>(controlIndex)] = mapping->getIntAttribute("cc", -1);
            }
        }

        if (auto* values = xmlState->getChildByName("ControlValues"))
        {
            for (auto* value = values->getFirstChildElement(); value != nullptr; value = value->getNextElement())
            {
                const auto index = value->getIntAttribute("control", -1);
                const auto stored = value->getDoubleAttribute("value");
                if (juce::isPositiveAndBelow(index, controlCount) && std::isfinite(stored))
                    loadedValues[static_cast<size_t>(index)] = index < knobCount
                        ? snappedControlValue(static_cast<ControlId>(index), stored)
                        : juce::jlimit(0.0, 1.0, stored);
            }
        }
    }

    {
        const juce::SpinLock::ScopedLockType lock(midiMappingLock);
        midiMappings = loadedMappings;
        controlValues = loadedValues;
        pendingMidiValueDirty.fill(false);
    }

    triggerAsyncUpdate();
}

void AriaStudioAudioProcessor::sendOSC(const juce::String& address, float value)
{
    value = roundToTwoDecimals(value);
    const auto packet = buildOSCMessage(address, value);
    const juce::ScopedLock lock(oscSendLock);
    oscSenderSocket.write(oscHost, oscSendPort, packet.getData(), static_cast<int>(packet.getSize()));
}

void AriaStudioAudioProcessor::sendOSC(const juce::String& address, int value)
{
    const auto packet = buildOSCMessage(address, normaliseOscIntValue(address, value));
    const juce::ScopedLock lock(oscSendLock);
    oscSenderSocket.write(oscHost, oscSendPort, packet.getData(), static_cast<int>(packet.getSize()));
}

void AriaStudioAudioProcessor::sendOSC(const juce::String& address)
{
    const auto packet = buildOSCMessage(address, ",");
    const juce::ScopedLock lock(oscSendLock);
    oscSenderSocket.write(oscHost, oscSendPort, packet.getData(), static_cast<int>(packet.getSize()));
}

void AriaStudioAudioProcessor::beginMidiLearn(ControlId controlId)
{
    learningControlIndex.store(toIndex(controlId));
    triggerAsyncUpdate();
}

void AriaStudioAudioProcessor::clearMidiMapping(ControlId controlId)
{
    {
        const juce::SpinLock::ScopedLockType lock(midiMappingLock);
        midiMappings[static_cast<size_t>(toIndex(controlId))] = -1;
    }

    if (learningControlIndex.load() == toIndex(controlId))
        learningControlIndex.store(-1);

    triggerAsyncUpdate();
}

int AriaStudioAudioProcessor::getMappedMidiCC(ControlId controlId) const
{
    const juce::SpinLock::ScopedLockType lock(midiMappingLock);
    return midiMappings[static_cast<size_t>(toIndex(controlId))];
}

int AriaStudioAudioProcessor::getMappedButtonMidi(ControlId buttonId) const
{
    const juce::SpinLock::ScopedLockType lock(midiMappingLock);
    return midiMappings[static_cast<size_t>(toIndex(buttonId))];
}

bool AriaStudioAudioProcessor::isLearningControl(ControlId controlId) const
{
    return learningControlIndex.load() == toIndex(controlId);
}

double AriaStudioAudioProcessor::getControlValue(ControlId controlId) const
{
    const juce::SpinLock::ScopedLockType lock(midiMappingLock);
    return controlValues[static_cast<size_t>(toIndex(controlId))];
}

void AriaStudioAudioProcessor::setControlValue(ControlId controlId, double value)
{
    if (! std::isfinite(value))
        return;
    const auto index = toIndex(controlId);
    const juce::SpinLock::ScopedLockType lock(midiMappingLock);
    controlValues[static_cast<size_t>(index)] = index < knobCount
        ? snappedControlValue(controlId, value) : juce::jlimit(0.0, 1.0, value);
}

AriaStudioAudioProcessor::OSCStateSnapshot AriaStudioAudioProcessor::getOSCStateSnapshot() const
{
    const juce::ScopedLock lock(oscStateLock);

    const auto now = juce::Time::currentTimeMillis();
    const auto last = lastOscMessageMs.load();
    const bool connected = (last != 0) && ((now - last) < 4000);

    return OSCStateSnapshot {
        currentStatus,
        lastLog,
        temp,
        topP,
        minP,
        connected
    };
}

void AriaStudioAudioProcessor::handleAsyncUpdate()
{
    std::array<double, controlCount> midiValuesToApply {};
    std::array<bool, controlCount> midiValueIsDirty {};

    {
        const juce::SpinLock::ScopedLockType lock(midiMappingLock);
        midiValuesToApply = pendingMidiValues;
        midiValueIsDirty = pendingMidiValueDirty;
        pendingMidiValueDirty.fill(false);
    }

    if (activeEditor != nullptr)
    {
        for (int index = 0; index < knobCount; ++index)
        {
            if (midiValueIsDirty[static_cast<size_t>(index)])
                activeEditor->applyMappedControlValue(static_cast<ControlId>(index), midiValuesToApply[static_cast<size_t>(index)]);
        }
    }

    for (int index = 0; index < knobCount; ++index)
    {
        if (! midiValueIsDirty[static_cast<size_t>(index)])
            continue;

        const auto controlId = static_cast<ControlId>(index);
        setControlValue(controlId, midiValuesToApply[static_cast<size_t>(index)]);

        if (controlSpecs[static_cast<size_t>(index)].integerOnly)
            sendOSC(controlAddress(controlId), juce::roundToInt(midiValuesToApply[static_cast<size_t>(index)]));
        else
            sendOSC(controlAddress(controlId), static_cast<float>(midiValuesToApply[static_cast<size_t>(index)]));
    }

    if (activeEditor != nullptr)
    {
        for (int index = knobCount; index < controlCount; ++index)
        {
            if (midiValueIsDirty[static_cast<size_t>(index)])
                activeEditor->applyMidiButtonTrigger(static_cast<ControlId>(index));
        }
    }

    if (activeEditor != nullptr)
    {
        activeEditor->refreshControlValues();
        activeEditor->refreshStatusDisplay();
        activeEditor->repaint();
    }
}

void AriaStudioAudioProcessor::timerCallback()
{
    sendOSC("/aria/ping");
}

void AriaStudioAudioProcessor::handleIncomingOSCMessage(const void* data, size_t sizeInBytes)
{
    // Any datagram from the backend proves it is alive — drives the connection indicator.
    lastOscMessageMs.store(juce::Time::currentTimeMillis());

    const auto* bytes = static_cast<const char*>(data);
    size_t offset = 0;

    juce::String address;
    juce::String typeTags;

    if (! readPaddedString(bytes, sizeInBytes, offset, address))
        return;

    if (! readPaddedString(bytes, sizeInBytes, offset, typeTags))
        return;

    if (! typeTags.startsWithChar(','))
        return;

    if (address == "/aria/status" && typeTags == ",s")
    {
        juce::String statusValue;

        if (readPaddedString(bytes, sizeInBytes, offset, statusValue))
        {
            {
                const juce::ScopedLock lock(oscStateLock);
                currentStatus = statusValue;
            }

            triggerAsyncUpdate();
        }

        return;
    }

    if (address == "/aria/log" && typeTags == ",s")
    {
        juce::String logValue;

        if (readPaddedString(bytes, sizeInBytes, offset, logValue))
        {
            {
                const juce::ScopedLock lock(oscStateLock);
                lastLog = logValue;
            }

            triggerAsyncUpdate();
        }

        return;
    }

    if (address == "/aria/params" && typeTags == ",fff")
    {
        float newTemp = 0.0f;
        float newTopP = 0.0f;
        float newMinP = 0.0f;

        if (readFloat32(bytes, sizeInBytes, offset, newTemp)
            && readFloat32(bytes, sizeInBytes, offset, newTopP)
            && readFloat32(bytes, sizeInBytes, offset, newMinP))
        {
            const juce::ScopedLock lock(oscStateLock);
            temp = newTemp;
            topP = newTopP;
            minP = newMinP;
        }

        triggerAsyncUpdate();
        return;
    }

    if (address == "/generation_start" && typeTags == ",")
    {
        juce::MessageManager::callAsync([this]
        {
            if (activeEditor != nullptr)
                activeEditor->setGenerationActive(true);
        });
        return;
    }

    if (address == "/generation_done" && typeTags == ",")
    {
        juce::MessageManager::callAsync([this]
        {
            if (activeEditor != nullptr)
                activeEditor->setGenerationActive(false);
        });
        return;
    }

    if (address == "/generation_progress" && (typeTags == ",i" || typeTags == ",f"))
    {
        int percent = 0;
        float floatPercent = 0.0f;

        const bool valid = typeTags == ",i" ? readInt32(bytes, sizeInBytes, offset, percent)
            : readFloat32(bytes, sizeInBytes, offset, floatPercent);
        if (valid)
        {
            if (typeTags == ",f")
                percent = juce::roundToInt(floatPercent);
            juce::MessageManager::callAsync([this, percent]
            {
                if (activeEditor != nullptr)
                    activeEditor->setGenerationProgress(static_cast<float>(percent) / 100.0f);
            });
        }

        return;
    }

    if (address == "/playback_progress" && typeTags == ",f")
    {
        float progressValue = 0.0f;

        if (readFloat32(bytes, sizeInBytes, offset, progressValue))
        {
            juce::MessageManager::callAsync([this, progressValue]
            {
                if (activeEditor != nullptr)
                    activeEditor->setPlaybackProgress(progressValue);
            });
        }

        return;
    }

    if (address == "/playback_duration" && typeTags == ",f")
    {
        float durationValue = 0.0f;

        if (readFloat32(bytes, sizeInBytes, offset, durationValue))
        {
            juce::MessageManager::callAsync([this, durationValue]
            {
                if (activeEditor != nullptr)
                    activeEditor->setPlaybackDuration(durationValue);
            });
        }

        return;
    }

    if (address == "/playback_stopped" && typeTags == ",")
    {
        juce::MessageManager::callAsync([this]
        {
            if (activeEditor != nullptr)
                activeEditor->stopPlayback();
        });
        return;
    }
}

void AriaStudioAudioProcessor::handleIncomingMidiMessage(juce::MidiInput*, const juce::MidiMessage& message)
{
    handleMidiMessage(message);
}

void AriaStudioAudioProcessor::handleMidiMessage(const juce::MidiMessage& message)
{
    const auto learningIndex = learningControlIndex.load();
    bool shouldTrigger = false;

    if (message.isController())
    {
        const auto ccNumber = message.getControllerNumber();
        const auto ccValue = message.getControllerValue();

        {
            const juce::SpinLock::ScopedLockType lock(midiMappingLock);
            const bool pressed = ccValue > 0 && lastControllerValues[static_cast<size_t>(ccNumber)] == 0;
            lastControllerValues[static_cast<size_t>(ccNumber)] = ccValue;

            if (juce::isPositiveAndBelow(learningIndex, controlCount))
            {
                for (auto& mapping : midiMappings)
                {
                    if (mapping == ccNumber)
                        mapping = -1;
                }

                midiMappings[static_cast<size_t>(learningIndex)] = ccNumber;

                if (learningIndex < knobCount)
                {
                    pendingMidiValues[static_cast<size_t>(learningIndex)] =
                        midiValueToControlValue(static_cast<ControlId>(learningIndex), ccValue);
                }

                pendingMidiValueDirty[static_cast<size_t>(learningIndex)] = learningIndex < knobCount || pressed;
                learningControlIndex.store(-1);
                shouldTrigger = true;
            }
            else
            {
                for (int index = 0; index < controlCount; ++index)
                {
                    if (midiMappings[static_cast<size_t>(index)] != ccNumber)
                        continue;

                    if (index < knobCount)
                    {
                        pendingMidiValues[static_cast<size_t>(index)] =
                            midiValueToControlValue(static_cast<ControlId>(index), ccValue);
                    }

                    if (index < knobCount || pressed)
                    {
                        pendingMidiValueDirty[static_cast<size_t>(index)] = true;
                        shouldTrigger = true;
                    }
                }
            }
        }
    }
    else if (message.isNoteOn())
    {
        const auto noteNumber = message.getNoteNumber();
        const auto encodedNote = 128 + noteNumber;

        {
            const juce::SpinLock::ScopedLockType lock(midiMappingLock);

            if (juce::isPositiveAndBelow(learningIndex, controlCount) && learningIndex >= knobCount)
            {
                for (auto& mapping : midiMappings)
                {
                    if (mapping == encodedNote)
                        mapping = -1;
                }

                midiMappings[static_cast<size_t>(learningIndex)] = encodedNote;
                pendingMidiValueDirty[static_cast<size_t>(learningIndex)] = true;
                learningControlIndex.store(-1);
                shouldTrigger = true;
            }
            else
            {
                for (int index = knobCount; index < controlCount; ++index)
                {
                    if (midiMappings[static_cast<size_t>(index)] != encodedNote)
                        continue;

                    pendingMidiValueDirty[static_cast<size_t>(index)] = true;
                    shouldTrigger = true;
                }
            }
        }
    }

    if (shouldTrigger)
        triggerAsyncUpdate();
}

void AriaStudioAudioProcessor::startStandaloneMidiInputs()
{
    if (wrapperType != wrapperType_Standalone)
        return;

    for (const auto& device : juce::MidiInput::getAvailableDevices())
    {
        if (auto midiInput = juce::MidiInput::openDevice(device.identifier, this))
        {
            midiInput->start();
            standaloneMidiInputs.push_back(std::move(midiInput));
        }
    }
}

void AriaStudioAudioProcessor::stopStandaloneMidiInputs()
{
    for (auto& midiInput : standaloneMidiInputs)
    {
        if (midiInput != nullptr)
            midiInput->stop();
    }

    standaloneMidiInputs.clear();
}

void AriaStudioAudioProcessor::setEditor(AriaStudioAudioProcessorEditor* editorToUse)
{
    activeEditor = editorToUse;
}

void AriaStudioAudioProcessor::clearEditor(AriaStudioAudioProcessorEditor* editorToClear)
{
    if (activeEditor == editorToClear)
        activeEditor = nullptr;
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new AriaStudioAudioProcessor();
}
