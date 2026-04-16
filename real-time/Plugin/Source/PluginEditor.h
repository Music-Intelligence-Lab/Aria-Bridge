#pragma once

#include "PluginProcessor.h"

class LearnableSlider final : public juce::Slider
{
public:
    std::function<void()> onContextMenuRequested;

    void mouseDown(const juce::MouseEvent& event) override
    {
        if (event.mods.isPopupMenu() && onContextMenuRequested != nullptr)
        {
            onContextMenuRequested();
            return;
        }

        juce::Slider::mouseDown(event);
    }
};

class MidiLearnButton final : public juce::TextButton
{
public:
    std::function<void()> onContextMenuRequested;

    void mouseDown(const juce::MouseEvent& event) override
    {
        if (event.mods.isPopupMenu() && onContextMenuRequested != nullptr)
        {
            onContextMenuRequested();
            return;
        }

        juce::TextButton::mouseDown(event);
    }
};

class AriaLookAndFeel final : public juce::LookAndFeel_V4
{
public:
    void drawRotarySlider(juce::Graphics& g,
                          int x,
                          int y,
                          int width,
                          int height,
                          float sliderPosProportional,
                          float rotaryStartAngle,
                          float rotaryEndAngle,
                          juce::Slider& slider) override;

    void drawButtonBackground(juce::Graphics& g,
                              juce::Button& button,
                              const juce::Colour& backgroundColour,
                              bool isMouseOverButton,
                              bool isButtonDown) override;

    void drawButtonText(juce::Graphics& g,
                        juce::TextButton& button,
                        bool isMouseOverButton,
                        bool isButtonDown) override;
};

class AriaBridgeAudioProcessorEditor final : public juce::AudioProcessorEditor,
                                             private juce::Timer
{
public:
    explicit AriaBridgeAudioProcessorEditor(AriaBridgeAudioProcessor&);
    ~AriaBridgeAudioProcessorEditor() override;

    void paint(juce::Graphics& g) override;
    void parentHierarchyChanged() override;
    void resized() override;
    void refreshStatusDisplay();
    void applyMappedControlValue(AriaBridgeAudioProcessor::ControlId controlId, double value);
    void applyMidiButtonTrigger(AriaBridgeAudioProcessor::ControlId buttonId);
    void setGenerationActive(bool active);
    void setPlaybackDuration(float seconds);
    void setPlaybackProgress(float value);
    void stopPlayback();

private:
    void configureFloatKnob(LearnableSlider& slider,
                            juce::Label& nameLabel,
                            juce::Label& valueLabel,
                            const juce::String& name,
                            double minimum,
                            double maximum,
                            double defaultValue,
                            const juce::String& oscAddress,
                            AriaBridgeAudioProcessor::ControlId controlId);

    void configureIntKnob(LearnableSlider& slider,
                          juce::Label& nameLabel,
                          juce::Label& valueLabel,
                          const juce::String& name,
                          int minimum,
                          int maximum,
                          int defaultValue,
                          const juce::String& oscAddress,
                          AriaBridgeAudioProcessor::ControlId controlId);

    void configureActionButton(MidiLearnButton& button, const juce::String& text);
    void configureNameLabel(juce::Label& label, const juce::String& text);
    void configureValueLabel(juce::Label& label);
    void updateRecordButtonAppearance();
    void refreshValueLabel(AriaBridgeAudioProcessor::ControlId controlId);
    void showSliderContextMenu(AriaBridgeAudioProcessor::ControlId controlId);
    void showButtonContextMenu(AriaBridgeAudioProcessor::ControlId buttonId);
    LearnableSlider& getSliderForControl(AriaBridgeAudioProcessor::ControlId controlId);
    MidiLearnButton& getButtonForControl(AriaBridgeAudioProcessor::ControlId buttonId);
    juce::Label& getValueLabelForControl(AriaBridgeAudioProcessor::ControlId controlId);
    void configureStandaloneWindowIfNeeded();
    void timerCallback() override;
    int statusBarHeight() const noexcept { return juce::jmax(44, juce::roundToInt(getHeight() * 0.14f)); }

    AriaBridgeAudioProcessor& audioProcessor;

    LearnableSlider tempSlider;
    LearnableSlider topPSlider;
    LearnableSlider minPSlider;
    LearnableSlider tokensSlider;

    LearnableSlider coherenceSlider;
    LearnableSlider tasteSlider;
    LearnableSlider repetitionSlider;
    LearnableSlider continuitySlider;
    LearnableSlider gradeSlider;

    juce::Label tempLabel;
    juce::Label topPLabel;
    juce::Label minPLabel;
    juce::Label tokensLabel;

    juce::Label coherenceLabel;
    juce::Label tasteLabel;
    juce::Label repetitionLabel;
    juce::Label continuityLabel;
    juce::Label gradeLabel;

    juce::Label tempValueLabel;
    juce::Label topPValueLabel;
    juce::Label minPValueLabel;
    juce::Label tokensValueLabel;

    juce::Label coherenceValueLabel;
    juce::Label tasteValueLabel;
    juce::Label repetitionValueLabel;
    juce::Label continuityValueLabel;
    juce::Label gradeValueLabel;

    MidiLearnButton recordButton;
    MidiLearnButton syncButton;
    MidiLearnButton commitButton;
    MidiLearnButton playButton;
    MidiLearnButton cancelButton;

    juce::Label statusLabel;
    juce::Label logLabel;

    double generationProgress = -1.0;
    double playbackProgress = 0.0;
    double playbackTotalDuration = 0.0;
    int generationElapsedSec = 0;
    juce::ProgressBar generationBar { generationProgress };
    juce::ProgressBar playbackBar { playbackProgress };
    juce::Label generationLabel;
    juce::Label playbackLabel;

    AriaLookAndFeel lookAndFeel;
    juce::ComponentBoundsConstrainer windowConstrainer;
    bool standaloneWindowConfigured = false;
    bool isRecordEnabled = false;
    bool isConnected = false;
    bool isPlaying = false;
};
