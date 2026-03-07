#include "PluginEditor.h"

namespace
{
const juce::Colour backgroundColour = juce::Colour::fromRGB(240, 238, 235);
const juce::Colour panelColour = juce::Colour::fromRGB(235, 233, 230);
const juce::Colour textColour = juce::Colour::fromRGB(62, 62, 62);
const juce::Colour mutedTextColour = juce::Colour::fromRGB(122, 122, 122);
const juce::Colour accentColour = juce::Colour::fromRGB(130, 100, 200);
const juce::Colour buttonColour = juce::Colour::fromRGB(224, 222, 218);
const juce::Colour recordActiveColour = juce::Colour::fromRGB(0xc6, 0x28, 0x28);
const juce::Colour disconnectedColour = juce::Colour::fromRGB(0x6a, 0x6a, 0x6a);
const juce::Colour learningColour = juce::Colour::fromRGB(0xff, 0xd5, 0x4f);

constexpr int statusBarHeight = 40;

juce::String formatFloatValue(double value)
{
    return juce::String(value, 2);
}

juce::String formatIntValue(double value)
{
    return juce::String(juce::roundToInt(value));
}
}

void AriaLookAndFeel::drawRotarySlider(juce::Graphics& g,
                                       int x,
                                       int y,
                                       int width,
                                       int height,
                                       float sliderPosProportional,
                                       float rotaryStartAngle,
                                       float rotaryEndAngle,
                                       juce::Slider&)
{
    const auto bounds = juce::Rectangle<float>(static_cast<float>(x), static_cast<float>(y),
                                               static_cast<float>(width), static_cast<float>(height));
    const auto centre = bounds.getCentre();
    const auto radius = juce::jmin(bounds.getWidth(), bounds.getHeight()) * 0.32f;
    const auto tickRadius = radius + 8.0f;
    const auto arcRadius = tickRadius + 8.0f;
    const auto angle = rotaryStartAngle + (sliderPosProportional * (rotaryEndAngle - rotaryStartAngle));
    const auto knobBounds = juce::Rectangle<float>(centre.x - radius, centre.y - radius, radius * 2.0f, radius * 2.0f);

    juce::ColourGradient knobGradient(juce::Colour::fromRGB(240, 240, 240), centre.x - radius * 0.35f, centre.y - radius * 0.45f,
                                      juce::Colour::fromRGB(180, 180, 180), centre.x + radius, centre.y + radius, true);
    knobGradient.addColour(0.65, juce::Colour::fromRGB(214, 214, 214));
    g.setGradientFill(knobGradient);
    g.fillEllipse(knobBounds);

    g.setColour(juce::Colour::fromRGB(108, 108, 108));
    g.drawEllipse(knobBounds, 1.0f);

    for (int i = 0; i < 24; ++i)
    {
        const auto t = static_cast<float>(i) / 23.0f;
        const auto tickAngle = rotaryStartAngle + t * (rotaryEndAngle - rotaryStartAngle);
        const auto tickPoint = centre.getPointOnCircumference(tickRadius, tickAngle);
        g.setColour(juce::Colour::fromRGB(200, 200, 200));
        g.fillEllipse(tickPoint.x - 1.5f, tickPoint.y - 1.5f, 3.0f, 3.0f);
    }

    juce::Path remainderArc;
    remainderArc.addCentredArc(centre.x, centre.y, arcRadius, arcRadius, 0.0f, rotaryStartAngle, rotaryEndAngle, true);
    g.setColour(accentColour.withAlpha(0.2f));
    g.strokePath(remainderArc, juce::PathStrokeType(3.0f, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));

    juce::Path valueArc;
    valueArc.addCentredArc(centre.x, centre.y, arcRadius, arcRadius, 0.0f, rotaryStartAngle, angle, true);
    g.setColour(accentColour);
    g.strokePath(valueArc, juce::PathStrokeType(3.0f, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));

    const auto indicatorStart = centre.getPointOnCircumference(radius * 0.15f, angle);
    const auto indicatorEnd = centre.getPointOnCircumference(radius * 0.82f, angle);
    g.setColour(juce::Colour::fromRGB(60, 60, 60));
    g.drawLine({ indicatorStart, indicatorEnd }, 2.0f);
}

void AriaLookAndFeel::drawButtonBackground(juce::Graphics& g,
                                           juce::Button& button,
                                           const juce::Colour& backgroundColourToUse,
                                           bool isMouseOverButton,
                                           bool isButtonDown)
{
    auto bounds = button.getLocalBounds().toFloat().reduced(0.5f);
    auto fill = button.getToggleState() ? accentColour : backgroundColourToUse;

    if (isButtonDown)
        fill = fill.darker(0.12f);
    else if (isMouseOverButton)
        fill = fill.brighter(0.06f);

    g.setColour(fill);
    g.fillRoundedRectangle(bounds, 8.0f);

    g.setColour(juce::Colours::white.withAlpha(button.getToggleState() ? 0.18f : 0.45f));
    g.drawLine(bounds.getX() + 2.0f, bounds.getY() + 1.5f, bounds.getRight() - 2.0f, bounds.getY() + 1.5f, 1.0f);

    g.setColour(juce::Colours::black.withAlpha(button.getToggleState() ? 0.18f : 0.22f));
    g.drawLine(bounds.getX() + 2.0f, bounds.getBottom() - 1.5f, bounds.getRight() - 2.0f, bounds.getBottom() - 1.5f, 1.0f);

    g.setColour(juce::Colour::fromRGB(152, 152, 152));
    g.drawRoundedRectangle(bounds, 8.0f, 1.0f);
}

void AriaLookAndFeel::drawButtonText(juce::Graphics& g,
                                     juce::TextButton& button,
                                     bool,
                                     bool)
{
    g.setColour(button.getToggleState() ? juce::Colours::white : juce::Colour::fromRGB(70, 70, 70));
    g.setFont(juce::Font(13.0f, juce::Font::bold));
    g.drawFittedText(button.getButtonText().toUpperCase(), button.getLocalBounds(), juce::Justification::centred, 1);
}

AriaBridgeAudioProcessorEditor::AriaBridgeAudioProcessorEditor(AriaBridgeAudioProcessor& processor)
    : AudioProcessorEditor(&processor),
      audioProcessor(processor)
{
    setLookAndFeel(&lookAndFeel);
    windowConstrainer.setMinimumSize(500, 250);
    windowConstrainer.setMaximumSize(1400, 700);

    configureFloatKnob(tempSlider, tempLabel, tempValueLabel, "temp", 0.1, 2.0, 0.9, "/aria/temp",
                       AriaBridgeAudioProcessor::ControlId::temp);
    configureFloatKnob(topPSlider, topPLabel, topPValueLabel, "top_p", 0.1, 1.0, 0.95, "/aria/top_p",
                       AriaBridgeAudioProcessor::ControlId::topP);
    configureFloatKnob(minPSlider, minPLabel, minPValueLabel, "min_p", 0.0, 0.3, 0.0, "/aria/min_p",
                       AriaBridgeAudioProcessor::ControlId::minP);
    configureIntKnob(tokensSlider, tokensLabel, tokensValueLabel, "tokens", 0, 2048, 512, "/aria/tokens",
                     AriaBridgeAudioProcessor::ControlId::tokens);

    configureIntKnob(coherenceSlider, coherenceLabel, coherenceValueLabel, "coherence", 1, 5, 3, "/aria/coherence",
                     AriaBridgeAudioProcessor::ControlId::coherence);
    configureIntKnob(tasteSlider, tasteLabel, tasteValueLabel, "taste", 1, 5, 3, "/aria/taste",
                     AriaBridgeAudioProcessor::ControlId::taste);
    configureIntKnob(repetitionSlider, repetitionLabel, repetitionValueLabel, "repetition", 1, 5, 3,
                     "/aria/repetition", AriaBridgeAudioProcessor::ControlId::repetition);
    configureIntKnob(continuitySlider, continuityLabel, continuityValueLabel, "continuity", 1, 5, 3,
                     "/aria/continuity", AriaBridgeAudioProcessor::ControlId::continuity);
    configureIntKnob(gradeSlider, gradeLabel, gradeValueLabel, "grade", 1, 5, 3, "/aria/grade",
                     AriaBridgeAudioProcessor::ControlId::grade);

    configureActionButton(recordButton, "record");
    configureActionButton(syncButton, "sync");
    configureActionButton(commitButton, "commit");
    configureActionButton(playButton, "play");
    configureActionButton(cancelButton, "cancel");

    recordButton.setClickingTogglesState(true);
    recordButton.onClick = [this]
    {
        isRecordEnabled = recordButton.getToggleState();
        audioProcessor.sendOSC("/aria/record", isRecordEnabled ? 1 : 0);
        updateRecordButtonAppearance();
    };

    syncButton.onClick = [this]
    {
        audioProcessor.sendOSC("/aria/sync");
        audioProcessor.sendOSC("/aria/temp", static_cast<float>(tempSlider.getValue()));
        audioProcessor.sendOSC("/aria/top_p", static_cast<float>(topPSlider.getValue()));
        audioProcessor.sendOSC("/aria/min_p", static_cast<float>(minPSlider.getValue()));
        audioProcessor.sendOSC("/aria/tokens", juce::roundToInt(tokensSlider.getValue()));
    };

    commitButton.onClick = [this] { audioProcessor.sendOSC("/aria/commit"); };
    playButton.onClick = [this] { audioProcessor.sendOSC("/aria/play"); };
    cancelButton.onClick = [this] { audioProcessor.sendOSC("/aria/cancel"); };

    statusLabel.setJustificationType(juce::Justification::centredLeft);
    statusLabel.setColour(juce::Label::textColourId, textColour);
    statusLabel.setFont(juce::Font(15.0f, juce::Font::bold));
    addAndMakeVisible(statusLabel);

    logLabel.setJustificationType(juce::Justification::centredLeft);
    logLabel.setColour(juce::Label::textColourId, mutedTextColour);
    logLabel.setFont(juce::Font(12.5f));
    addAndMakeVisible(logLabel);

    setResizable(true, false);
    setResizeLimits(500, 250, 1400, 700);
    setSize(700, 320);
    updateRecordButtonAppearance();
    refreshStatusDisplay();
    audioProcessor.setEditor(this);
    configureStandaloneWindowIfNeeded();
}

AriaBridgeAudioProcessorEditor::~AriaBridgeAudioProcessorEditor()
{
    setLookAndFeel(nullptr);
    audioProcessor.clearEditor(this);
}

void AriaBridgeAudioProcessorEditor::paint(juce::Graphics& g)
{
    auto bounds = getLocalBounds();
    auto statusBar = bounds.removeFromBottom(54);

    g.fillAll(backgroundColour);
    const auto cardBounds = bounds.reduced(10).toFloat();
    const auto statusBounds = statusBar.reduced(10, 6).toFloat();
    juce::DropShadow(juce::Colours::black.withAlpha(0.16f), 16, { 0, 4 }).drawForRectangle(g, cardBounds.getSmallestIntegerContainer());

    g.setColour(panelColour);
    g.fillRoundedRectangle(cardBounds, 12.0f);
    g.fillRoundedRectangle(statusBounds, 10.0f);

    g.setColour(juce::Colours::white.withAlpha(0.55f));
    g.drawRoundedRectangle(cardBounds.reduced(0.5f), 12.0f, 1.0f);
    g.setColour(juce::Colours::black.withAlpha(0.10f));
    g.drawRoundedRectangle(statusBounds.reduced(0.5f), 10.0f, 1.0f);

    const auto dotBounds = juce::Rectangle<float>(24.0f, static_cast<float>(getHeight() - 35), 12.0f, 12.0f);
    g.setColour(isConnected ? juce::Colours::green : disconnectedColour);
    g.fillEllipse(dotBounds);

    for (int index = 0; index < static_cast<int>(AriaBridgeAudioProcessor::ControlId::count); ++index)
    {
        const auto controlId = static_cast<AriaBridgeAudioProcessor::ControlId>(index);

        if (! audioProcessor.isLearningControl(controlId))
            continue;

        auto ringBounds = getSliderForControl(controlId).getBounds().toFloat().expanded(4.0f);
        g.setColour(learningColour);
        g.drawEllipse(ringBounds, 3.0f);
    }
}

void AriaBridgeAudioProcessorEditor::parentHierarchyChanged()
{
    AudioProcessorEditor::parentHierarchyChanged();
    configureStandaloneWindowIfNeeded();
}

void AriaBridgeAudioProcessorEditor::resized()
{
    auto bounds = getLocalBounds().reduced(12);
    auto statusBar = bounds.removeFromBottom(statusBarHeight);
    bounds.removeFromBottom(6);

    auto buttonPanel = bounds.removeFromRight(juce::roundToInt(bounds.getWidth() * 0.25f));
    auto knobPanel = bounds;

    auto firstRow = knobPanel.removeFromTop(knobPanel.getHeight() / 2);
    auto secondRow = knobPanel;

    const auto layoutKnobRow = [] (juce::Rectangle<int> rowBounds,
                                   std::initializer_list<std::tuple<LearnableSlider*, juce::Label*, juce::Label*>> controls)
    {
        const auto count = static_cast<int>(controls.size());
        const int cellWidth = rowBounds.getWidth() / juce::jmax(1, count);
        const int knobAreaHeight = juce::roundToInt(rowBounds.getHeight() * 0.72f);
        const int valueFontSize = juce::jmax(10, juce::roundToInt(juce::jmin(rowBounds.getHeight() * 0.6f, cellWidth * 0.7f) * 0.18f));
        const int nameLabelHeight = juce::jmax(valueFontSize + 2, juce::roundToInt(rowBounds.getHeight() * 0.12f));
        const int valueLabelHeight = juce::jmax(valueFontSize + 2, juce::roundToInt(rowBounds.getHeight() * 0.12f));
        int index = 0;

        for (auto control : controls)
        {
            auto cell = rowBounds.withTrimmedLeft(index * cellWidth);
            cell.setWidth(index == count - 1 ? rowBounds.getRight() - cell.getX() : cellWidth);
            const int knobSize = juce::roundToInt(juce::jmin(knobAreaHeight * 0.6f, cell.getWidth() * 0.7f));
            const int sliderY = cell.getY() + juce::jmax(0, (knobAreaHeight - knobSize) / 2);

            auto sliderBounds = juce::Rectangle<int>(cell.getX() + (cell.getWidth() - knobSize) / 2,
                                                     sliderY,
                                                     knobSize,
                                                     knobSize);
            auto nameBounds = juce::Rectangle<int>(cell.getX(),
                                                   sliderBounds.getBottom() + 4,
                                                   cell.getWidth(),
                                                   nameLabelHeight);
            auto valueBounds = juce::Rectangle<int>(cell.getX(),
                                                    nameBounds.getBottom() + 1,
                                                    cell.getWidth(),
                                                    valueLabelHeight);

            std::get<0>(control)->setBounds(sliderBounds);
            std::get<1>(control)->setBounds(nameBounds);
            std::get<2>(control)->setBounds(valueBounds);
            std::get<1>(control)->setFont(juce::Font(static_cast<float>(valueFontSize)));
            std::get<2>(control)->setFont(juce::Font(static_cast<float>(valueFontSize), juce::Font::bold));
            ++index;
        }
    };

    layoutKnobRow(firstRow, {
        { &tempSlider, &tempLabel, &tempValueLabel },
        { &topPSlider, &topPLabel, &topPValueLabel },
        { &minPSlider, &minPLabel, &minPValueLabel },
        { &tokensSlider, &tokensLabel, &tokensValueLabel }
    });

    layoutKnobRow(secondRow, {
        { &coherenceSlider, &coherenceLabel, &coherenceValueLabel },
        { &tasteSlider, &tasteLabel, &tasteValueLabel },
        { &repetitionSlider, &repetitionLabel, &repetitionValueLabel },
        { &continuitySlider, &continuityLabel, &continuityValueLabel },
        { &gradeSlider, &gradeLabel, &gradeValueLabel }
    });

    auto buttonsArea = buttonPanel.reduced(10, 4);
    std::array<juce::TextButton*, 5> buttons { &recordButton, &syncButton, &commitButton, &playButton, &cancelButton };
    const int buttonSlotHeight = buttonsArea.getHeight() / static_cast<int>(buttons.size());

    for (int i = 0; i < static_cast<int>(buttons.size()); ++i)
    {
        auto slot = buttonsArea.removeFromTop(i == static_cast<int>(buttons.size()) - 1 ? buttonsArea.getHeight() : buttonSlotHeight);
        const int buttonHeight = juce::jmax(30, juce::roundToInt(slot.getHeight() * 0.58f));
        const int buttonY = slot.getY() + juce::jmax(0, (slot.getHeight() - buttonHeight) / 2);
        buttons[static_cast<size_t>(i)]->setBounds(slot.withY(buttonY).withHeight(buttonHeight).reduced(4, 0));
    }

    auto statusContent = statusBar.reduced(14, 6);
    statusContent.removeFromLeft(26);
    statusLabel.setFont(juce::Font(static_cast<float>(juce::jmax(11, juce::roundToInt(getHeight() * 0.04f))), juce::Font::bold));
    logLabel.setFont(juce::Font(static_cast<float>(juce::jmax(10, juce::roundToInt(getHeight() * 0.032f)))));
    statusLabel.setBounds(statusContent.removeFromTop(statusContent.getHeight() / 2));
    statusContent.removeFromTop(3);
    logLabel.setBounds(statusContent);
}

void AriaBridgeAudioProcessorEditor::refreshStatusDisplay()
{
    const auto snapshot = audioProcessor.getOSCStateSnapshot();
    statusLabel.setText(snapshot.currentStatus, juce::dontSendNotification);
    logLabel.setText(snapshot.lastLog, juce::dontSendNotification);
    isConnected = snapshot.currentStatus != "DISCONNECTED";
    repaint();
}

void AriaBridgeAudioProcessorEditor::applyMappedControlValue(AriaBridgeAudioProcessor::ControlId controlId, double value)
{
    getSliderForControl(controlId).setValue(value, juce::sendNotificationSync);
}

void AriaBridgeAudioProcessorEditor::configureFloatKnob(LearnableSlider& slider,
                                                        juce::Label& nameLabel,
                                                        juce::Label& valueLabel,
                                                        const juce::String& name,
                                                        double minimum,
                                                        double maximum,
                                                        double defaultValue,
                                                        const juce::String& oscAddress,
                                                        AriaBridgeAudioProcessor::ControlId controlId)
{
    slider.setSliderStyle(juce::Slider::RotaryVerticalDrag);
    slider.setTextBoxStyle(juce::Slider::NoTextBox, false, 0, 0);
    slider.setRange(minimum, maximum, 0.001);
    slider.setValue(defaultValue, juce::dontSendNotification);
    slider.onValueChange = [this, controlId]
    {
        refreshValueLabel(controlId);
    };
    slider.onDragEnd = [this, &slider, oscAddress]
    {
        audioProcessor.sendOSC(oscAddress, static_cast<float>(slider.getValue()));
    };
    slider.onContextMenuRequested = [this, controlId]
    {
        showSliderContextMenu(controlId);
    };
    addAndMakeVisible(slider);

    configureNameLabel(nameLabel, name);
    configureValueLabel(valueLabel);
    refreshValueLabel(controlId);
}

void AriaBridgeAudioProcessorEditor::configureIntKnob(LearnableSlider& slider,
                                                      juce::Label& nameLabel,
                                                      juce::Label& valueLabel,
                                                      const juce::String& name,
                                                      int minimum,
                                                      int maximum,
                                                      int defaultValue,
                                                      const juce::String& oscAddress,
                                                      AriaBridgeAudioProcessor::ControlId controlId)
{
    slider.setSliderStyle(juce::Slider::RotaryVerticalDrag);
    slider.setTextBoxStyle(juce::Slider::NoTextBox, false, 0, 0);
    slider.setRange(minimum, maximum, 1.0);
    slider.setValue(defaultValue, juce::dontSendNotification);
    slider.onValueChange = [this, controlId]
    {
        refreshValueLabel(controlId);
    };
    slider.onDragEnd = [this, &slider, oscAddress]
    {
        audioProcessor.sendOSC(oscAddress, static_cast<int>(slider.getValue()));
    };
    slider.onContextMenuRequested = [this, controlId]
    {
        showSliderContextMenu(controlId);
    };
    addAndMakeVisible(slider);

    configureNameLabel(nameLabel, name);
    configureValueLabel(valueLabel);
    refreshValueLabel(controlId);
}

void AriaBridgeAudioProcessorEditor::configureActionButton(juce::TextButton& button, const juce::String& text)
{
    button.setButtonText(text);
    button.setColour(juce::TextButton::buttonColourId, buttonColour);
    button.setColour(juce::TextButton::buttonOnColourId, accentColour);
    button.setColour(juce::TextButton::textColourOffId, textColour);
    button.setColour(juce::TextButton::textColourOnId, juce::Colours::white);
    addAndMakeVisible(button);
}

void AriaBridgeAudioProcessorEditor::configureNameLabel(juce::Label& label, const juce::String& text)
{
    label.setText(text, juce::dontSendNotification);
    label.setJustificationType(juce::Justification::centred);
    label.setColour(juce::Label::textColourId, textColour);
    label.setFont(juce::Font(13.0f));
    addAndMakeVisible(label);
}

void AriaBridgeAudioProcessorEditor::configureValueLabel(juce::Label& label)
{
    label.setJustificationType(juce::Justification::centred);
    label.setColour(juce::Label::textColourId, accentColour);
    label.setFont(juce::Font(13.0f, juce::Font::bold));
    addAndMakeVisible(label);
}

void AriaBridgeAudioProcessorEditor::updateRecordButtonAppearance()
{
    recordButton.setColour(juce::TextButton::buttonColourId, isRecordEnabled ? recordActiveColour : buttonColour);
    recordButton.setColour(juce::TextButton::buttonOnColourId, isRecordEnabled ? recordActiveColour : buttonColour);
    recordButton.repaint();
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
                           if (result == 1)
                               audioProcessor.beginMidiLearn(controlId);
                           else if (result == 2)
                               audioProcessor.clearMidiMapping(controlId);
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
        case AriaBridgeAudioProcessor::ControlId::coherence: return coherenceSlider;
        case AriaBridgeAudioProcessor::ControlId::taste: return tasteSlider;
        case AriaBridgeAudioProcessor::ControlId::repetition: return repetitionSlider;
        case AriaBridgeAudioProcessor::ControlId::continuity: return continuitySlider;
        case AriaBridgeAudioProcessor::ControlId::grade: return gradeSlider;
        case AriaBridgeAudioProcessor::ControlId::count: break;
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
        case AriaBridgeAudioProcessor::ControlId::coherence: return coherenceValueLabel;
        case AriaBridgeAudioProcessor::ControlId::taste: return tasteValueLabel;
        case AriaBridgeAudioProcessor::ControlId::repetition: return repetitionValueLabel;
        case AriaBridgeAudioProcessor::ControlId::continuity: return continuityValueLabel;
        case AriaBridgeAudioProcessor::ControlId::grade: return gradeValueLabel;
        case AriaBridgeAudioProcessor::ControlId::count: break;
    }

    jassertfalse;
    return tempValueLabel;
}

void AriaBridgeAudioProcessorEditor::configureStandaloneWindowIfNeeded()
{
    if (standaloneWindowConfigured)
        return;

    if (auto* window = dynamic_cast<juce::ResizableWindow*>(getTopLevelComponent()))
    {
        window->setResizable(true, true);
        window->setResizeLimits(500, 250, 1400, 700);
        window->setConstrainer(&windowConstrainer);
        standaloneWindowConfigured = true;
    }
}
