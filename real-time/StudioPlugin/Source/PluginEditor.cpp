#include "PluginEditor.h"

#include <cmath>

namespace
{
const juce::Colour ground     = juce::Colour::fromRGB(19, 23, 29);
const juce::Colour knobDark   = juce::Colour::fromRGB(35, 41, 49);
const juce::Colour buttonDark = juce::Colour::fromRGB(13, 17, 23);
const juce::Colour ink        = juce::Colour::fromRGB(224, 234, 242);
const juce::Colour muted      = juce::Colour::fromRGB(127, 146, 162);
const juce::Colour lineColour = juce::Colour::fromRGB(49, 63, 76);
const juce::Colour cyan       = juce::Colour::fromRGB(78, 201, 231);
const juce::Colour mint       = juce::Colour::fromRGB(91, 219, 177);
const juce::Colour amber      = juce::Colour::fromRGB(239, 185, 95);
const juce::Colour coral      = juce::Colour::fromRGB(240, 103, 116);
const juce::Colour magenta    = juce::Colour::fromRGB(162, 163, 244);
const juce::Colour green      = mint;
const juce::Colour progressColour = cyan;

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

juce::Font uiFont(float height, bool bold = false)
{
    return juce::Font(juce::FontOptions("Segoe UI", height, bold ? juce::Font::bold : juce::Font::plain));
}

void drawPanel(juce::Graphics& g, juce::Rectangle<int> bounds, float scale,
               const juce::String& number, const juce::String& title)
{
    const auto r = bounds.toFloat();
    g.setGradientFill(juce::ColourGradient(knobDark, r.getX(), r.getY(),
                                          ground.brighter(0.04f), r.getX(), r.getBottom(), false));
    g.fillRoundedRectangle(r, 5.0f * scale);
    g.setColour(lineColour);
    g.drawRoundedRectangle(r.reduced(0.5f), 5.0f * scale, 1.0f);
    g.setColour(cyan);
    g.setFont(uiFont(12.0f * scale, true));
    g.drawText(number, juce::Rectangle<float>(r.getX() + 16 * scale, r.getY() + 12 * scale, 22 * scale, 17 * scale),
               juce::Justification::centredLeft);
    g.setColour(ink);
    g.setFont(uiFont(14.0f * scale, true));
    g.drawText(title, juce::Rectangle<float>(r.getX() + 42 * scale, r.getY() + 12 * scale,
               r.getWidth() - 60 * scale, 17 * scale), juce::Justification::centredLeft);
    g.setColour(lineColour.withAlpha(0.7f));
    g.drawHorizontalLine(juce::roundToInt(r.getY() + 41 * scale), r.getX() + 1, r.getRight() - 1);
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
    const float stroke = juce::jmax(1.5f, radius * 0.055f);
    const float arcRadius = radius * 0.80f;
    const float angle = rotaryStartAngle + sliderPosProportional * (rotaryEndAngle - rotaryStartAngle);
    const auto accent = slider.findColour(juce::Slider::rotarySliderFillColourId);

    for (int tick = 0; tick <= 10; ++tick)
    {
        const float tickAngle = rotaryStartAngle + tick * (rotaryEndAngle - rotaryStartAngle) / 10.0f;
        g.setColour(tick / 10.0f <= sliderPosProportional ? accent.withAlpha(0.75f) : lineColour.brighter(0.2f));
        g.drawLine({ centre.getPointOnCircumference(radius * 0.91f, tickAngle),
                     centre.getPointOnCircumference(radius, tickAngle) }, juce::jmax(1.0f, radius * 0.028f));
    }
    juce::Path track;
    track.addCentredArc(centre.x, centre.y, arcRadius, arcRadius, 0.0f, rotaryStartAngle, rotaryEndAngle, true);
    g.setColour(buttonDark);
    g.strokePath(track, juce::PathStrokeType(stroke, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));

    if (sliderPosProportional > 0.0f)
    {
        juce::Path value;
        value.addCentredArc(centre.x, centre.y, arcRadius, arcRadius, 0.0f, rotaryStartAngle, angle, true);
        g.setColour(accent);
        g.strokePath(value, juce::PathStrokeType(stroke, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));
    }

    const auto face = juce::Rectangle<float>(radius * 1.32f, radius * 1.32f).withCentre(centre);
    g.setColour(juce::Colours::black.withAlpha(0.35f));
    g.fillEllipse(face.translated(0.0f, radius * 0.07f).expanded(radius * 0.03f));
    g.setGradientFill(juce::ColourGradient(juce::Colour::fromRGB(73, 85, 97), face.getX(), face.getY(),
                                          juce::Colour::fromRGB(24, 30, 38), face.getRight(), face.getBottom(), false));
    g.fillEllipse(face);
    g.setColour(juce::Colour::fromRGB(86, 102, 116));
    g.drawEllipse(face.reduced(0.5f), juce::jmax(1.0f, radius * 0.022f));
    g.setGradientFill(juce::ColourGradient(juce::Colour::fromRGB(53, 64, 77), centre.x, face.getY(),
                                          juce::Colour::fromRGB(30, 38, 48), centre.x, face.getBottom(), false));
    g.fillEllipse(face.reduced(radius * 0.09f));
    g.setColour(ink);
    g.drawLine({ centre.getPointOnCircumference(radius * 0.25f, angle),
                 centre.getPointOnCircumference(radius * 0.55f, angle) }, juce::jmax(2.0f, radius * 0.07f));
    const auto tip = centre.getPointOnCircumference(radius * 0.63f, angle);
    g.setColour(accent);
    g.fillEllipse(tip.x - radius * 0.04f, tip.y - radius * 0.04f, radius * 0.08f, radius * 0.08f);
    if (slider.isMouseOverOrDragging())
    {
        g.setColour(accent.withAlpha(0.35f));
        g.drawEllipse(face.expanded(1.0f), 1.0f);
    }
}

void AriaLookAndFeel::drawButtonBackground(juce::Graphics& g,
                                          juce::Button& button,
                                          const juce::Colour&,
                                          bool isMouseOverButton,
                                          bool isButtonDown)
{
    const bool on = button.getToggleState() || isButtonDown;
    const auto shape = button.getLocalBounds().toFloat().reduced(1.0f);
    const auto accent = button.findColour(juce::TextButton::buttonOnColourId);
    const bool primary = button.getName() == "record";
    const auto fill = on ? accent.withMultipliedBrightness(0.24f)
                         : (primary ? coral.withMultipliedBrightness(0.14f) : knobDark);
    g.setGradientFill(juce::ColourGradient(fill.brighter(isMouseOverButton ? 0.15f : 0.04f),
                                          shape.getX(), shape.getY(), fill.darker(0.15f),
                                          shape.getX(), shape.getBottom(), false));
    g.fillRoundedRectangle(shape, 3.0f);
    g.setColour(on || isMouseOverButton || primary ? accent.withAlpha(on ? 0.9f : 0.55f) : lineColour.brighter(0.1f));
    g.drawRoundedRectangle(shape, 3.0f, 1.0f);
    if (on)
    {
        g.setColour(accent);
        g.fillRoundedRectangle(shape.withTop(shape.getBottom() - 2.0f).reduced(5.0f, 0.0f), 1.0f);
    }
}

void AriaLookAndFeel::drawButtonText(juce::Graphics& g, juce::TextButton& button, bool, bool)
{
    const auto bounds = button.getLocalBounds().toFloat();
    const float s = bounds.getHeight() / 40.0f;
    const auto accent = button.findColour(juce::TextButton::buttonOnColourId);
    const bool on = button.getToggleState() || button.isDown();
    const float iconX = 13.0f * s;
    const float iconY = bounds.getCentreY();
    g.setColour(on ? accent : (button.getName() == "record" ? coral : muted));
    if (button.getName() == "play")
    {
        juce::Path triangle;
        triangle.addTriangle(iconX - 3 * s, iconY - 5 * s, iconX - 3 * s, iconY + 5 * s, iconX + 4 * s, iconY);
        g.fillPath(triangle);
    }
    else if (button.getName() == "cancel")
        g.fillRect(iconX - 3 * s, iconY - 3 * s, 6 * s, 6 * s);
    else
        g.fillEllipse(iconX - 3 * s, iconY - 3 * s, 6 * s, 6 * s);
    g.setColour(on ? accent : ink);
    g.setFont(uiFont(14.0f * s, true));
    g.drawFittedText(button.getButtonText().toUpperCase(), bounds.withLeft(25 * s).reduced(3 * s, 0).toNearestInt(),
                     juce::Justification::centred, 1);
}

AriaStudioAudioProcessorEditor::AriaStudioAudioProcessorEditor(AriaStudioAudioProcessor& processor)
    : AudioProcessorEditor(&processor), audioProcessor(processor)
{
    setLookAndFeel(&lookAndFeel);
    windowConstrainer.setMinimumSize(768, 400);
    windowConstrainer.setMaximumSize(1920, 1000);
    windowConstrainer.setFixedAspectRatio(1.92);

    using Control = AriaStudioAudioProcessor::ControlId;
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
    configureActionButton(commitButton, "commit", cyan);

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
        // The backend disables the shared progress slider while writing clips.
        playbackDisplayed = 0.0;
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
        setGenerationActive(false);
        stopPlayback();
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
    statusLabel.setColour(juce::Label::textColourId, muted);
    statusLabel.setJustificationType(juce::Justification::centredRight);
    addAndMakeVisible(statusLabel);
    logLabel.setVisible(false);
    progressLabel.setColour(juce::Label::textColourId, ink);
    progressLabel.setJustificationType(juce::Justification::centredRight);
    addAndMakeVisible(progressLabel);

    setResizable(true, false);
    setResizeLimits(768, 400, 1920, 1000);
    if (auto* editorConstrainer = getConstrainer())
        editorConstrainer->setFixedAspectRatio(1.92);
    setSize(960, 500);
    refreshControlValues();
    refreshStatusDisplay();
    audioProcessor.setEditor(this);
    configureStandaloneWindowIfNeeded();
    startTimerHz(30);
}

AriaStudioAudioProcessorEditor::~AriaStudioAudioProcessorEditor()
{
    stopTimer();
    setLookAndFeel(nullptr);
    audioProcessor.clearEditor(this);
}

void AriaStudioAudioProcessorEditor::paint(juce::Graphics& g)
{
    const float s = uiScale;
    g.fillAll(ground);
    const auto at = [this, s] (float x, float y, float w, float h)
    {
        return juce::Rectangle<float>(contentLeft + x * s, contentTop + y * s, w * s, h * s);
    };
    const auto text = [&g, s, &at] (const juce::String& value, float x, float y, float w, float h,
                                  float fontSize, juce::Colour colour, bool bold = false)
    {
        g.setColour(colour);
        g.setFont(uiFont(fontSize * s, bold));
        g.drawText(value, at(x, y, w, h), juce::Justification::centredLeft);
    };

    g.setColour(buttonDark);
    g.fillRect(at(0, 0, 960, 64));
    g.setColour(lineColour);
    g.fillRect(at(0, 63, 960, 1));
    g.setColour(cyan);
    // A digital pulse mark, drawn as vectors at every UI scale.
    for (int index = 0; index < 4; ++index)
    {
        const float heights[] { 10, 24, 17, 31 };
        g.fillRoundedRectangle(at(25.0f + index * 7.0f, 33.0f - heights[index] * 0.5f,
                                   3.0f, heights[index]), 1.0f * s);
    }
    text("ARIA", 64, 10, 94, 34, 26, ink, true);
    text("STUDIO", 154, 16, 90, 24, 17, cyan, true);
    text("MIDI CONTROL", 65, 41, 190, 14, 11, muted);
    g.setColour(lineColour);
    g.fillRect(at(267, 18, 1, 28));
    text("Capture. Generate. Route.", 287, 20, 340, 24, 15, muted);

    const auto connection = at(735, 17, 201, 30);
    g.setColour(ground);
    g.fillRoundedRectangle(connection, 4 * s);
    g.setColour(lineColour);
    g.drawRoundedRectangle(connection, 4 * s, 1.0f);
    const auto dotColour = isConnected ? stateColour(statusLabel.getText()) : muted;
    g.setColour(dotColour.withAlpha(0.12f));
    g.fillEllipse(at(746, 24, 14, 14));
    g.setColour(dotColour);
    g.fillEllipse(at(750, 28, 6, 6));

    drawPanel(g, generationRect, s, "01", "GENERATION");
    drawPanel(g, destinationRect, s, "02", "DESTINATION & FEEDBACK");
    drawPanel(g, transportRect, s, "03", "CAPTURE & TRANSPORT");
    drawPanel(g, modeRect, s, "04", "CLIP MODES");
    text("Sampling parameters", 41, 277, 240, 16, 11, muted);
    text(juce::String::fromUTF8("Zero-based track / slot    ·    0 measures = full clip"), 503, 277, 417, 16, 11, muted);

    // Recessed numeric displays underneath the eight controls.
    for (int index = 0; index < static_cast<int>(AriaStudioAudioProcessor::ControlId::record); ++index)
    {
        const auto control = static_cast<AriaStudioAudioProcessor::ControlId>(index);
        const auto valueBounds = getValueLabelForControl(control).getBounds().toFloat();
        g.setColour(buttonDark);
        g.fillRoundedRectangle(valueBounds, 3 * s);
        g.setColour(lineColour.withAlpha(0.8f));
        g.drawRoundedRectangle(valueBounds.reduced(0.5f), 3 * s, 1.0f);
    }

    g.setColour(buttonDark);
    g.fillRoundedRectangle(activityRect.toFloat(), 4 * s);
    g.setColour(lineColour);
    g.drawRoundedRectangle(activityRect.toFloat().reduced(0.5f), 4 * s, 1.0f);
    const bool clipMode = clipButton.getToggleState();
    const juce::String stage = clipMode ? "CLIP OUTPUT" : (isPlaying ? "PLAYBACK" : (isGenerating ? "GENERATING" : "ACTIVITY"));
    text(stage, 41, 442, 108, 16, 12, clipMode ? muted : cyan, true);
    const float progress = clipMode ? 0.0f : static_cast<float>(juce::jlimit(0.0, 1.0, playbackDisplayed));
    const auto bar = barRect.toFloat();
    constexpr int segments = 64;
    const float step = bar.getWidth() / segments;
    const int activeSegments = juce::roundToInt(progress * segments);
    for (int segment = 0; segment < segments; ++segment)
    {
        g.setColour(segment < activeSegments ? progressColour : lineColour.withAlpha(clipMode ? 0.3f : 0.65f));
        g.fillRect(bar.getX() + segment * step, bar.getY(), step - 2 * s, bar.getHeight());
    }
    if (progressLabel.getText().isEmpty() || clipMode)
        text(clipMode ? "CLIP MODE" : "IDLE", 802, 442, 110, 16, 12, muted);

    text("ARIA STUDIO  /  MIDI CONTROL PLUGIN", 25, 482, 380, 14, 10, muted);
    text(juce::String::fromUTF8("RIGHT-CLICK: MIDI LEARN    ·    DOUBLE-CLICK KNOB: RESET"), 563, 482, 373, 14, 10, muted);

    using Control = AriaStudioAudioProcessor::ControlId;
    for (int index = 0; index < static_cast<int>(Control::count); ++index)
    {
        const auto control = static_cast<Control>(index);
        if (! audioProcessor.isLearningControl(control))
            continue;
        g.setColour(amber);
        if (index < static_cast<int>(Control::record))
            g.drawEllipse(getSliderForControl(control).getBounds().toFloat().expanded(1.5f * s), 2.0f * s);
        else
            g.drawRoundedRectangle(getButtonForControl(control).getBounds().toFloat().expanded(2 * s), 4 * s, 1.5f * s);
    }
}

void AriaStudioAudioProcessorEditor::parentHierarchyChanged()
{
    AudioProcessorEditor::parentHierarchyChanged();
    configureStandaloneWindowIfNeeded();
}

void AriaStudioAudioProcessorEditor::layoutKnob(LearnableSlider& slider, juce::Label& nameLabel,
                                               juce::Label& valueLabel, juce::Rectangle<int> cell, int knobSize)
{
    const int nameH = juce::roundToInt(17 * uiScale);
    const int valueH = juce::roundToInt(26 * uiScale);
    nameLabel.setBounds(cell.withHeight(nameH));
    valueLabel.setBounds(cell.getCentreX() - juce::roundToInt(37 * uiScale), cell.getBottom() - valueH,
                         juce::roundToInt(74 * uiScale), valueH);
    const int size = juce::jmin(knobSize, cell.getHeight() - nameH - valueH - juce::roundToInt(10 * uiScale), cell.getWidth() - 4);
    slider.setBounds(cell.getCentreX() - size / 2, cell.getY() + nameH + juce::roundToInt(6 * uiScale), size, size);
}

void AriaStudioAudioProcessorEditor::resized()
{
    uiScale = juce::jmin(getWidth() / 960.0f, getHeight() / 500.0f);
    const float s = uiScale;
    contentLeft = (getWidth() - juce::roundToInt(960 * s)) / 2;
    contentTop = (getHeight() - juce::roundToInt(500 * s)) / 2;
    const auto bounds = [this, s] (int x, int y, int w, int h)
    {
        return juce::Rectangle<int>(contentLeft + juce::roundToInt(x * s), contentTop + juce::roundToInt(y * s),
                                     juce::roundToInt(w * s), juce::roundToInt(h * s));
    };
    headerRect = bounds(24, 0, 912, 64);
    generationRect = bounds(24, 82, 450, 218);
    destinationRect = bounds(486, 82, 450, 218);
    transportRect = bounds(24, 312, 552, 104);
    modeRect = bounds(588, 312, 348, 104);
    activityRect = bounds(24, 428, 912, 48);
    barRect = bounds(153, 448, 612, 8);
    statusLabel.setBounds(bounds(767, 20, 155, 24));
    statusLabel.setFont(uiFont(13 * s, true));
    progressLabel.setBounds(bounds(792, 438, 126, 28));
    progressLabel.setFont(juce::Font(juce::FontOptions("Consolas", 14 * s, juce::Font::plain)));
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
        auto& knob = knobs[(size_t) i];
        std::get<1>(knob)->setFont(uiFont(14 * s, true));
        std::get<2>(knob)->setFont(juce::Font(juce::FontOptions("Consolas", 18 * s, juce::Font::plain)));
        layoutKnob(*std::get<0>(knob), *std::get<1>(knob), *std::get<2>(knob),
                   bounds((i < 4 ? 39 : 501) + (i % 4) * 106, 141, 102, 126), juce::roundToInt(78 * s));
    }
    std::array<MidiLearnButton*, 5> transport {{ &recordButton, &playButton, &cancelButton, &syncButton, &commitButton }};
    for (int i = 0; i < 5; ++i)
        transport[static_cast<size_t>(i)]->setBounds(bounds(40 + i * 104, 360, 96, 40));
    std::array<MidiLearnButton*, 3> modes {{ &clipButton, &loopButton, &autoButton }};
    for (int i = 0; i < 3; ++i)
        modes[static_cast<size_t>(i)]->setBounds(bounds(604 + i * 106, 360, 98, 40));
}

void AriaStudioAudioProcessorEditor::refreshControlValues()
{
    using Control = AriaStudioAudioProcessor::ControlId;
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

void AriaStudioAudioProcessorEditor::refreshStatusDisplay()
{
    const auto snapshot = audioProcessor.getOSCStateSnapshot();
    isConnected = snapshot.connected;
    const juce::String shown = isConnected ? snapshot.currentStatus : juce::String("DISCONNECTED");
    statusLabel.setText(shown, juce::dontSendNotification);
    statusLabel.setTooltip(shown + "\n" + snapshot.lastLog);
    logLabel.setText(snapshot.lastLog, juce::dontSendNotification);
    repaint();
}

void AriaStudioAudioProcessorEditor::applyMappedControlValue(AriaStudioAudioProcessor::ControlId controlId, double value)
{
    getSliderForControl(controlId).setValue(value, juce::dontSendNotification);
    refreshValueLabel(controlId);
}

void AriaStudioAudioProcessorEditor::configureKnob(LearnableSlider& slider,
                                                   juce::Label& nameLabel,
                                                   juce::Label& valueLabel,
                                                   const juce::String& name,
                                                   double minimum, double maximum, double interval,
                                                   double defaultValue,
                                                   const juce::String& oscAddress,
                                                   AriaStudioAudioProcessor::ControlId controlId,
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

void AriaStudioAudioProcessorEditor::configureActionButton(MidiLearnButton& button, const juce::String& text, juce::Colour accent)
{
    button.setButtonText(text);
    button.setName(text);
    button.setColour(juce::TextButton::buttonColourId, accent);
    button.setColour(juce::TextButton::buttonOnColourId, accent);
    addAndMakeVisible(button);
}

void AriaStudioAudioProcessorEditor::configureNameLabel(juce::Label& label, const juce::String& text, juce::Colour accent)
{
    label.setText(text == "temp" ? "TEMPERATURE" : text.replace("_", " ").toUpperCase(), juce::dontSendNotification);
    label.setJustificationType(juce::Justification::centred);
    juce::ignoreUnused(accent);
    label.setColour(juce::Label::textColourId, muted);
    label.setBorderSize(juce::BorderSize<int>(0));
    label.setFont(juce::Font(juce::FontOptions(14.0f)));
    addAndMakeVisible(label);
}

void AriaStudioAudioProcessorEditor::configureValueLabel(juce::Label& label)
{
    label.setJustificationType(juce::Justification::centred);
    label.setColour(juce::Label::textColourId, cyan);
    label.setBorderSize(juce::BorderSize<int>(0));
    label.setFont(juce::Font(juce::FontOptions(14.0f)));
    addAndMakeVisible(label);
}

void AriaStudioAudioProcessorEditor::refreshValueLabel(AriaStudioAudioProcessor::ControlId controlId)
{
    auto& slider = getSliderForControl(controlId);
    auto& valueLabel = getValueLabelForControl(controlId);
    const auto isFloatControl = controlId == AriaStudioAudioProcessor::ControlId::temp
        || controlId == AriaStudioAudioProcessor::ControlId::topP
        || controlId == AriaStudioAudioProcessor::ControlId::minP;

    valueLabel.setText(isFloatControl ? formatFloatValue(slider.getValue()) : formatIntValue(slider.getValue()),
                       juce::dontSendNotification);
}

void AriaStudioAudioProcessorEditor::showSliderContextMenu(AriaStudioAudioProcessor::ControlId controlId)
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

LearnableSlider& AriaStudioAudioProcessorEditor::getSliderForControl(AriaStudioAudioProcessor::ControlId controlId)
{
    switch (controlId)
    {
        case AriaStudioAudioProcessor::ControlId::temp: return tempSlider;
        case AriaStudioAudioProcessor::ControlId::topP: return topPSlider;
        case AriaStudioAudioProcessor::ControlId::minP: return minPSlider;
        case AriaStudioAudioProcessor::ControlId::tokens: return tokensSlider;
        case AriaStudioAudioProcessor::ControlId::grade: return gradeSlider;
        case AriaStudioAudioProcessor::ControlId::track: return trackSlider;
        case AriaStudioAudioProcessor::ControlId::slot: return slotSlider;
        case AriaStudioAudioProcessor::ControlId::measures: return measuresSlider;
        default: break;
    }
    jassertfalse;
    return tempSlider;
}

juce::Label& AriaStudioAudioProcessorEditor::getValueLabelForControl(AriaStudioAudioProcessor::ControlId controlId)
{
    switch (controlId)
    {
        case AriaStudioAudioProcessor::ControlId::temp: return tempValueLabel;
        case AriaStudioAudioProcessor::ControlId::topP: return topPValueLabel;
        case AriaStudioAudioProcessor::ControlId::minP: return minPValueLabel;
        case AriaStudioAudioProcessor::ControlId::tokens: return tokensValueLabel;
        case AriaStudioAudioProcessor::ControlId::grade: return gradeValueLabel;
        case AriaStudioAudioProcessor::ControlId::track: return trackValueLabel;
        case AriaStudioAudioProcessor::ControlId::slot: return slotValueLabel;
        case AriaStudioAudioProcessor::ControlId::measures: return measuresValueLabel;
        default: break;
    }
    jassertfalse;
    return tempValueLabel;
}

void AriaStudioAudioProcessorEditor::applyMidiButtonTrigger(AriaStudioAudioProcessor::ControlId buttonId)
{
    getButtonForControl(buttonId).triggerClick();
}

void AriaStudioAudioProcessorEditor::showButtonContextMenu(AriaStudioAudioProcessor::ControlId buttonId)
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

MidiLearnButton& AriaStudioAudioProcessorEditor::getButtonForControl(AriaStudioAudioProcessor::ControlId buttonId)
{
    switch (buttonId)
    {
        case AriaStudioAudioProcessor::ControlId::record: return recordButton;
        case AriaStudioAudioProcessor::ControlId::play:   return playButton;
        case AriaStudioAudioProcessor::ControlId::cancel: return cancelButton;
        case AriaStudioAudioProcessor::ControlId::sync:   return syncButton;
        case AriaStudioAudioProcessor::ControlId::commit: return commitButton;
        case AriaStudioAudioProcessor::ControlId::clip: return clipButton;
        case AriaStudioAudioProcessor::ControlId::loop: return loopButton;
        case AriaStudioAudioProcessor::ControlId::autoFire: return autoButton;
        default: break;
    }
    jassertfalse;
    return recordButton;
}

void AriaStudioAudioProcessorEditor::configureStandaloneWindowIfNeeded()
{
    if (standaloneWindowConfigured)
        return;

    if (auto* window = dynamic_cast<juce::ResizableWindow*>(getTopLevelComponent()))
    {
        window->setResizable(true, true);
        window->setResizeLimits(768, 400, 1920, 1000);
        window->setConstrainer(&windowConstrainer);
        standaloneWindowConfigured = true;
    }
}

void AriaStudioAudioProcessorEditor::setGenerationActive(bool active)
{
    isGenerating = active;
    if (active)
    {
        genProgress = 0.0;
        if (! isPlaying)
        {
            playbackDisplayed = 0.0;
            progressLabel.setText("GEN 0%", juce::dontSendNotification);
        }
    }
    else if (! isPlaying)
    {
        progressLabel.setText({}, juce::dontSendNotification);
    }
}

void AriaStudioAudioProcessorEditor::setGenerationProgress(float value)
{
    if (value <= 0.0f && ! isGenerating && ! isPlaying)
    {
        genProgress = 0.0;
        return;
    }
    // New M4L backends send playback through /generation_progress as well.
    if (isPlaying)
        setPlaybackProgress(value);
    else
    {
        isGenerating = true;
        genProgress = juce::jlimit(0.0, 1.0, static_cast<double>(value));
        progressLabel.setText("GEN " + juce::String(juce::roundToInt(genProgress * 100.0)) + "%", juce::dontSendNotification);
    }
}

void AriaStudioAudioProcessorEditor::timerCallback()
{
    phase += 1.0;

    // One eased bar value, target driven by whichever phase is active.
    const double target = clipButton.getToggleState() ? 0.0
        : (isPlaying ? playbackTarget : (isGenerating ? genProgress : 0.0));
    playbackDisplayed += (target - playbackDisplayed) * 0.25;
    progressLabel.setVisible(! clipButton.getToggleState());
    progressLabel.setTooltip(clipButton.getToggleState() ? "Progress disabled in clip mode"
                                                       : progressLabel.getText());

    // Re-evaluate connection so it clears when the backend goes quiet.
    if (audioProcessor.getOSCStateSnapshot().connected != isConnected)
        refreshStatusDisplay();

    repaint();
}

void AriaStudioAudioProcessorEditor::setPlaybackDuration(float seconds)
{
    playbackTotalDuration = static_cast<double>(seconds);
    isPlaying = true;
    playbackTarget = 0.0;
}

void AriaStudioAudioProcessorEditor::setPlaybackProgress(float value)
{
    isPlaying = true;
    isGenerating = false;
    playbackTarget = juce::jlimit(0.0, 1.0, static_cast<double>(value));
    const double remaining = playbackTotalDuration * (1.0 - playbackTarget);
    progressLabel.setText(formatClock(juce::roundToInt(remaining)) + " left", juce::dontSendNotification);
}

void AriaStudioAudioProcessorEditor::stopPlayback()
{
    isPlaying = false;
    playbackTarget = 0.0;
    if (! isGenerating)
        progressLabel.setText({}, juce::dontSendNotification);
}
