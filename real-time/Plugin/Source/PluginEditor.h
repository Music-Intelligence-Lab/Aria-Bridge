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

// Valhalla-descended look: near-black canvas, flat indicator-line knobs whose accent is read
// per-slider from rotarySliderFillColourId, plus a "hero" property for the big filled knob.
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
    void setGenerationProgress(float value);
    void stopPlayback();

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
                       AriaBridgeAudioProcessor::ControlId controlId,
                       juce::Colour accent,
                       bool hero);

    void configureActionButton(MidiLearnButton& button, const juce::String& text, juce::Colour accent);
    void configureNameLabel(juce::Label& label, const juce::String& text, juce::Colour accent);
    void configureValueLabel(juce::Label& label);
    void refreshValueLabel(AriaBridgeAudioProcessor::ControlId controlId);
    void showSliderContextMenu(AriaBridgeAudioProcessor::ControlId controlId);
    void showButtonContextMenu(AriaBridgeAudioProcessor::ControlId buttonId);
    LearnableSlider& getSliderForControl(AriaBridgeAudioProcessor::ControlId controlId);
    MidiLearnButton& getButtonForControl(AriaBridgeAudioProcessor::ControlId buttonId);
    juce::Label& getValueLabelForControl(AriaBridgeAudioProcessor::ControlId controlId);
    void configureStandaloneWindowIfNeeded();
    void layoutKnob(LearnableSlider& slider, juce::Label& nameLabel, juce::Label& valueLabel,
                    juce::Rectangle<int> cell, int knobSize);
    void drawPanel(juce::Graphics& g, juce::Rectangle<int> r, juce::Colour accent, const juce::String& head);
    void timerCallback() override;

    AriaBridgeAudioProcessor& audioProcessor;

    LearnableSlider tempSlider;
    LearnableSlider topPSlider;
    LearnableSlider minPSlider;
    LearnableSlider tokensSlider;
    LearnableSlider gradeSlider;

    juce::Label tempLabel;
    juce::Label topPLabel;
    juce::Label minPLabel;
    juce::Label tokensLabel;
    juce::Label gradeLabel;

    juce::Label tempValueLabel;
    juce::Label topPValueLabel;
    juce::Label minPValueLabel;
    juce::Label tokensValueLabel;
    juce::Label gradeValueLabel;

    MidiLearnButton recordButton;
    MidiLearnButton playButton;
    MidiLearnButton cancelButton;
    MidiLearnButton syncButton;
    MidiLearnButton commitButton;

    juce::Label statusLabel;
    juce::Label logLabel;
    juce::Label progressLabel;

    // Panel rectangles (computed in resized, drawn in paint).
    juce::Rectangle<int> samplingPanel, transportPanel, feedbackPanel, playbackPanel, barRect, statusStrip;

    // Animation / live state.
    double phase = 0.0;
    bool isGenerating = false;
    double genStartMs = 0.0;
    int generationElapsedSec = 0;
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
};
