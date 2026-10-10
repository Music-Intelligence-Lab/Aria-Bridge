#pragma once

#include <JuceHeader.h>

class AriaStudioAudioProcessorEditor;

class AriaStudioAudioProcessor final : public juce::AudioProcessor,
                                       private juce::AsyncUpdater,
                                       private juce::Timer,
                                       private juce::MidiInputCallback
{
public:
    // Knobs first (temp..measures), then buttons. `record` marks the
    // knob/button boundary; anything >= record is a momentary/toggle button.
    enum class ControlId
    {
        temp = 0,
        topP,
        minP,
        tokens,
        grade,
        track,
        slot,
        measures,
        record,
        sync,
        commit,
        play,
        cancel,
        clip,
        loop,
        autoFire,
        count
    };

    struct OSCStateSnapshot
    {
        juce::String currentStatus;
        juce::String lastLog;
        float temp = 0.0f;
        float topP = 0.0f;
        float minP = 0.0f;
        bool connected = false;
    };

    AriaStudioAudioProcessor();
    ~AriaStudioAudioProcessor() override;

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;

    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;

    void processBlock(juce::AudioBuffer<float>&, juce::MidiBuffer&) override;
    using AudioProcessor::processBlock;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override;

    const juce::String getName() const override;

    bool acceptsMidi() const override;
    bool producesMidi() const override;
    bool isMidiEffect() const override;
    double getTailLengthSeconds() const override;

    int getNumPrograms() override;
    int getCurrentProgram() override;
    void setCurrentProgram(int index) override;
    const juce::String getProgramName(int index) override;
    void changeProgramName(int index, const juce::String& newName) override;

    void getStateInformation(juce::MemoryBlock& destData) override;
    void setStateInformation(const void* data, int sizeInBytes) override;

    void sendOSC(const juce::String& address, float value);
    void sendOSC(const juce::String& address, int value);
    void sendOSC(const juce::String& address);

    void beginMidiLearn(ControlId controlId);
    void clearMidiMapping(ControlId controlId);
    int getMappedMidiCC(ControlId controlId) const;
    int getMappedButtonMidi(ControlId buttonId) const;
    bool isLearningControl(ControlId controlId) const;
    double getControlValue(ControlId controlId) const;
    void setControlValue(ControlId controlId, double value);

    OSCStateSnapshot getOSCStateSnapshot() const;
    void setEditor(AriaStudioAudioProcessorEditor* editorToUse);
    void clearEditor(AriaStudioAudioProcessorEditor* editorToClear);

private:
    class OSCReceiverThread;

    void handleAsyncUpdate() override;
    void timerCallback() override;
    void handleIncomingOSCMessage(const void* data, size_t sizeInBytes);
    void handleIncomingMidiMessage(juce::MidiInput* source, const juce::MidiMessage& message) override;
    void handleMidiMessage(const juce::MidiMessage& message);
    void startStandaloneMidiInputs();
    void stopStandaloneMidiInputs();

    mutable juce::CriticalSection oscStateLock;
    juce::CriticalSection oscSendLock;
    mutable juce::SpinLock midiMappingLock;

    juce::String currentStatus { "DISCONNECTED" };
    juce::String lastLog { "No log received yet." };
    float temp = 0.0f;
    float topP = 0.0f;
    float minP = 0.0f;

    std::array<int, static_cast<int>(ControlId::count)> midiMappings {};
    std::array<double, static_cast<int>(ControlId::count)> pendingMidiValues {};
    std::array<bool, static_cast<int>(ControlId::count)> pendingMidiValueDirty {};
    std::array<double, static_cast<int>(ControlId::count)> controlValues {};
    std::array<int, 128> lastControllerValues {};
    std::atomic<int> learningControlIndex { -1 };
    std::vector<std::unique_ptr<juce::MidiInput>> standaloneMidiInputs;

    juce::DatagramSocket oscSenderSocket;
    std::unique_ptr<OSCReceiverThread> oscReceiverThread;
    AriaStudioAudioProcessorEditor* activeEditor = nullptr;
    std::atomic<juce::int64> lastOscMessageMs { 0 };
};
