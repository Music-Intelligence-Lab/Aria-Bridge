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
constexpr auto controlCount = static_cast<int>(AriaBridgeAudioProcessor::ControlId::count);

struct ControlSpec
{
    const char* address;
    double minimum;
    double maximum;
    bool integerOnly;
};

constexpr std::array<ControlSpec, controlCount> controlSpecs {{
    { "/aria/temp", 0.1, 2.0, false },
    { "/aria/top_p", 0.1, 1.0, false },
    { "/aria/min_p", 0.0, 0.3, false },
    { "/aria/tokens", 0.0, 2048.0, true },
    { "/aria/coherence", 1.0, 5.0, true },
    { "/aria/taste", 1.0, 5.0, true },
    { "/aria/repetition", 1.0, 5.0, true },
    { "/aria/continuity", 1.0, 5.0, true },
    { "/aria/grade", 1.0, 5.0, true },
}};

int toIndex(AriaBridgeAudioProcessor::ControlId controlId)
{
    return static_cast<int>(controlId);
}

float roundToTwoDecimals(float value)
{
    return std::round(value * 100.0f) / 100.0f;
}

float normaliseOscFloatValue(const juce::String& address, float value)
{
    if (address == "/aria/temp" || address == "/aria/top_p" || address == "/aria/min_p")
        return roundToTwoDecimals(value);

    return value;
}

int normaliseOscIntValue(const juce::String& address, int value)
{
    if (address == "/aria/tokens")
        return juce::jlimit(0, 2048, value);

    if (address == "/aria/coherence"
        || address == "/aria/taste"
        || address == "/aria/repetition"
        || address == "/aria/continuity"
        || address == "/aria/grade")
        return juce::jlimit(1, 5, value);

    if (address == "/aria/record")
        return juce::jlimit(0, 1, value);

    return value;
}

double snappedControlValue(AriaBridgeAudioProcessor::ControlId controlId, double value)
{
    const auto& spec = controlSpecs[static_cast<size_t>(toIndex(controlId))];
    const auto clamped = juce::jlimit(spec.minimum, spec.maximum, value);

    if (spec.integerOnly)
        return static_cast<double>(juce::roundToInt(clamped));

    return clamped;
}

double midiValueToControlValue(AriaBridgeAudioProcessor::ControlId controlId, int midiValue)
{
    const auto& spec = controlSpecs[static_cast<size_t>(toIndex(controlId))];
    const auto normalised = juce::jlimit(0, 127, midiValue) / 127.0;
    const auto mapped = spec.minimum + ((spec.maximum - spec.minimum) * normalised);
    return snappedControlValue(controlId, mapped);
}

juce::String controlAddress(AriaBridgeAudioProcessor::ControlId controlId)
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

class AriaBridgeAudioProcessor::OSCReceiverThread final : public juce::Thread
{
public:
    explicit OSCReceiverThread(AriaBridgeAudioProcessor& ownerToUse)
        : juce::Thread("AriaBridge OSC Receiver"), owner(ownerToUse)
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
    AriaBridgeAudioProcessor& owner;
};

AriaBridgeAudioProcessor::AriaBridgeAudioProcessor()
    : AudioProcessor(BusesProperties()),
      oscSenderSocket(false)
{
    midiMappings.fill(-1);
    pendingMidiValues.fill(0.0);
    pendingMidiValueDirty.fill(false);
    oscReceiverThread = std::make_unique<OSCReceiverThread>(*this);
    oscReceiverThread->startThread();
    startTimer(2000);
    launchBackendProcessIfNeeded();
    startStandaloneMidiInputs();
}

AriaBridgeAudioProcessor::~AriaBridgeAudioProcessor()
{
    stopTimer();
    cancelPendingUpdate();
    activeEditor = nullptr;
    stopStandaloneMidiInputs();
    backendProcess.kill();

    if (oscReceiverThread != nullptr)
    {
        oscReceiverThread->stopThread(1500);
        oscReceiverThread.reset();
    }
}

void AriaBridgeAudioProcessor::prepareToPlay(double, int)
{
}

void AriaBridgeAudioProcessor::releaseResources()
{
}

bool AriaBridgeAudioProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const
{
    juce::ignoreUnused(layouts);
    return true;
}

void AriaBridgeAudioProcessor::processBlock(juce::AudioBuffer<float>& audioBuffer,
                                            juce::MidiBuffer& midiMessages)
{
    juce::ScopedNoDenormals noDenormals;

    for (const auto metadata : midiMessages)
        handleMidiControllerMessage(metadata.getMessage());

    audioBuffer.clear();
}

juce::AudioProcessorEditor* AriaBridgeAudioProcessor::createEditor()
{
    return new AriaBridgeAudioProcessorEditor(*this);
}

bool AriaBridgeAudioProcessor::hasEditor() const
{
    return true;
}

const juce::String AriaBridgeAudioProcessor::getName() const
{
    return JucePlugin_Name;
}

bool AriaBridgeAudioProcessor::acceptsMidi() const
{
   #if JucePlugin_WantsMidiInput
    return true;
   #else
    return false;
   #endif
}

bool AriaBridgeAudioProcessor::producesMidi() const
{
   #if JucePlugin_ProducesMidiOutput
    return true;
   #else
    return false;
   #endif
}

bool AriaBridgeAudioProcessor::isMidiEffect() const
{
   #if JucePlugin_IsMidiEffect
    if (wrapperType == wrapperType_Standalone)
        return false;

    return true;
   #else
    return false;
   #endif
}

double AriaBridgeAudioProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

int AriaBridgeAudioProcessor::getNumPrograms()
{
    return 1;
}

int AriaBridgeAudioProcessor::getCurrentProgram()
{
    return 0;
}

void AriaBridgeAudioProcessor::setCurrentProgram(int)
{
}

const juce::String AriaBridgeAudioProcessor::getProgramName(int)
{
    return {};
}

void AriaBridgeAudioProcessor::changeProgramName(int, const juce::String&)
{
}

void AriaBridgeAudioProcessor::getStateInformation(juce::MemoryBlock& destData)
{
    juce::XmlElement state("AriaBridgeState");
    auto* mappings = state.createNewChildElement("MidiMappings");

    const juce::SpinLock::ScopedLockType lock(midiMappingLock);

    for (int index = 0; index < controlCount; ++index)
    {
        auto* mapping = mappings->createNewChildElement("Mapping");
        mapping->setAttribute("control", index);
        mapping->setAttribute("cc", midiMappings[static_cast<size_t>(index)]);
    }

    copyXmlToBinary(state, destData);
}

void AriaBridgeAudioProcessor::setStateInformation(const void* data, int sizeInBytes)
{
    std::array<int, controlCount> loadedMappings {};
    loadedMappings.fill(-1);

    if (const auto xmlState = getXmlFromBinary(data, sizeInBytes))
    {
        if (auto* mappings = xmlState->getChildByName("MidiMappings"))
        {
            for (auto* mapping = mappings->getFirstChildElement(); mapping != nullptr; mapping = mapping->getNextElement())
            {
                const auto controlIndex = mapping->getIntAttribute("control", -1);

                if (juce::isPositiveAndBelow(controlIndex, controlCount))
                    loadedMappings[static_cast<size_t>(controlIndex)] = mapping->getIntAttribute("cc", -1);
            }
        }
    }

    {
        const juce::SpinLock::ScopedLockType lock(midiMappingLock);
        midiMappings = loadedMappings;
    }

    triggerAsyncUpdate();
}

void AriaBridgeAudioProcessor::sendOSC(const juce::String& address, float value)
{
    const juce::String rounded = juce::String(value, 2);
    value = rounded.getFloatValue();
    const auto packet = buildOSCMessage(address, value);
    const juce::ScopedLock lock(oscSendLock);
    oscSenderSocket.write(oscHost, oscSendPort, packet.getData(), static_cast<int>(packet.getSize()));
}

void AriaBridgeAudioProcessor::sendOSC(const juce::String& address, int value)
{
    const auto packet = buildOSCMessage(address, normaliseOscIntValue(address, value));
    const juce::ScopedLock lock(oscSendLock);
    oscSenderSocket.write(oscHost, oscSendPort, packet.getData(), static_cast<int>(packet.getSize()));
}

void AriaBridgeAudioProcessor::sendOSC(const juce::String& address)
{
    const auto packet = buildOSCMessage(address, ",");
    const juce::ScopedLock lock(oscSendLock);
    oscSenderSocket.write(oscHost, oscSendPort, packet.getData(), static_cast<int>(packet.getSize()));
}

void AriaBridgeAudioProcessor::beginMidiLearn(ControlId controlId)
{
    learningControlIndex.store(toIndex(controlId));
    triggerAsyncUpdate();
}

void AriaBridgeAudioProcessor::clearMidiMapping(ControlId controlId)
{
    {
        const juce::SpinLock::ScopedLockType lock(midiMappingLock);
        midiMappings[static_cast<size_t>(toIndex(controlId))] = -1;
    }

    if (learningControlIndex.load() == toIndex(controlId))
        learningControlIndex.store(-1);

    triggerAsyncUpdate();
}

int AriaBridgeAudioProcessor::getMappedMidiCC(ControlId controlId) const
{
    const juce::SpinLock::ScopedLockType lock(midiMappingLock);
    return midiMappings[static_cast<size_t>(toIndex(controlId))];
}

bool AriaBridgeAudioProcessor::isLearningControl(ControlId controlId) const
{
    return learningControlIndex.load() == toIndex(controlId);
}

AriaBridgeAudioProcessor::OSCStateSnapshot AriaBridgeAudioProcessor::getOSCStateSnapshot() const
{
    const juce::ScopedLock lock(oscStateLock);

    return OSCStateSnapshot {
        currentStatus,
        lastLog,
        temp,
        topP,
        minP
    };
}

void AriaBridgeAudioProcessor::handleAsyncUpdate()
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
        for (int index = 0; index < controlCount; ++index)
        {
            if (midiValueIsDirty[static_cast<size_t>(index)])
                activeEditor->applyMappedControlValue(static_cast<ControlId>(index), midiValuesToApply[static_cast<size_t>(index)]);
        }
    }

    for (int index = 0; index < controlCount; ++index)
    {
        if (! midiValueIsDirty[static_cast<size_t>(index)])
            continue;

        const auto controlId = static_cast<ControlId>(index);

        if (controlSpecs[static_cast<size_t>(index)].integerOnly)
            sendOSC(controlAddress(controlId), juce::roundToInt(midiValuesToApply[static_cast<size_t>(index)]));
        else
            sendOSC(controlAddress(controlId), static_cast<float>(midiValuesToApply[static_cast<size_t>(index)]));
    }

    if (activeEditor != nullptr)
    {
        activeEditor->refreshStatusDisplay();
        activeEditor->repaint();
    }
}

void AriaBridgeAudioProcessor::timerCallback()
{
    sendOSC("/aria/ping");
}

void AriaBridgeAudioProcessor::handleIncomingOSCMessage(const void* data, size_t sizeInBytes)
{
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
    }
}

void AriaBridgeAudioProcessor::handleIncomingMidiMessage(juce::MidiInput*, const juce::MidiMessage& message)
{
    handleMidiControllerMessage(message);
}

void AriaBridgeAudioProcessor::handleMidiControllerMessage(const juce::MidiMessage& message)
{
    if (! message.isController())
        return;

    const auto ccNumber = message.getControllerNumber();
    const auto ccValue = message.getControllerValue();
    const auto learningIndex = learningControlIndex.load();
    bool shouldTrigger = false;

    {
        const juce::SpinLock::ScopedLockType lock(midiMappingLock);

        if (juce::isPositiveAndBelow(learningIndex, controlCount))
        {
            for (auto& mapping : midiMappings)
            {
                if (mapping == ccNumber)
                    mapping = -1;
            }

            midiMappings[static_cast<size_t>(learningIndex)] = ccNumber;
            pendingMidiValues[static_cast<size_t>(learningIndex)] =
                midiValueToControlValue(static_cast<ControlId>(learningIndex), ccValue);
            pendingMidiValueDirty[static_cast<size_t>(learningIndex)] = true;
            learningControlIndex.store(-1);
            shouldTrigger = true;
        }
        else
        {
            for (int index = 0; index < controlCount; ++index)
            {
                if (midiMappings[static_cast<size_t>(index)] != ccNumber)
                    continue;

                pendingMidiValues[static_cast<size_t>(index)] =
                    midiValueToControlValue(static_cast<ControlId>(index), ccValue);
                pendingMidiValueDirty[static_cast<size_t>(index)] = true;
                shouldTrigger = true;
            }
        }
    }

    if (shouldTrigger)
        triggerAsyncUpdate();
}

void AriaBridgeAudioProcessor::launchBackendProcessIfNeeded()
{
    if (wrapperType != wrapperType_Standalone)
        return;

    juce::File exeDir = juce::File::getSpecialLocation(juce::File::currentExecutableFile).getParentDirectory();
    juce::File launcherScript = exeDir.getChildFile("start.bat");

    if (! launcherScript.existsAsFile())
    {
        {
            const juce::ScopedLock lock(oscStateLock);
            currentStatus = "ERROR: start.bat not found next to exe";
        }

        triggerAsyncUpdate();
        return;
    }

    {
        const juce::ScopedLock lock(oscStateLock);
        lastLog = "Launching backend: " + launcherScript.getFullPathName();
    }

    juce::String command = "cmd.exe /c \"" + launcherScript.getFullPathName() + "\"";
    backendProcess.start(command);
    triggerAsyncUpdate();
}

void AriaBridgeAudioProcessor::startStandaloneMidiInputs()
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

void AriaBridgeAudioProcessor::stopStandaloneMidiInputs()
{
    for (auto& midiInput : standaloneMidiInputs)
    {
        if (midiInput != nullptr)
            midiInput->stop();
    }

    standaloneMidiInputs.clear();
}

void AriaBridgeAudioProcessor::setEditor(AriaBridgeAudioProcessorEditor* editorToUse)
{
    activeEditor = editorToUse;
}

void AriaBridgeAudioProcessor::clearEditor(AriaBridgeAudioProcessorEditor* editorToClear)
{
    if (activeEditor == editorToClear)
        activeEditor = nullptr;
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new AriaBridgeAudioProcessor();
}
