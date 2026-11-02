#include "PluginEditor.h"

#include <cmath>

namespace
{
const juce::Colour ground     = juce::Colour::fromRGB(54, 54, 54);
const juce::Colour knobDark   = juce::Colour::fromRGB(48, 48, 48);
const juce::Colour buttonDark = juce::Colour::fromRGB(25, 25, 25);
const juce::Colour ink        = juce::Colour::fromRGB(176, 176, 176);
const juce::Colour muted      = juce::Colour::fromRGB(126, 126, 126);
const juce::Colour lineColour = juce::Colour::fromRGB(88, 88, 88);
const juce::Colour cyan       = juce::Colour::fromRGB(3, 195, 213);
const juce::Colour mint       = juce::Colour::fromRGB(0, 211, 141);
const juce::Colour amber      = juce::Colour::fromRGB(255, 183, 77);
const juce::Colour coral      = juce::Colour::fromRGB(255, 77, 71);
const juce::Colour magenta    = juce::Colour::fromRGB(245, 0, 255);
const juce::Colour green      = juce::Colour::fromRGB(86, 255, 88);
const juce::Colour progressGreen = juce::Colour::fromRGB(0, 156, 46);

juce::String formatFloatValue(double value) { return juce::String(value, 2); }
juce::String formatIntValue(double value)   { return juce::String(juce::roundToInt(value)); }

juce::String formatClock(int totalSeconds)
{
    const int s = juce::jmax(0, totalSeconds);
    return juce::String(s / 60) + ":" + juce::String(s % 60).paddedLeft('0', 2);
}

juce::Colour stateColour(const juce::String& status)
{
    if (status == "RECORDING")  return coral;
    if (status == "GENERATING") return amber;
    if (status == "PLAYING")    return mint;
    if (status == "IDLE" || status == "READY") return cyan;
    return muted;
}

juce::Rectangle<float> actionShape(const juce::Button& button)
{
    const auto bounds = button.getLocalBounds().toFloat();
    const float size = juce::jmin(bounds.getWidth() - 4.0f, bounds.getHeight() * 0.64f);
    return { bounds.getCentreX() - size * 0.5f, bounds.getY(), size, size };
}
}

void AriaLookAndFeel::drawRotarySlider(juce::Graphics& g,
                                      int x, int y, int width, int height,
                                      float sliderPosProportional,
                                      float rotaryStartAngle, float rotaryEndAngle,
                                      juce::Slider& slider)
{
    const auto bounds = juce::Rectangle<float>((float) x, (float) y, (float) width, (float) height).reduced(2.0f);
    const auto centre = bounds.getCentre();
    const float radius = juce::jmin(bounds.getWidth(), bounds.getHeight()) * 0.5f;
    const float stroke = juce::jmax(2.0f, radius * 0.17f);
    const float arcRadius = radius - stroke * 0.5f;
    const float angle = rotaryStartAngle + sliderPosProportional * (rotaryEndAngle - rotaryStartAngle);

    g.setColour(ground);
    g.fillEllipse(bounds);
    juce::Path track;
    track.addCentredArc(centre.x, centre.y, arcRadius, arcRadius, 0.0f, rotaryStartAngle, rotaryEndAngle, true);
    g.setColour(buttonDark);
    g.strokePath(track, juce::PathStrokeType(stroke, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));

    if (sliderPosProportional > 0.0f)
    {
        juce::Path value;
        value.addCentredArc(centre.x, centre.y, arcRadius, arcRadius, 0.0f, rotaryStartAngle, angle, true);
        g.setColour(slider.findColour(juce::Slider::rotarySliderFillColourId));
        g.strokePath(value, juce::PathStrokeType(stroke, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));
    }

    g.setColour(buttonDark);
    g.drawLine({ centre.getPointOnCircumference(radius * 0.12f, angle),
                 centre.getPointOnCircumference(radius * 0.77f, angle) },
               juce::jmax(2.0f, radius * 0.15f));
}

void AriaLookAndFeel::drawButtonBackground(juce::Graphics& g,
                                          juce::Button& button,
                                          const juce::Colour&,
                                          bool isMouseOverButton,
                                          bool isButtonDown)
{
    const bool on = button.getToggleState() || isButtonDown;
    const auto shape = actionShape(button).reduced(0.75f);
    auto fill = on ? button.findColour(juce::TextButton::buttonOnColourId) : buttonDark;
    if (! on && isMouseOverButton)
        fill = fill.brighter(0.12f);
    g.setColour(fill);
    if (button.getClickingTogglesState())
        g.fillRect(shape);
    else
        g.fillEllipse(shape);

    g.setColour(buttonDark.darker(0.2f));
    if (button.getClickingTogglesState())
        g.drawRect(shape, 1.5f);
    else
        g.drawEllipse(shape, 1.5f);
}

void AriaLookAndFeel::drawButtonText(juce::Graphics& g, juce::TextButton& button, bool, bool)
{
    const auto shape = actionShape(button);
    const float scale = shape.getWidth() / 50.0f;
    const auto label = button.getLocalBounds().toFloat().withTop(shape.getBottom() + 3.0f * scale);
    g.setColour(knobDark);
    g.fillRoundedRectangle(label, 3.0f * scale);
    g.setColour(ink);
    g.setFont(juce::Font(juce::FontOptions(14.0f * scale)));
    g.drawFittedText(button.getButtonText(), label.toNearestInt(), juce::Justification::centred, 1);
}

AriaBridgeAudioProcessorEditor::AriaBridgeAudioProcessorEditor(AriaBridgeAudioProcessor& processor)
    : AudioProcessorEditor(&processor), audioProcessor(processor)
{
    setLookAndFeel(&lookAndFeel);
    windowConstrainer.setMinimumSize(560, 190);
    windowConstrainer.setMaximumSize(1500, 500);

    using Control = AriaBridgeAudioProcessor::ControlId;
    configureKnob(tempSlider, tempLabel, tempValueLabel, "temp", 0.1, 2.0, 0.01, 1.00, "/aria/temp", Control::temp, cyan);
    configureKnob(topPSlider, topPLabel, topPValueLabel, "top_p", 0.7, 1.0, 0.01, 0.98, "/aria/top_p", Control::topP, cyan);
    configureKnob(minPSlider, minPLabel, minPValueLabel, "min_p", 0.0, 0.1, 0.01, 0.01, "/aria/min_p", Control::minP, cyan);
    configureKnob(tokensSlider, tokensLabel, tokensValueLabel, "tokens", 0, 2048, 1.0, 1000, "/aria/tokens", Control::tokens, cyan);
    configureKnob(trackSlider, trackLabel, trackValueLabel, "track", 0, 10, 1.0, 0, "/aria/track", Control::track, cyan);
    configureKnob(slotSlider, slotLabel, slotValueLabel, "slot", 0, 10, 1.0, 0, "/aria/slot", Control::slot, cyan);
    configureKnob(measuresSlider, measuresLabel, measuresValueLabel, "measures", 0, 60, 1.0, 0, "/aria/measures", Control::measures, cyan);
    configureKnob(gradeSlider, gradeLabel, gradeValueLabel, "grade", 1, 5, 1.0, 3, "/aria/grade", Control::grade, cyan);

    configureActionButton(recordButton, "record", coral);
    configureActionButton(clipButton, "clip", cyan);
    configureActionButton(loopButton, "loop", magenta);
    configureActionButton(autoButton, "auto", green);
    configureActionButton(playButton, "play", mint);
    configureActionButton(cancelButton, "cancel", juce::Colour::fromRGB(255, 105, 127));
    configureActionButton(syncButton, "sync", cyan);
    configureActionButton(commitButton, "commit", juce::Colour::fromRGB(0, 31, 139));

    for (const auto control : { Control::record, Control::clip, Control::loop, Control::autoFire })
    {
        auto& button = getButtonForControl(control);
        button.setClickingTogglesState(true);
        button.setToggleState(audioProcessor.getControlValue(control) >= 0.5, juce::dontSendNotification);
    }
    for (int index = static_cast<int>(Control::record); index < static_cast<int>(Control::count); ++index)
    {
        const auto control = static_cast<Control>(index);
        getButtonForControl(control).onContextMenuRequested = [this, control] { showButtonContextMenu(control); };
    }

    recordButton.onClick = [this]
    {
        isRecordEnabled = recordButton.getToggleState();
        audioProcessor.setControlValue(Control::record, isRecordEnabled ? 1.0 : 0.0);
        audioProcessor.sendOSC("/aria/record", isRecordEnabled ? 1 : 0);
    };
    clipButton.onClick = [this]
    {
        const bool enabled = clipButton.getToggleState();
        audioProcessor.setControlValue(Control::clip, enabled ? 1.0 : 0.0);
        audioProcessor.sendOSC("/aria/clip", enabled ? 1 : 0);
        if (! enabled && loopButton.getToggleState())
        {
            loopButton.setToggleState(false, juce::dontSendNotification);
            audioProcessor.setControlValue(Control::loop, 0.0);
            audioProcessor.sendOSC("/aria/loop", 0);
        }
        repaint();
    };
    loopButton.onClick = [this]
    {
        const bool enabled = loopButton.getToggleState();
        if (enabled && ! clipButton.getToggleState())
        {
            clipButton.setToggleState(true, juce::dontSendNotification);
            audioProcessor.setControlValue(Control::clip, 1.0);
            audioProcessor.sendOSC("/aria/clip", 1);
        }
        audioProcessor.setControlValue(Control::loop, enabled ? 1.0 : 0.0);
        audioProcessor.sendOSC("/aria/loop", enabled ? 1 : 0);
        repaint();
    };
    autoButton.onClick = [this]
    {
        const bool enabled = autoButton.getToggleState();
        audioProcessor.setControlValue(Control::autoFire, enabled ? 1.0 : 0.0);
        audioProcessor.sendOSC("/aria/fire", enabled ? 1 : 0);
    };
    syncButton.onClick = [this]
    {
        for (int index = 0; index < static_cast<int>(Control::record); ++index)
        {
            const auto control = static_cast<Control>(index);
            auto& slider = getSliderForControl(control);
            slider.onValueChange();
        }
        audioProcessor.sendOSC("/aria/clip", clipButton.getToggleState() ? 1 : 0);
        audioProcessor.sendOSC("/aria/loop", loopButton.getToggleState() ? 1 : 0);
        audioProcessor.sendOSC("/aria/fire", autoButton.getToggleState() ? 1 : 0);
        audioProcessor.sendOSC("/aria/ping");
    };
    commitButton.onClick = [this] { audioProcessor.sendOSC("/aria/commit"); };
    playButton.onClick = [this] { audioProcessor.sendOSC("/aria/play"); };
    cancelButton.onClick = [this]
    {
        audioProcessor.sendOSC("/aria/cancel");
        audioProcessor.sendOSC("/cancel_playback");
        audioProcessor.resetProgressState();
        recordButton.setToggleState(false, juce::dontSendNotification);
        audioProcessor.setControlValue(Control::record, 0.0);
        isRecordEnabled = false;
    };

    recordButton.setTooltip("Record input; release to generate.");
    clipButton.setTooltip("Write generated MIDI to the Ableton clip at track / slot.");
    loopButton.setTooltip("Run alternating recording and generation with clip output enabled.");
    autoButton.setTooltip("Automatically launch a clip after writing it.");
    trackSlider.setTooltip("Ableton destination track (0-based).");
    slotSlider.setTooltip("Ableton destination clip slot (0-based).");
    measuresSlider.setTooltip("Clip length in measures. 0 keeps the full clip.");
    gradeSlider.setTooltip("Rate the take from 1 to 5, then commit.");
    statusLabel.setColour(juce::Label::textColourId, juce::Colours::transparentBlack);
    addAndMakeVisible(statusLabel);
    logLabel.setVisible(false);
    progressLabel.setColour(juce::Label::textColourId, ink);
    progressLabel.setJustificationType(juce::Justification::centred);
    addAndMakeVisible(progressLabel);

    setResizable(true, false);
    setResizeLimits(560, 190, 1500, 500);
    setSize(660, 220);
    refreshControlValues();
    refreshStatusDisplay();
    audioProcessor.setEditor(this);
    configureStandaloneWindowIfNeeded();
    startTimerHz(30);
}

AriaBridgeAudioProcessorEditor::~AriaBridgeAudioProcessorEditor()
{
    stopTimer();
    setLookAndFeel(nullptr);
    audioProcessor.clearEditor(this);
}

void AriaBridgeAudioProcessorEditor::paint(juce::Graphics& g)
{
    const float s = uiScale;
    g.fillAll(ground);
    g.setColour(lineColour);
    g.drawRect(getLocalBounds(), juce::jmax(1, juce::roundToInt(s)));
    g.setColour(ink);
    g.setFont(juce::Font(juce::FontOptions(20.0f * s)));
    g.drawText("Aria Bridge", headerRect, juce::Justification::centred);
    g.setColour(lineColour);
    g.drawRoundedRectangle(headerRect.withTop(headerRect.getBottom() + juce::roundToInt(3 * s))
                                     .withHeight(juce::roundToInt(3 * s)).toFloat(), 1.5f * s, 1.0f);

    const float dot = 5.0f * s;
    const auto colour = stateColour(statusLabel.getText());
    g.setColour(colour);
    g.fillEllipse(headerRect.getRight() - 12.0f * s, headerRect.getCentreY() - dot * 0.5f, dot, dot);

    const auto bar = barRect.toFloat();
    g.setColour(buttonDark);
    g.fillRect(bar);
    const float progress = (float) juce::jlimit(0.0, 1.0, playbackDisplayed);
    const float fillHeight = progress * bar.getHeight();
    if (fillHeight > 0.0f)
    {
        g.setColour(progressGreen);
        g.fillRect(bar.withTop(bar.getBottom() - fillHeight));
    }
    g.setColour(progressGreen);
    g.fillRect(bar.withTop(bar.getBottom() - 5.0f * s).reduced(1.0f * s, 0.0f));
    const float handleSize = bar.getWidth() * 0.76f;
    const float handleY = bar.getY() + 6.0f * s + progress * (bar.getHeight() - handleSize - 12.0f * s);
    g.setColour(knobDark);
    g.fillEllipse(bar.getCentreX() - handleSize * 0.5f, handleY, handleSize, handleSize);

    using Control = AriaBridgeAudioProcessor::ControlId;
    for (int index = 0; index < static_cast<int>(Control::count); ++index)
    {
        const auto control = static_cast<Control>(index);
        if (! audioProcessor.isLearningControl(control))
            continue;
        g.setColour(amber);
        if (index < static_cast<int>(Control::record))
            g.drawEllipse(getSliderForControl(control).getBounds().toFloat().expanded(1.5f * s), 2.0f * s);
        else
        {
            auto& button = getButtonForControl(control);
            const auto shape = actionShape(button).translated((float) button.getX(), (float) button.getY()).expanded(2.0f * s);
            if (button.getClickingTogglesState())
                g.drawRect(shape, 2.0f * s);
            else
                g.drawEllipse(shape, 2.0f * s);
        }
    }
}

void AriaBridgeAudioProcessorEditor::parentHierarchyChanged()
{
    AudioProcessorEditor::parentHierarchyChanged();
    configureStandaloneWindowIfNeeded();
}

void AriaBridgeAudioProcessorEditor::layoutKnob(LearnableSlider& slider, juce::Label& nameLabel,
                                               juce::Label& valueLabel, juce::Rectangle<int> cell, int knobSize)
{
    const int nameH = juce::roundToInt(17 * uiScale);
    const int valueH = juce::roundToInt(17 * uiScale);
    nameLabel.setBounds(cell.withHeight(nameH));
    valueLabel.setBounds(cell.withTop(cell.getBottom() - valueH));
    const int size = juce::jmin(knobSize, cell.getHeight() - nameH - valueH, cell.getWidth() - 4);
    slider.setBounds(cell.getCentreX() - size / 2, cell.getY() + nameH, size, size);
}

void AriaBridgeAudioProcessorEditor::resized()
{
    uiScale = juce::jmin(getWidth() / 660.0f, getHeight() / 220.0f);
    const float s = uiScale;
    const int left = juce::roundToInt(10 * s);
    const int top = (getHeight() - juce::roundToInt(220 * s)) / 2;
    const int barWidth = juce::roundToInt(50 * s);
    const int barGap = juce::roundToInt(18 * s);
    barRect = { getWidth() - juce::roundToInt(14 * s) - barWidth, top + juce::roundToInt(10 * s),
                barWidth, juce::roundToInt(198 * s) };
    const int controlsRight = barRect.getX() - barGap;
    const int controlsWidth = controlsRight - left;
    headerRect = { left + juce::roundToInt(30 * s), top + juce::roundToInt(3 * s),
                   controlsWidth - juce::roundToInt(60 * s), juce::roundToInt(28 * s) };
    statusLabel.setBounds(headerRect);
    progressLabel.setBounds(barRect);
    progressLabel.setFont(juce::Font(juce::FontOptions(9.0f * s)));

    std::array<MidiLearnButton*, 8> buttons {{
        &recordButton, &clipButton, &loopButton, &autoButton, &playButton, &cancelButton, &syncButton, &commitButton
    }};
    std::array<std::tuple<LearnableSlider*, juce::Label*, juce::Label*>, 8> knobs {{
        { &tempSlider, &tempLabel, &tempValueLabel },
        { &topPSlider, &topPLabel, &topPValueLabel },
        { &minPSlider, &minPLabel, &minPValueLabel },
        { &tokensSlider, &tokensLabel, &tokensValueLabel },
        { &trackSlider, &trackLabel, &trackValueLabel },
        { &slotSlider, &slotLabel, &slotValueLabel },
        { &measuresSlider, &measuresLabel, &measuresValueLabel },
        { &gradeSlider, &gradeLabel, &gradeValueLabel }
    }};
    for (int i = 0; i < 8; ++i)
    {
        const int cellLeft = left + controlsWidth * i / 8;
        const int cellRight = left + controlsWidth * (i + 1) / 8;
        const int cellWidth = cellRight - cellLeft;
        buttons[(size_t) i]->setBounds(cellLeft + juce::roundToInt(5 * s), top + juce::roundToInt(58 * s),
                                      cellWidth - juce::roundToInt(10 * s), juce::roundToInt(82 * s));
        auto& knob = knobs[(size_t) i];
        std::get<1>(knob)->setFont(juce::Font(juce::FontOptions(14.0f * s)));
        std::get<2>(knob)->setFont(juce::Font(juce::FontOptions(14.0f * s)));
        layoutKnob(*std::get<0>(knob), *std::get<1>(knob), *std::get<2>(knob),
                   { cellLeft, top + juce::roundToInt(143 * s), cellWidth, juce::roundToInt(68 * s) },
                   juce::roundToInt(34 * s));
    }
}

void AriaBridgeAudioProcessorEditor::refreshControlValues()
{
    using Control = AriaBridgeAudioProcessor::ControlId;
    for (int index = 0; index < static_cast<int>(Control::record); ++index)
    {
        const auto control = static_cast<Control>(index);
        getSliderForControl(control).setValue(audioProcessor.getControlValue(control), juce::dontSendNotification);
        refreshValueLabel(control);
    }
    for (const auto control : { Control::record, Control::clip, Control::loop, Control::autoFire })
        getButtonForControl(control).setToggleState(audioProcessor.getControlValue(control) >= 0.5, juce::dontSendNotification);
    isRecordEnabled = recordButton.getToggleState();
    repaint();
}

void AriaBridgeAudioProcessorEditor::refreshStatusDisplay()
{
    const auto snapshot = audioProcessor.getOSCStateSnapshot();
    isConnected = snapshot.connected;
    const juce::String shown = isConnected ? snapshot.currentStatus : juce::String("DISCONNECTED");
    statusLabel.setText(shown, juce::dontSendNotification);
    statusLabel.setTooltip(shown + "\n" + snapshot.lastLog);
    logLabel.setText(snapshot.lastLog, juce::dontSendNotification);
    repaint();
}

void AriaBridgeAudioProcessorEditor::applyMappedControlValue(AriaBridgeAudioProcessor::ControlId controlId, double value)
{
    getSliderForControl(controlId).setValue(value, juce::dontSendNotification);
    refreshValueLabel(controlId);
}

void AriaBridgeAudioProcessorEditor::configureKnob(LearnableSlider& slider,
                                                   juce::Label& nameLabel,
                                                   juce::Label& valueLabel,
                                                   const juce::String& name,
                                                   double minimum, double maximum, double interval,
                                                   double defaultValue,
                                                   const juce::String& oscAddress,
                                                   AriaBridgeAudioProcessor::ControlId controlId,
                                                   juce::Colour accent)
{
    const bool isInt = (interval >= 1.0);
    slider.setSliderStyle(juce::Slider::RotaryVerticalDrag);
    slider.setTextBoxStyle(juce::Slider::NoTextBox, false, 0, 0);
    slider.setRange(minimum, maximum, interval);
    slider.setValue(audioProcessor.getControlValue(controlId), juce::dontSendNotification);
    slider.setDoubleClickReturnValue(true, defaultValue);
    slider.setRotaryParameters(juce::MathConstants<float>::pi * 1.25f, juce::MathConstants<float>::pi * 2.75f, true);
    slider.setName(name);
    slider.setColour(juce::Slider::rotarySliderFillColourId, accent);
    slider.onValueChange = [this, &slider, controlId, oscAddress, isInt]
    {
        audioProcessor.setControlValue(controlId, slider.getValue());
        refreshValueLabel(controlId);
        if (isInt) audioProcessor.sendOSC(oscAddress, static_cast<int>(slider.getValue()));
        else       audioProcessor.sendOSC(oscAddress, static_cast<float>(slider.getValue()));
    };
    slider.onContextMenuRequested = [this, controlId] { showSliderContextMenu(controlId); };
    addAndMakeVisible(slider);

    configureNameLabel(nameLabel, name, accent);
    configureValueLabel(valueLabel);
    refreshValueLabel(controlId);
}

void AriaBridgeAudioProcessorEditor::configureActionButton(MidiLearnButton& button, const juce::String& text, juce::Colour accent)
{
    button.setButtonText(text);
    button.setName(text);
    button.setColour(juce::TextButton::buttonColourId, accent);
    button.setColour(juce::TextButton::buttonOnColourId, accent);
    addAndMakeVisible(button);
}

void AriaBridgeAudioProcessorEditor::configureNameLabel(juce::Label& label, const juce::String& text, juce::Colour accent)
{
    label.setText(text, juce::dontSendNotification);
    label.setJustificationType(juce::Justification::centred);
    juce::ignoreUnused(accent);
    label.setColour(juce::Label::textColourId, ink);
    label.setBorderSize(juce::BorderSize<int>(0));
    label.setFont(juce::Font(juce::FontOptions(14.0f)));
    addAndMakeVisible(label);
}

void AriaBridgeAudioProcessorEditor::configureValueLabel(juce::Label& label)
{
    label.setJustificationType(juce::Justification::centred);
    label.setColour(juce::Label::textColourId, ink);
    label.setBorderSize(juce::BorderSize<int>(0));
    label.setFont(juce::Font(juce::FontOptions(14.0f)));
    addAndMakeVisible(label);
}

void AriaBridgeAudioProcessorEditor::refreshValueLabel(AriaBridgeAudioProcessor::ControlId controlId)
{
    auto& slider = getSliderForControl(controlId);
    auto& valueLabel = getValueLabelForControl(controlId);
    const auto isFloatControl = controlId == AriaBridgeAudioProcessor::ControlId::temp
        || controlId == AriaBridgeAudioProcessor::ControlId::topP
        || controlId == AriaBridgeAudioProcessor::ControlId::minP;

    valueLabel.setText(isFloatControl ? formatFloatValue(slider.getValue()) : formatIntValue(slider.getValue()),
                       juce::dontSendNotification);
}

void AriaBridgeAudioProcessorEditor::showSliderContextMenu(AriaBridgeAudioProcessor::ControlId controlId)
{
    juce::PopupMenu menu;
    menu.addItem(1, "MIDI Learn");
    const auto mappedCc = audioProcessor.getMappedMidiCC(controlId);
    if (mappedCc >= 0)
        menu.addItem(2, "Clear MIDI mapping (CC " + juce::String(mappedCc) + ")");

    menu.showMenuAsync(juce::PopupMenu::Options(),
                       [this, controlId] (int result)
                       {
                           if (result == 1) audioProcessor.beginMidiLearn(controlId);
                           else if (result == 2) audioProcessor.clearMidiMapping(controlId);
                       });
}

LearnableSlider& AriaBridgeAudioProcessorEditor::getSliderForControl(AriaBridgeAudioProcessor::ControlId controlId)
{
    switch (controlId)
    {
        case AriaBridgeAudioProcessor::ControlId::temp: return tempSlider;
        case AriaBridgeAudioProcessor::ControlId::topP: return topPSlider;
        case AriaBridgeAudioProcessor::ControlId::minP: return minPSlider;
        case AriaBridgeAudioProcessor::ControlId::tokens: return tokensSlider;
        case AriaBridgeAudioProcessor::ControlId::grade: return gradeSlider;
        case AriaBridgeAudioProcessor::ControlId::track: return trackSlider;
        case AriaBridgeAudioProcessor::ControlId::slot: return slotSlider;
        case AriaBridgeAudioProcessor::ControlId::measures: return measuresSlider;
        default: break;
    }
    jassertfalse;
    return tempSlider;
}

juce::Label& AriaBridgeAudioProcessorEditor::getValueLabelForControl(AriaBridgeAudioProcessor::ControlId controlId)
{
    switch (controlId)
    {
        case AriaBridgeAudioProcessor::ControlId::temp: return tempValueLabel;
        case AriaBridgeAudioProcessor::ControlId::topP: return topPValueLabel;
        case AriaBridgeAudioProcessor::ControlId::minP: return minPValueLabel;
        case AriaBridgeAudioProcessor::ControlId::tokens: return tokensValueLabel;
        case AriaBridgeAudioProcessor::ControlId::grade: return gradeValueLabel;
        case AriaBridgeAudioProcessor::ControlId::track: return trackValueLabel;
        case AriaBridgeAudioProcessor::ControlId::slot: return slotValueLabel;
        case AriaBridgeAudioProcessor::ControlId::measures: return measuresValueLabel;
        default: break;
    }
    jassertfalse;
    return tempValueLabel;
}

void AriaBridgeAudioProcessorEditor::applyMidiButtonTrigger(AriaBridgeAudioProcessor::ControlId buttonId)
{
    getButtonForControl(buttonId).triggerClick();
}

void AriaBridgeAudioProcessorEditor::showButtonContextMenu(AriaBridgeAudioProcessor::ControlId buttonId)
{
    juce::PopupMenu menu;
    menu.addItem(1, "MIDI Learn");
    const auto mappedMidi = audioProcessor.getMappedButtonMidi(buttonId);
    if (mappedMidi >= 0)
    {
        juce::String clearLabel = mappedMidi >= 128
            ? "Clear MIDI mapping (Note " + juce::String(mappedMidi - 128) + ")"
            : "Clear MIDI mapping (CC " + juce::String(mappedMidi) + ")";
        menu.addItem(2, clearLabel);
    }

    menu.showMenuAsync(juce::PopupMenu::Options(),
                       [this, buttonId] (int result)
                       {
                           if (result == 1) audioProcessor.beginMidiLearn(buttonId);
                           else if (result == 2) audioProcessor.clearMidiMapping(buttonId);
                       });
}

MidiLearnButton& AriaBridgeAudioProcessorEditor::getButtonForControl(AriaBridgeAudioProcessor::ControlId buttonId)
{
    switch (buttonId)
    {
        case AriaBridgeAudioProcessor::ControlId::record: return recordButton;
        case AriaBridgeAudioProcessor::ControlId::play:   return playButton;
        case AriaBridgeAudioProcessor::ControlId::cancel: return cancelButton;
        case AriaBridgeAudioProcessor::ControlId::sync:   return syncButton;
        case AriaBridgeAudioProcessor::ControlId::commit: return commitButton;
        case AriaBridgeAudioProcessor::ControlId::clip: return clipButton;
        case AriaBridgeAudioProcessor::ControlId::loop: return loopButton;
        case AriaBridgeAudioProcessor::ControlId::autoFire: return autoButton;
        default: break;
    }
    jassertfalse;
    return recordButton;
}

void AriaBridgeAudioProcessorEditor::configureStandaloneWindowIfNeeded()
{
    if (standaloneWindowConfigured)
        return;

    if (auto* window = dynamic_cast<juce::ResizableWindow*>(getTopLevelComponent()))
    {
        window->setResizable(true, true);
        window->setResizeLimits(560, 190, 1500, 500);
        window->setConstrainer(&windowConstrainer);
        standaloneWindowConfigured = true;
    }
}

void AriaBridgeAudioProcessorEditor::applyProgressState(const AriaBridgeAudioProcessor::ProgressStateSnapshot& state)
{
    isGenerating = state.generating;
    isPlaying = state.playing;
    genProgress = state.generation;
    playbackTarget = state.playback;
    playbackTotalDuration = state.duration;
    if (progressIteration != state.iteration)
    {
        progressIteration = state.iteration;
        playbackDisplayed = 0.0;
    }
    if (isPlaying)
    {
        const double remaining = playbackTotalDuration * (1.0 - playbackTarget);
        progressLabel.setText(formatClock(juce::roundToInt(remaining)) + " left", juce::dontSendNotification);
    }
    else if (isGenerating)
        progressLabel.setText("GEN " + juce::String(juce::roundToInt(genProgress * 100.0)) + "%", juce::dontSendNotification);
    else
        progressLabel.setText({}, juce::dontSendNotification);
}

void AriaBridgeAudioProcessorEditor::timerCallback()
{
    phase += 1.0;
    const double target = isPlaying ? playbackTarget : (isGenerating ? genProgress : 0.0);
    playbackDisplayed += (target - playbackDisplayed) * 0.25;
    progressLabel.setTooltip(progressLabel.getText());
    if (audioProcessor.getOSCStateSnapshot().connected != isConnected)
        refreshStatusDisplay();
    repaint();
}
