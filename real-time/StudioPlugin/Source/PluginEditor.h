#pragma once

#include "PluginProcessor.h"

// Slider that opens a MIDI-learn context menu on right-click instead of dragging.
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

// Button that opens a MIDI-learn context menu on right-click.
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

// Vector-rendered hardware controls, inspired by Retrologue's front panel.
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

class AriaStudioAudioProcessorEditor final : public juce::AudioProcessorEditor,
                                             private juce::Timer
{
public:
    explicit AriaStudioAudioProcessorEditor(AriaStudioAudioProcessor&);
    ~AriaStudioAudioProcessorEditor() override;

    void paint(juce::Graphics& g) override;
    void parentHierarchyChanged() override;
    void resized() override;
    void refreshStatusDisplay();
    void refreshControlValues();
    void applyMappedControlValue(AriaStudioAudioProcessor::ControlId controlId, double value);
    void applyMidiButtonTrigger(AriaStudioAudioProcessor::ControlId buttonId);
    void setGenerationActive(bool active);
    void setPlaybackDuration(float seconds);
    void setPlaybackProgress(float value);
    void setGenerationProgress(float value);
    void stopPlayback();
    juce::Rectangle<int> getProgressDisplayBounds() const { return barRect; }

private:
    void configureKnob(LearnableSlider& slider,
                       juce::Label& nameLabel,
                       juce::Label& valueLabel,
                       const juce::String& name,
                       double minimum,
                       double maximum,
                       double interval,
                       double defaultValue,
                       const juce::String& oscAddress,
                       AriaStudioAudioProcessor::ControlId controlId,
                       juce::Colour accent);

    void configureActionButton(MidiLearnButton& button, const juce::String& text, juce::Colour accent);
    void configureNameLabel(juce::Label& label, const juce::String& text, juce::Colour accent);
    void configureValueLabel(juce::Label& label);
    void refreshValueLabel(AriaStudioAudioProcessor::ControlId controlId);
    void showSliderContextMenu(AriaStudioAudioProcessor::ControlId controlId);
    void showButtonContextMenu(AriaStudioAudioProcessor::ControlId buttonId);
    LearnableSlider& getSliderForControl(AriaStudioAudioProcessor::ControlId controlId);
    MidiLearnButton& getButtonForControl(AriaStudioAudioProcessor::ControlId buttonId);
    juce::Label& getValueLabelForControl(AriaStudioAudioProcessor::ControlId controlId);
    void configureStandaloneWindowIfNeeded();
    void layoutKnob(LearnableSlider& slider, juce::Label& nameLabel, juce::Label& valueLabel,
                    juce::Rectangle<int> cell, int knobSize);
    void timerCallback() override;

    AriaStudioAudioProcessor& audioProcessor;

    LearnableSlider tempSlider;
    LearnableSlider topPSlider;
    LearnableSlider minPSlider;
    LearnableSlider tokensSlider;
    LearnableSlider gradeSlider;
    LearnableSlider trackSlider;
    LearnableSlider slotSlider;
    LearnableSlider measuresSlider;

    juce::Label tempLabel;
    juce::Label topPLabel;
    juce::Label minPLabel;
    juce::Label tokensLabel;
    juce::Label gradeLabel;
    juce::Label trackLabel;
    juce::Label slotLabel;
    juce::Label measuresLabel;

    juce::Label tempValueLabel;
    juce::Label topPValueLabel;
    juce::Label minPValueLabel;
    juce::Label tokensValueLabel;
    juce::Label gradeValueLabel;
    juce::Label trackValueLabel;
    juce::Label slotValueLabel;
    juce::Label measuresValueLabel;

    MidiLearnButton recordButton;
    MidiLearnButton playButton;
    MidiLearnButton cancelButton;
    MidiLearnButton syncButton;
    MidiLearnButton commitButton;
    MidiLearnButton clipButton;
    MidiLearnButton loopButton;
    MidiLearnButton autoButton;

    juce::Label statusLabel;
    juce::Label logLabel;
    juce::Label progressLabel;

    juce::Rectangle<int> headerRect, barRect;
    juce::Rectangle<int> generationRect, destinationRect, transportRect, modeRect, activityRect;
    int contentLeft = 0, contentTop = 0;

    // Animation / live state.
    double phase = 0.0;
    bool isGenerating = false;
    double playbackTarget = 0.0;
    double playbackDisplayed = 0.0;
    double playbackTotalDuration = 0.0;
    double genProgress = 0.0;
    float uiScale = 1.0f;

    AriaLookAndFeel lookAndFeel;
    juce::ComponentBoundsConstrainer windowConstrainer;
    bool standaloneWindowConfigured = false;
    bool isRecordEnabled = false;
    bool isConnected = false;
    bool isPlaying = false;
    juce::TooltipWindow tooltipWindow { this, 700 };
};
